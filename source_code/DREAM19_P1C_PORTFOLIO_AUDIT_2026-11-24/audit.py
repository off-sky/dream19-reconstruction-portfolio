#!/usr/bin/env python3
"""Independent offline audit of frozen P0E + P1A + P1B FIX1. No routing/refit."""
import argparse, csv, decimal, gzip, hashlib, io, json, math, sys, traceback, zipfile
from collections import defaultdict
from pathlib import Path, PurePosixPath
from itertools import combinations
import numpy as np

SOURCES={
 'P0E_RESULTS_20261124T155705Z.zip':'835891c4457ab54288c3e3cdf7c2c174882fd903d6c3d64228e3b7fdf6574c18',
 'P1A_RESULTS_20261124T161614Z.zip':'f3a5ef6966ab5779b3180ef79954cd21997034538b35243357672f2f40a6fb4c',
 'P1B_FIX1_RESULTS_20261124T163244Z.zip':'e6d2878f6dc10e4f7697c1a9532dc63893ad0176bab0f05f5ce8fd5b4a532f23'}
CANDS=['N02','N03','B20','P378079','P347295']; SCEN=['OPEN','S14','A18_EXCEPT_S14','P378079','P347295','N02','N03','B20']; COMPS=[('inter','MAIN'),('local','MAIN'),('inter','U1')]; FIELDS=['lon_o','lat_o','lon_d','lat_d']; D=decimal.Decimal

def require(x,m):
    if not x: raise ValueError(m)
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for a in iter(lambda:f.read(1024*1024),b''): h.update(a)
    return h.hexdigest()
def write(p,rows):
    rows=list(rows); require(bool(rows),'empty output: '+str(p));
    with p.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
def key(r): return tuple(round(float(r[x]),7) for x in FIELDS)
class Source:
    def __init__(self,path,expected):
        require(sha(path)==expected,'archive SHA mismatch '+path.name)
        self.z=zipfile.ZipFile(path); require(self.z.testzip() is None,'CRC failure');self.path=path
        members=self.z.namelist();roots={PurePosixPath(x).parts[0] for x in members};require(len(roots)==1,'root ambiguity');self.root=roots.pop()+'/'
        require(len(members)==len(set(members)) and all(x.startswith(self.root) and '..' not in PurePosixPath(x).parts for x in members),'invalid member')
        self.manifest=self.j('SHA256SUMS.json');require(set(self.manifest)=={x[len(self.root):] for x in members}-{ 'SHA256SUMS.json'},'manifest names mismatch')
        for n,h in self.manifest.items():require(hashlib.sha256(self.b(n)).hexdigest()==h,'member hash mismatch '+n)
    def b(self,n):return self.z.read(self.root+n)
    def j(self,n):return json.loads(self.b(n))
    def rows(self,n):
        b=self.b(n)
        if n.endswith('.gz'):b=gzip.decompress(b)
        return csv.DictReader(io.StringIO(b.decode('utf-8-sig')))

def pairs(src,s):
    d={}
    for r in src.rows(s+'_PAIR_RESULTS.csv.gz'):
        k=key(r);require(k not in d,'duplicate route key');d[k]=(r['status'],float(r['seconds']) if r['seconds'] else math.nan)
    return d

def varsum(x,group,exact,n):
    sums=np.bincount(group,weights=x,minlength=len(n)); sq=np.bincount(group,weights=x*x,minlength=len(n)); active=(~exact)&(n>1)
    return float(np.maximum(0,(n[active]*sq[active]-sums[active]**2)/(n[active]-1)).sum())

def main(root,out):
    a,b,c=[Source(root/n,h) for n,h in SOURCES.items()]; audit={'source_sha256':SOURCES,'manifest_counts':[len(x.manifest) for x in (a,b,c)]}
    op=pairs(b,'OPEN'); cl=pairs(b,'CLOSED'); require(len(op)==143076 and set(op)==set(cl),'base OD keys'); ks=list(op);idx={k:i for i,k in enumerate(ks)}; require(all(v[0]=='OK' for v in op.values()),'OPEN status'); common=np.array([cl[k][0]=='OK' for k in ks]); savings={}
    for s in SCEN:
        d=pairs(b if s in ('OPEN','S14') else c,s);require(set(d)==set(op),'scenario keys '+s)
        good=np.array([d[k][0]=='OK' for k in ks]); o=np.array([op[k][1] for k in ks]); t=np.array([cl[k][1] for k in ks]); v=np.array([d[k][1] for k in ks]); require(np.all(np.isfinite(v[good])) and np.all(v[good]>0),'invalid times'); mask=common&good
        require(np.all(v[mask]<=t[mask]+1.000000001) and np.all(v[mask]>=o[mask]-1.000000001),'nesting '+s)
        require(np.array_equal(mask,common),'scenario common support shrank '+s)
        savings[s]=np.r_[np.where(mask,np.maximum(0,t-v)/60,0),0.]
    require(int(common.sum())==143010,'common keys'); audit.update(accepted_keys=len(op),common_keys=int(common.sum()),nesting_violations=0)
    del op,cl,d
    # Preserve all draws (including unpaired/unrouted draws as zero contribution to conditional estimand).
    data={cm:[] for cm in COMPS}; seen=set()
    for r in a.rows('ROUTE_LEDGER.csv'):
        cm=(r['component'],r['model']); require(cm in data,'unknown component'); draw=(r['draw'],cm);require(draw not in seen,'duplicate draw');seen.add(draw);w=float(r['weight']);require(w>=0 and math.isfinite(w),'bad weight');require((int(r['parent_o'])==int(r['parent_d']))==(cm[0]=='local'),'parent double count');k=len(ks)
        if r['route_status']=='OK' and w>0:
            k=idx[key(r)]
        data[cm].append((w,k,r['stratum'],r['sampling']=='EXACT'))
    arrays={}; sums={}; sem={}; masses={}
    for cm,rows in data.items():
        w=np.array([r[0] for r in rows]); ii=np.array([r[1] for r in rows]); mp={}; group=np.array([mp.setdefault(r[2],len(mp)) for r in rows]);n=np.bincount(group);exact=np.zeros(len(mp),dtype=bool)
        for g,r in zip(group,rows):exact[g]=r[3]
        arrays[cm]=(w,ii,group,exact,n); masses[cm]=float(w[np.r_[common,False][ii]].sum());
        for s,x in savings.items():
            z=w*x[ii];sums[s,cm]=float(z.sum());sem[s,cm]=math.sqrt(varsum(z,group,exact,n))
    old={(r['scenario_id'],(r['component'],r['model'])):r for r in c.rows('SCENARIO_COMPONENT_BENEFITS.csv')}; require(set(old)==set(sums),'component keys'); errors=[]; rows=[]
    for (s,cm),val in sums.items():
        r=old[s,cm]; err=val-float(r['common_finite_minutes_per_component_unit']);errors.append(abs(err)); require(abs(err)<1e-9,'component dot product');require(abs(sem[s,cm]-float(r['mc_se_minutes']))<1e-9,'component MC SE');require(abs(masses[cm]-float(r['paired_finite_mass']))<1e-9,'paired mass');rows.append(dict(scenario=s,component=cm[0],model=cm[1],minutes_per_component_unit=val,mc_se_minutes=sem[s,cm],common_mass=masses[cm],delta_reported=err))
    write(out/'INDEPENDENT_COMPONENTS.csv',rows);audit['max_component_abs_error']=max(errors)
    fits={};unique=set()
    for r in a.rows('RETROSPECTIVE_FIT.csv'):
        q=tuple(float(r[f]) for f in ('Q_inter','Q_local','Q_U1'))
        if q not in unique:fits[r['fold']]=q;unique.add(q)
    require(len(fits)==4,'scale scenarios');costs={r['bridge_id']:D(r['historical_cost_UAH']) for r in c.rows('HISTORICAL_COSTS_19.csv')};require(len(costs)==19,'cost count');total=sum(costs.values()); candidates={r['scenario_id']:r for r in c.rows('FROZEN_PORTFOLIO_CANDIDATES.csv')}; cost={}
    for s,r in candidates.items():
        sel=r['selected'].split('+');require(len(sel)==len(set(sel)),'duplicate bridge');cost[s]=sum(costs[i] for i in sel);require(cost[s]==D(r['historical_cost_UAH']) and cost[s]<=D(r['budget_UAH']),'budget gate')
    oldscale={(r['scenario_id'],r['scale_scenario']):r for r in c.rows('SCALE_SCENARIO_VEHICLE_HOURS.csv')}; vals={}; rs=[]; de=[]
    for f,(qi,ql,qu) in fits.items():
        for s in SCEN:
            v=(qi*sums[s,COMPS[0]]+ql*sums[s,COMPS[1]])/60;u=qu*sums[s,COMPS[2]]/60;vals[s,f]=v;err=v-float(oldscale[s,f]['MAIN_common_vehicle_hours_day']);de.append(abs(err));require(abs(err)<1e-6 and abs(u-float(oldscale[s,f]['U1_vehicle_hours_day']))<1e-6,'scale mismatch');rs.append(dict(scenario_id=s,scale=f,main_hours_day=v,u1_hours_day=u,delta_reported=err))
    write(out/'INDEPENDENT_SCALE.csv',rs);audit['max_scaled_abs_error']=max(de)
    oldreg={(r['scenario_id'],r['scale_scenario']):r for r in c.rows('CANDIDATE_REGRET.csv')}; rr=[]; maxreg={};
    for s in CANDS:
        for f in fits:
            best=max(vals[t,f] for t in CANDS);rel=(best-vals[s,f])/best;require(abs(rel-float(oldreg[s,f]['relative_regret']))<1e-10,'regret mismatch');rr.append(dict(scenario=s,scale=f,relative_regret=rel));
        maxreg[s]=max(r['relative_regret'] for r in rr if r['scenario']==s)
    write(out/'INDEPENDENT_REGRETS.csv',rr); rank=sorted(CANDS,key=lambda s:(maxreg[s],cost[s],s)); require(rank==[r['scenario_id'] for r in c.rows('CANDIDATE_MINIMAX_RANK.csv')],'rank mismatch')
    write(out/'INDEPENDENT_RANK.csv',[dict(rank=i+1,scenario=s,cost_UAH=str(cost[s]),bridges=len(candidates[s]['selected'].split('+')),budget_remaining_UAH=str(total*D('.2')-cost[s]),max_relative_regret=maxreg[s]) for i,s in enumerate(rank)])
    # Paired MC comparison using identical weights/strata. Not a confidence bound for model structure.
    olddiff={(r['scenario_a'],r['scenario_b'],r['scale_scenario']):r for r in c.rows('PAIRED_CANDIDATE_DIFFERENCES.csv')};rr=[];errors=[]
    for x,y in combinations(CANDS,2):
        dm={};vv={}
        for cm,(w,ii,g,e,n) in arrays.items():
            z=w*(savings[x][ii]-savings[y][ii]);dm[cm]=float(z.sum());vv[cm]=varsum(z,g,e,n)
        for f,(qi,ql,qu) in fits.items():
            d=(qi*dm[COMPS[0]]+ql*dm[COMPS[1]])/60;se=math.sqrt(qi**2*vv[COMPS[0]]+ql**2*vv[COMPS[1]])/60;du=qu*dm[COMPS[2]]/60;su=qu*math.sqrt(vv[COMPS[2]])/60; oldr=olddiff[x,y,f];errs=[abs(v-float(oldr[k])) for v,k in [(d,'MAIN_difference_hours_day'),(se,'MAIN_MC_SE_hours_day'),(du,'U1_difference_hours_day'),(su,'U1_MC_SE_hours_day')]];require(max(errs)<1e-6,'pair difference mismatch');errors.extend(errs);rr.append(dict(a=x,b=y,scale=f,delta_hours_day=d,mc_se=se,mc_normal_approx_low=d-1.96*se,mc_normal_approx_high=d+1.96*se,U1_delta=du,U1_mc_se=su))
    write(out/'INDEPENDENT_PAIRED_DIFFERENCES.csv',rr);audit['max_paired_abs_error']=max(errors)
    # Benefit share, cost efficiency and component-wise dominance. No new parameter fit.
    ef=[]
    for s in CANDS+['A18_EXCEPT_S14','S14','OPEN']:
        cc=cost[s] if s in cost else costs['S14'] if s=='S14' else total
        for f in fits:
            ef.append(dict(scenario=s,scale=f,cost_UAH=str(cc),cost_share=float(cc/total),benefit_share_of_OPEN=vals[s,f]/vals['OPEN',f],hours_day_per_billion_historical_cost=vals[s,f]/float(cc)*1e9))
    write(out/'COST_BENEFIT_EFFICIENCY.csv',ef)
    dominance=[]
    for x,y in combinations(CANDS,2):
        for i,j in [(x,y),(y,x)]:
            if all(sums[i,cm]>=sums[j,cm] for cm in COMPS[:2]) and any(sums[i,cm]>sums[j,cm] for cm in COMPS[:2]):dominance.append(dict(dominates=i,dominated=j,also_cost_no_higher=cost[i]<=cost[j]))
    write(out/'COMPONENT_DOMINANCE.csv',dominance)
    si=sums['B20',COMPS[0]]-sums['N03',COMPS[0]];sl=sums['B20',COMPS[1]]-sums['N03',COMPS[1]];threshold=si/(si-sl)
    def mixture_reg(f,s):
        bv={x:(1-f)*sums[x,COMPS[0]]+f*sums[x,COMPS[1]] for x in CANDS};return 1-bv[s]/max(bv.values())
    lo=min(q[1]/(q[0]+q[1]) for q in fits.values());hi=max(q[1]/(q[0]+q[1]) for q in fits.values());mix=[]
    for label,l,h in [('observed_scenario_mix_interval',lo,hi),('extreme_full_mix_diagnostic',0.,1.)]:
        mm={s:max(mixture_reg(l,s),mixture_reg(h,s)) for s in CANDS};rank2=sorted(CANDS,key=lambda s:mm[s]);
        for i,s in enumerate(rank2):mix.append(dict(interval=label,local_fraction_low=l,local_fraction_high=h,scenario=s,max_relative_regret=mm[s],rank=i+1))
    write(out/'MIXTURE_SENSITIVITY.csv',mix)
    # Check witnesses and historical fingerprint counts. Candidate identity not certified.
    wqa=[]
    for s,r in candidates.items():
        closed={int(x) for x in r['closed_way_ids'].split(';')};cw=[];ow=defaultdict(int)
        for v in c.rows('BRIDGE_WITNESSES_'+s+'.csv'):
            way=int(v['target_id'].split('_')[-1]);flag=int(v['both_candidate_terminal_nodes']);
            if way in closed:cw.append(flag)
            else:ow[v['target_id']]=max(ow[v['target_id']],flag)
        require(not any(cw),'closed candidate terminal witness');wqa.append(dict(scenario=s,closed_witness_rows=len(cw),closed_terminal_hits=sum(cw),open_candidates_without_terminal_witness=';'.join(k for k,v in ow.items() if v==0),historical_matrix_available=s in ['A18_EXCEPT_S14','P378079','P347295']))
    write(out/'WITNESS_AUDIT.csv',wqa)
    audit.update(status='OFFLINE_ARITHMETIC_PASS_CONDITIONAL',historical_total_UAH=str(total),rank=rank,local_fraction_B20_N03_crossover=threshold,incremental_cost_B20_vs_N03_UAH=str(cost['B20']-cost['N03']),external_validation=False,absolute_scale_validated=False,global_20pct_optimum=False,notes=['No graph execution or new demand fitting','Conditional finite support only; excluded OD not assumed zero','Extreme full mix is sensitivity, not evidence about actual share','MC intervals omit structural uncertainty and parameter estimation','Historical costs not current residual CAPEX'])
    (out/'AUDIT.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n',encoding='utf-8');print(json.dumps(audit,ensure_ascii=False,indent=2))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--bridges',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);args=ap.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    try:main(args.bridges,args.out)
    except Exception:
        (args.out/'ERROR.txt').write_text(traceback.format_exc());raise
