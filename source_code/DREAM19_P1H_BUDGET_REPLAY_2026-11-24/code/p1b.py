#!/usr/bin/env python3
"""Replay the frozen P0E OD ledger on prior Stage13e9D candidate portfolios.
No demand refit, no new candidate search. Fail closed on graph/provenance mismatch.
"""
from __future__ import annotations
import argparse, collections, csv, decimal, gzip, hashlib, io, json, math, os, random, re, shutil, subprocess, sys, traceback, zipfile
from pathlib import Path, PurePosixPath
import p1a as p
ROOT=p.ROOT
SOURCE='P1A_RESULTS_20261124T161614Z.zip'
SOURCE_SHA='f3a5ef6966ab5779b3180ef79954cd21997034538b35243357672f2f40a6fb4c'
PRIOR='DREAM19_STAGE13E9D_RESULTS_20261116T150808Z.zip'
PRIOR_SHA='eac3eb5ae95ce57237125fd55b666644ad7c01fb6bb65efbeb02c46f5f8cc24a'
PBF_SHA='dbaed9e6c7898a83fa062ac31ebb17786f252529540d27b9ebcc20e53b9486fa'
ORDER=('A18_EXCEPT_S14','P378079','P347295','N02','N03','B20')
CANDIDATES=('N02','N03','B20','P378079','P347295')
MATRIX_IDS=set(ORDER[:3]); D=decimal.Decimal
A18_PATH='stage13e1_S14_local_results/graph/ukraine_261111_S14_closed_duration.osrm'

def unpack(pth,d,expect):
    p.require(p.sha(pth)==expect,'P1A_RESULT_HASH_MISMATCH');d.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(pth) as z:
        p.require(z.testzip() is None,'P1A_CRC');roots=set()
        for i in z.infolist():
            q=PurePosixPath(i.filename);p.require(not i.is_dir() and not q.is_absolute() and '..' not in q.parts and len(q.parts)>=2,'UNSAFE_RESULT_MEMBER');roots.add(q.parts[0]);f=d.joinpath(*q.parts[1:]);f.parent.mkdir(parents=True,exist_ok=True);f.write_bytes(z.read(i))
        p.require(len(roots)==1,'MULTIPLE_RESULT_ROOTS')
    m=json.loads((d/'SHA256SUMS.json').read_text());p.require(set(m)=={f.relative_to(d).as_posix() for f in d.rglob('*') if f.is_file() and f.name!='SHA256SUMS.json'} and all(p.sha(d/k)==v for k,v in m.items()),'P1A_MEMBER_HASH')
    return len(m)

def gz_pairs(path,keys):
    rr={}
    with gzip.open(path,'rt',encoding='utf8',newline='') as f:
        for x in csv.DictReader(f):
            k=p.key(x);p.require(k not in rr,'DUPLICATE_PAIR_KEY');rr[k]=p.unflatten(x)
    p.require(set(rr)==set(keys),'PAIR_KEYS_MISMATCH');return rr

def candidates():
    registry=p.readcsv(ROOT/'inputs/bridge_registry_19.csv');cost={r['sample_id']:D(r['historical_finance_UAH']) for r in registry};p.require(set(cost)=={f'S{i:02}' for i in range(1,20)} and len(registry)==19 and all(v>0 for v in cost.values()),'BAD_COST_REGISTRY')
    cert=json.loads((ROOT/'inputs/stage13e9d_budget40_60_certificate.json').read_text());total=sum(cost.values());p.require(total==D(str(cert['total_historical_cost_UAH'])),'COST_TOTAL_MISMATCH')
    prior=p.readcsv(ROOT/'inputs/stage13e9d_R1_scorecard.csv');out={}
    for sid in CANDIDATES:
        g=[r for r in prior if r['scenario']==sid];p.require(len(g)==4 and len({r['selected'] for r in g})==1,'PRIOR_CANDIDATE_SCHEMA')
        sel=set(g[0]['selected'].split('+'));c=sum(cost[x] for x in sel);p.require(all(abs(c-D(r['historical_cost_UAH']))<D('.01') for r in g) and c<=total*D('.2'),'CANDIDATE_COST_MISMATCH')
        out[sid]=dict(selected=sel,cost=c,source='FROZEN_STAGE13E9D_SCORECARD',budget=D('.2')*total)
    sel=set(cert['selected'].split('+'));c=sum(cost[x] for x in sel);p.require(sel==set(cost)-{'S14'} and c==D(str(cert['historical_cost_UAH'])) and cost['S14']==D(str(cert['S14_historical_cost_UAH'])) and c<=total*D('.4') and cost['S14']>total*D('.6'),'A18_CERTIFICATE_COST_GATE')
    out['A18_EXCEPT_S14']=dict(selected=sel,cost=c,source='FROZEN_STAGE13E9D_BUDGET_CERTIFICATE',budget=total*D('.4'))
    # Stage13e9D used 20 opened ways for the 18 non-S14 bridges, hence one of the two S19 candidates.
    oldway=json.loads((ROOT/'inputs/PRIOR_A18_WAY_QA.json').read_text());p.require(oldway['status']=='PASS_BRIDGE_WAY_ROUTING' and oldway['opened_way_count']==20 and all(oldway['per_way_route_evidence'].values()),'PRIOR_WAY_QA')
    way={r['bridge_id']:set() for r in p.readcsv(ROOT/'inputs/13b_19_bridge_gates.csv')}
    for r in p.readcsv(ROOT/'inputs/13b_candidate_way_QA.csv'):
        wid=int(r['candidate_way_id']);bid=r['bridge_id']
        if bid=='S14' or str(wid) in oldway['per_way_route_evidence']:way[bid].add(wid)
    allways=set().union(*way.values());p.require(len(allways)==22 and all(way.values()) and len(way['S19'])==1 and len(way['S14'])==2,'PRIOR_WAY_MAPPING')
    return out,cost,total,way

def closure_for(sel,way):return set().union(*(v for k,v in way.items() if k not in sel))

def strip_lua_comments(s):
    # Drop whole-line comments only. Inline comments remain in the template comparison.
    return re.sub(r'(?m)^\s*--[^\n]*\n','',s)

def table_templates(text,expected):
    """Recognize an unambiguous literal Lua set/list or contiguous assignments.
    No rewriting arbitrary code, no guessing IDs from unrelated numeric literals.
    """
    out=[]
    # An entire flat literal table must consist of the expected ID set only.
    patterns=[('key_true',r'\[\s*(\d+)\s*\]\s*=\s*true'),('string_key_true',r'\[\s*[\"\'](\d+)[\"\']\s*\]\s*=\s*true'),('list',r'(\d+)'),('string_list',r'[\"\'](\d+)[\"\']')]
    for m in re.finditer(r'\{([^{}]{1,16000})\}',text,re.S):
        body=m.group(1);clean=re.sub(r'--[^\n]*','',body)
        for typ,pat in patterns:
            vals=re.findall(pat,clean);rest=re.sub(pat,'',clean)
            if len(vals)==len(expected) and set(map(int,vals))==expected and not re.sub(r'[\s,;]','',rest):
                out.append(dict(start=m.start(),end=m.end(),kind=typ,variable=None,text=text));break
    # Alternative: repeated assignments to one table (with no intervening executable code).
    pat=r'(?m)^[ \t]*([A-Za-z_]\w*)\s*\[\s*(\d+)\s*\]\s*=\s*true\s*;?[ \t]*(?:--[^\n]*)?\n?'
    matches=list(re.finditer(pat,text));by=collections.defaultdict(list)
    for m in matches:by[m.group(1)].append(m)
    for var,mm in by.items():
        if len(mm)!=len(expected) or {int(m.group(2)) for m in mm}!=expected:continue
        if any(re.sub(r'(?m)--[^\n]*','',text[a.end():b.start()]).strip() for a,b in zip(mm,mm[1:])):continue
        out.append(dict(start=mm[0].start(),end=mm[-1].end(),kind='assign',variable=var,text=text))
    return out

def canonical(t):
    # Preserve executable text, including string literals; only normalize whole-line comments/line endings.
    s=t['text'][:t['start']]+'__DREAM19_CLOSURE_SET__'+t['text'][t['end']:]
    return '\n'.join(x.rstrip() for x in strip_lua_comments(s).splitlines() if x.strip())

def render(t,ids):
    ids=sorted(ids);kind=t['kind'];items=[]
    for wid in ids:
        items.append({'key_true':f'[{wid}] = true','string_key_true':f'["{wid}"] = true','list':str(wid),'string_list':f'"{wid}"','assign':f"{t['variable']}[{wid}] = true"}[kind])
    body='\n'.join(items)+'\n' if kind=='assign' else '{'+', '.join(items)+'}'
    return t['text'][:t['start']]+body+t['text'][t['end']:]

def scan_profiles(root,closed_ids,s14_ids,out,explicit=None):
    # Existing profiles are read only. The directory of the original profile is retained for Lua imports.
    # Canonicalize both sides before containment, relative_to, and path ranking.
    # On macOS /var is a symlink to /private/var; TemporaryDirectory can use either.
    root=Path(root).resolve(strict=True)
    paths=[]
    if explicit:
        paths=[Path(x).resolve(strict=True) for x in explicit]
        p.require(all(q.is_relative_to(root) for q in paths),'PROFILE_OUTSIDE_BRIDGES')
    else:
        for current,dirs,files in os.walk(root):
            dirs[:]=[x for x in dirs if not x.startswith('.') and not x.startswith(('P1B_WORK_','P1B_FIX1_WORK_')) and x not in ('P1A_FROZEN_ROUTE_CACHE','P1B_ROUTE_CACHE','ghsl_data','_data','node_modules','__pycache__')]
            for f in files:
                if not f.lower().endswith('.lua'):continue
                try:q=(Path(current)/f).resolve(strict=True)
                except (OSError,RuntimeError):continue
                # Do not read automatically discovered links pointing outside bridges.
                if q.is_relative_to(root):paths.append(q)
    hits=[];closed=[];s14=[]
    for path in sorted(set(paths)):
        try:
            if path.stat().st_size>2_000_000:continue
            text=path.read_text(encoding='utf8');n=sum(str(x) in text for x in closed_ids)
            if n<max(2,len(s14_ids)-1):continue
            ct=table_templates(text,closed_ids);st=table_templates(text,s14_ids)
            hits.append(dict(path=str(path.relative_to(root)),sha256=p.sha(path),expected_way_mentions=n,closed_templates=len(ct),s14_templates=len(st)))
            q=out/'profile_candidates';q.mkdir(exist_ok=True);(q/(p.sha(path)[:16]+'.lua')).write_text(text,encoding='utf8')
            for t in ct:closed.append((path,t))
            for t in st:s14.append((path,t))
        except (UnicodeError,OSError):continue
    p.writecsv(out/'PROFILE_INVENTORY.csv',hits,fields=['path','sha256','expected_way_mentions','closed_templates','s14_templates'])
    # Strictly require equal executable template across CLOSED and S14. Prefer prior native dirs.
    pairs=[(a,b) for a in closed for b in s14 if canonical(a[1])==canonical(b[1]) and a[1]['kind']==b[1]['kind']]
    if not pairs:raise ValueError('NO_MATCHING_NATIVE_CLOSED_AND_S14_LUA_TEMPLATES; see PROFILE_INVENTORY and prior source capture')
    p.require(len({canonical(a[1]) for a,b in pairs})==1,'AMBIGUOUS_NATIVE_PROFILE_LOGIC')
    pairs.sort(key=lambda ab:(not str(ab[0][0]).startswith(str(root/'stage13e2_all19_local_results')),not str(ab[1][0]).startswith(str(root/'stage13e3_S14_restore_local_results')),str(ab[0][0]),str(ab[1][0])))
    a,b=pairs[0]
    p.jwrite(out/'PROFILE_BINDING.json',dict(closed_profile=str(a[0]),s14_profile=str(b[0]),closed_sha=p.sha(a[0]),s14_sha=p.sha(b[0]),template_kind=a[1]['kind'],closed_ids=sorted(closed_ids),s14_closed_ids=sorted(s14_ids),same_code_except_literal_closure_set=True,warning='PROFILE_LOGIC_MATCH_NOT_PHYSICAL_BRIDGE_CERTIFICATION'))
    return a[0],a[1]

def source_archive(bridges,out):
    matches=[x for x in bridges.rglob(PRIOR) if x.is_file()];valid=[x for x in matches if p.sha(x)==PRIOR_SHA];p.require(valid,'PRIOR_13E9D_ARCHIVE_NOT_FOUND_OR_HASH_MISMATCH')
    q=valid[0];manifest=json.loads((ROOT/'inputs/PRIOR_13E9D_ARCHIVE_MANIFEST.json').read_text());res={}
    with zipfile.ZipFile(q) as z:
        p.require(z.testzip() is None,'PRIOR_ARCHIVE_CRC');names=set(z.namelist())
        for sid in MATRIX_IDS:
            n=f'results/scenarios/{sid}/T_seconds_941.npz';p.require(n in names,'PRIOR_MATRIX_MISSING');data=z.read(n);p.require(hashlib.sha256(data).hexdigest()==manifest['files'][n]['sha256'],'PRIOR_MATRIX_HASH');res[sid]=data
        # Capture actual archived builder source for review, never execute or patch it implicitly.
        n='code/stage13e9d_fasttrack.py';data=z.read(n);p.require(hashlib.sha256(data).hexdigest()==manifest['files'][n]['sha256'],'PRIOR_BUILDER_HASH');(out/'PRIOR_13E9D_BUILDER.py.txt').write_bytes(data)
    p.jwrite(out/'PRIOR_SOURCE_QA.json',dict(path=str(q),archive_sha256=PRIOR_SHA,matrix_sha256={k:hashlib.sha256(v).hexdigest() for k,v in res.items()},builder_sha=manifest['files'][n]['sha256']))
    return res

def matrix_check(base,sid,matrices,out):
    import numpy as np
    data=matrices[sid]
    with np.load(io.BytesIO(data),allow_pickle=False) as z:
        candidates=[k for k in z.files if z[k].shape==(941,941) and z[k].dtype.kind in 'fiu'];p.require(len(candidates)==1,'PRIOR_MATRIX_ARRAY_SCHEMA');T=z[candidates[0]].astype(float)
        zid=[k for k in z.files if z[k].shape==(941,) and z[k].dtype.kind in 'US'];p.require(len(zid)==1,'PRIOR_MATRIX_ZONE_IDS_MISSING');ids=list(map(str,z[zid[0]]))
    p.require(len(set(ids))==941,'MATRIX_ZONE_IDS_NOT_UNIQUE');anch={r['zone_id']:r for r in p.readcsv(ROOT/'inputs/13e9_anchor_941.csv')};snaps={r['zone_id']:r for r in p.readcsv(ROOT/'inputs/13e9_new_941_snaps.csv')};p.require(set(ids)==set(anch)==set(snaps),'MATRIX_ZONE_CROSSWALK')
    rng=random.Random(20261124);pairs=set()
    while len(pairs)<40:
        i,j=rng.randrange(941),rng.randrange(941)
        if i!=j and math.isfinite(T[i,j]) and T[i,j]>0:pairs.add((i,j))
    # Discriminating historical pairs, not current AADT. Compare to other measured scenarios.
    for other,v in matrices.items():
        if other==sid:continue
        with np.load(io.BytesIO(v),allow_pickle=False) as z:
            m=[k for k in z.files if z[k].shape==(941,941) and z[k].dtype.kind in 'fiu'];ii=[k for k in z.files if z[k].shape==(941,) and z[k].dtype.kind in 'US'];p.require(len(m)==len(ii)==1 and list(map(str,z[ii[0]]))==ids,'PRIOR_MATRIX_ALIGNMENT');a=z[m[0]].astype(float)
        dif=np.abs(T-a);dif[~np.isfinite(dif)]=0;np.fill_diagonal(dif,0)
        for idx in np.argsort(dif.ravel())[-12:]:
            i,j=divmod(int(idx),941)
            if dif[i,j]>1 and T[i,j]>0:pairs.add((i,j))
    rr=[];success=[]
    for mode in ('raw_anchor','prior_snap'):
        coords={z:(float(anch[z]['new_lon']),float(anch[z]['new_lat'])) if mode=='raw_anchor' else (float(snaps[z]['new_snap_lon']),float(snaps[z]['new_snap_lat'])) for z in ids};ok=0
        for i,j in sorted(pairs):
            k=coords[ids[i]]+coords[ids[j]];r=p.table_group(base,[k])[k];t=r.get('seconds');passed=t is not None and math.isfinite(t) and abs(t-T[i,j])<=1.;ok+=int(passed);rr.append(dict(scenario_id=sid,mode=mode,zone_o=ids[i],zone_d=ids[j],prior_seconds=T[i,j],live_seconds=t,delta_seconds=None if t is None else t-T[i,j],match=int(passed)))
        if ok==len(pairs):success.append(mode)
    p.writecsv(out/f'HISTORICAL_MATRIX_FINGERPRINT_{sid}.csv',rr);p.require(bool(success),'HISTORICAL_MATRIX_FINGERPRINT_FAIL '+sid);return dict(pairs=len(pairs),matching_coordinate_modes=success)

def native_bin(name,reference):
    q=Path(reference).parent/name
    if q.is_file() and os.access(q,os.X_OK):return str(q)
    x=shutil.which(name);p.require(x,'NATIVE_BINARY_NOT_FOUND '+name);return x

def build_graph(bridges,sid,selected,way,profile_path,template,work,out,binary):
    # Build with the same source PBF and executable Lua logic, replacing only the literal closure set.
    closed=closure_for(selected,way);text=render(template,closed);p.require(len(table_templates(text,closed))==1,'RENDERED_CLOSURE_SET_MISMATCH');newt=table_templates(text,closed)[0];p.require(canonical(newt)==canonical(template),'RENDER_CHANGED_LUA_LOGIC')
    pbf=(bridges/'_data/osm/ukraine-261111.osm.pbf').resolve();p.require(pbf.is_file() and p.sha(pbf)==PBF_SHA,'FROZEN_PBF_HASH_MISMATCH')
    w=work/sid;w.mkdir(parents=True,exist_ok=False);inp=w/(sid+'.osm.pbf');inp.symlink_to(pbf);graph=w/(sid+'.osrm')
    prof=profile_path.parent/(f'.dream19_p1b_{sid}_{os.getpid()}.lua');p.require(not prof.exists(),'TEMP_PROFILE_COLLISION');prof.write_text(text,encoding='utf8')
    (out/f'PROFILE_{sid}.lua').write_text(text,encoding='utf8')
    bins={n:native_bin(n,binary) for n in ('osrm-extract','osrm-partition','osrm-customize')};p.jwrite(out/f'BUILD_{sid}.json',dict(pbf_sha256=PBF_SHA,closed_ids=sorted(closed),restored_bridges=sorted(selected),profile_sha256=p.sha(prof),binary_sha256={n:p.sha(Path(x)) for n,x in bins.items()},profile_source=str(profile_path)))
    try:
        for name,cmd in [('extract',[bins['osrm-extract'],'-p',str(prof),str(inp)]),('partition',[bins['osrm-partition'],str(graph)]),('customize',[bins['osrm-customize'],str(graph)])]:
            print(sid,'building',name,flush=True)
            with (out/f'BUILD_{sid}_{name}.log').open('w') as log:subprocess.run(cmd,cwd=profile_path.parent,stdout=log,stderr=subprocess.STDOUT,check=True)
        p.graph_parts(graph);return graph
    finally:prof.unlink(missing_ok=True)

def audit_p1a(rows,keys,mass,scope,fits,d,out):
    g=json.loads((d/'GATE.json').read_text());p.require(g['status']=='CONDITIONAL_COUNTERFACTUAL_ROUTING_COMPLETE_REVIEW_REQUIRED' and g['source_sha']==p.SHA and g['accepted_unique_keys']==143076,'P1A_GATE_NOT_ACCEPTED')
    op,closed,s14=(gz_pairs(d/f'{s}_PAIR_RESULTS.csv.gz',keys) for s in ('OPEN','CLOSED','S14'))
    p.require(all(p.matches_ledger(v,keys[k]) for k,v in op.items()),'OPEN_LEDGER_REPLAY_FAIL')
    sc={'OPEN':op,'S14':s14};a,b,outs,common=p.aggregate(rows,op,closed,sc,mass,scope)
    old={(r['scenario_id'],r['component'],r['model']):r for r in p.readcsv(d/'SCENARIO_COMPONENT_BENEFITS.csv')};maxdiff=0
    for r in a:
        prev=old[r['scenario_id'],r['component'],r['model']]
        for k,v in r.items():
            if isinstance(v,(int,float)):
                dd=abs(v-float(prev[k]));maxdiff=max(maxdiff,dd);p.require(dd<=1e-9*max(1,abs(v)),'P1A_AGGREGATE_MISMATCH '+k)
    # Independent direct weighted dot products, not previous aggregate output.
    audit=[]
    for sid,z in sc.items():
        for cm in p.COMPS:
            val=math.fsum(r['weight']*max(0,closed[r['key']]['seconds']-z[r['key']]['seconds'])/60 for r in rows if (r['component'],r['model'])==cm and r['weight']>0 and r['route_status']=='OK' and r['key'] in common)
            ref=b[sid,*cm]['common_finite_minutes_per_component_unit'];p.require(abs(val-ref)<1e-9,'DIRECT_DOT_PRODUCT_MISMATCH');audit.append(dict(scenario_id=sid,component=cm[0],model=cm[1],direct_minutes=val,reported_minutes=ref,delta=val-ref))
    p.writecsv(out/'P1A_INDEPENDENT_DOT_PRODUCT.csv',audit);p.jwrite(out/'P1A_AUDIT.json',dict(status='PASS',max_aggregate_delta=maxdiff,common_keys=len(common),positive_time_saving_keys={s:sum(st=='FINITE_PAIRED' and dt>0 for st,dt in x.values()) for s,x in outs.items()},source_sha=SOURCE_SHA));return op,closed,s14

def regrets(benefits,cands,total):
    """Compute regret ONLY within the fixed five-candidate set and common finite OD support."""
    by=collections.defaultdict(dict)
    for r in benefits:
        if r['scenario_id'] in CANDIDATES:by[r['scale_scenario']][r['scenario_id']]=r
    if not by or any(set(x)!=set(CANDIDATES) for x in by.values()):return [],[]
    rr=[]
    for f,g in by.items():
        best=max(x['MAIN_common_vehicle_hours_day'] for x in g.values());p.require(best>=0,'NEGATIVE_BEST')
        for sid,r in g.items():
            val=r['MAIN_common_vehicle_hours_day'];reg=best-val;rr.append(dict(scale_scenario=f,scenario_id=sid,conditional_hours_day=val,best_hours_day=best,regret_hours_day=reg,relative_regret=reg/best if best>0 else 0.,cost_UAH=str(cands[sid]['cost']),budget_UAH=str(cands[sid]['budget']),status='FIXED_CANDIDATES_COMMON_FINITE_SUPPORT_ONLY'))
    sc=[]
    for sid in CANDIDATES:
        x=[r for r in rr if r['scenario_id']==sid];sc.append(dict(scenario_id=sid,max_relative_regret=max(r['relative_regret'] for r in x),max_regret_hours_day=max(r['regret_hours_day'] for r in x),cost_UAH=str(cands[sid]['cost']),selected='+'.join(sorted(cands[sid]['selected']))))
    sc.sort(key=lambda x:(x['max_relative_regret'],D(x['cost_UAH']),x['scenario_id']))
    for i,r in enumerate(sc,1):r['relative_minimax_rank']=i;r['status']='SCENARIO_CONDITIONAL_CANDIDATE_RANK_NOT_GLOBAL_OPTIMUM'
    return rr,sc

def paired_differences(rows,outs,common,cands,fits):
    # Component-wise paired MC variance; exact strata have no MC variance.
    # Independent inter/local draws for combined variance, not structural uncertainty.
    rr=[]
    for i,a in enumerate(CANDIDATES):
        for b in CANDIDATES[i+1:]:
            if a not in outs or b not in outs:continue
            base={}
            for cm in p.COMPS:
                strata=collections.defaultdict(lambda:[0,0.,0.,False])
                for r in rows:
                    if (r['component'],r['model'])!=cm:continue
                    s=strata[r['stratum']];s[0]+=1;s[3]=r['sampling']=='EXACT';x=0.
                    if r['route_status']=='OK' and r['weight']>0 and r['key'] in common:x=r['weight']*(outs[a][r['key']][1]-outs[b][r['key']][1])
                    s[1]+=x;s[2]+=x*x
                base[cm]=(sum(v[1] for v in strata.values()),sum(max(0,(n*s2-s1*s1)/(n-1)) for n,s1,s2,e in strata.values() if n>1 and not e))
            seen=set()
            for f in fits:
                qi,ql,qu=(float(f[k]) for k in ('Q_inter','Q_local','Q_U1'));q=(qi,ql,qu)
                if q in seen:continue
                seen.add(q);mi,vi=base['inter','MAIN'];ml,vl=base['local','MAIN'];mu,vu=base['inter','U1'];diff=(qi*mi+ql*ml)/60;se=math.sqrt(qi*qi*vi+ql*ql*vl)/60
                rr.append(dict(scenario_a=a,scenario_b=b,scale_scenario=f['fold'],MAIN_difference_hours_day=diff,MAIN_MC_SE_hours_day=se,U1_difference_hours_day=qu*mu/60,U1_MC_SE_hours_day=qu*math.sqrt(vu)/60,interpretation='MC_ONLY_NOT_STRUCTURAL_UNCERTAINTY'))
    return rr

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bridges',required=True,type=Path);ap.add_argument('--out',required=True,type=Path);ap.add_argument('--offline',action='store_true');ap.add_argument('--scenario-port',type=int,default=5002);ap.add_argument('--workers',type=int,default=8);ap.add_argument('--osrm-bin',default='osrm-routed');ap.add_argument('--profiles',nargs=2);ap.add_argument('--scenarios',nargs='+',choices=ORDER);a=ap.parse_args();o=a.out;o.mkdir(parents=True,exist_ok=True);f=o/'_frozen';pr=o/'_p1a';build=o/'_graphs';build.mkdir(exist_ok=True);gate=dict(stage='P1B',status='STARTED',demand_structure_frozen=True,absolute_scale_validated=False,external_validation=False,prior_candidate_set_changed=False,no_global_20pct_optimum_claim=True,no_national_benefit_claim=True);sc={};code=1
    try:
        p.require(a.scenario_port not in (5000,5001) and 1<=a.workers<=32,'BAD_PORT_OR_WORKERS');n=p.source_unpack(f);pn=unpack(ROOT/'frozen'/SOURCE,pr,SOURCE_SHA);rows,keys,tot,unknown,counts=p.load_ledger(f/'ROUTE_LEDGER.csv');p.require(len(rows)==218354 and len(keys)==143076,'FROZEN_OD_LEDGER_COUNT');mass={(r['component'],r['model']):r for r in p.readcsv(f/'MASS_QA.csv')};scope={int(r['parent']):r['scope'] for r in p.readcsv(f/'PARENT_SCOPE_941.csv')};fits=p.readcsv(f/'RETROSPECTIVE_FIT.csv');op,closed,s14=audit_p1a(rows,keys,mass,scope,fits,pr,o);sc={'OPEN':op,'S14':s14};cands,cost,total,way=candidates()
        p.writecsv(o/'FROZEN_PORTFOLIO_CANDIDATES.csv',(dict(scenario_id=s,selected='+'.join(sorted(v['selected'])),historical_cost_UAH=str(v['cost']),budget_UAH=str(v['budget']),source=v['source'],closed_way_ids=';'.join(map(str,sorted(closure_for(v['selected'],way))))) for s,v in cands.items()));p.writecsv(o/'HISTORICAL_COSTS_19.csv',(dict(bridge_id=s,historical_cost_UAH=str(c),operational_way_ids=';'.join(map(str,sorted(way[s])))) for s,c in sorted(cost.items())))
        gate.update(p0e_manifest_items=n,p1a_manifest_items=pn,accepted_unique_keys=len(keys),prior_candidate_count=len(cands),historical_total_UAH=str(total),historical_20pct_budget_UAH=str(total*D('.2')),s19_way_assumption=sorted(way['S19']))
        if a.offline:gate['status']='P1A_REPRODUCED_AND_PRIOR_CANDIDATES_BOUND_NO_NEW_ROUTING';code=0;return code
        p.require(p.free_port(a.scenario_port),'SCENARIO_PORT_OCCUPIED');binary=shutil.which(a.osrm_bin) or (a.osrm_bin if Path(a.osrm_bin).is_file() else None);p.require(binary,'OSRM_BINARY_NOT_FOUND');p.require(p.sha(Path(binary))==json.loads((pr/'GRAPH_CLOSED_SHA256.json').read_text())['osrm_routed_binary_sha256'],'OSRM_BINARY_CHANGED_SINCE_P1A')
        rng=random.Random(2026112420);live=[]
        for k in rng.sample(sorted(keys),24):
            z=p.pair_route('http://127.0.0.1:5001',k);ok=p.matches_ledger(z,keys[k]) and p.same_endpoints(z,op[k]);live.append(dict(zip(p.FIELDS,k))|dict(match=int(ok)))
        p.writecsv(o/'OPEN_FINGERPRINT.csv',live);p.require(all(x['match'] for x in live),'OPEN_FINGERPRINT_MISMATCH')
        # Verify local historical graph bytes against the actual P1A graph manifests.
        for s in ('CLOSED','S14'):
            m=json.loads((pr/f'GRAPH_{s}_SHA256.json').read_text());q=Path(m['graph_base']);p.require(q.resolve().is_relative_to(a.bridges.resolve()) and p.graph_manifest(q)==m['graph_files'],'P1A_LOCAL_GRAPH_CHANGED '+s)
        matrices=source_archive(a.bridges,o);cache=a.bridges/'P1B_ROUTE_CACHE';cache.mkdir(exist_ok=True);witnesses=p.witness_set(keys);qa=[];profile=None;profile_error=None;reference_build_verified=False
        for sid in ORDER:
            if a.scenarios and sid not in a.scenarios:continue
            selected=cands[sid]['selected'];graph=None;new=False
            try:
                if sid=='A18_EXCEPT_S14' and Path(str(a.bridges/A18_PATH)+'.mldgr').is_file():graph=a.bridges/A18_PATH
                else:
                    if profile is None:
                        if profile_error:raise ValueError(profile_error)
                        try:profile=scan_profiles(a.bridges,closure_for(set(),way),closure_for({'S14'},way),o,a.profiles)
                        except Exception as ex:profile_error=str(ex);raise
                    if sid not in MATRIX_IDS:p.require(reference_build_verified,'NO_REBUILT_HISTORICAL_MATRIX_REFERENCE_PASSED')
                    graph=build_graph(a.bridges,sid,selected,way,profile[0],profile[1],build,o,binary);new=True
                gmanifest=p.graph_manifest(graph);fp=dict(source_sha=SOURCE_SHA,demand_sha=p.SHA,scenario_id=sid,selected=sorted(selected),graph_files=gmanifest,binary_sha=p.sha(Path(binary)),runner_sha=p.sha(Path(__file__)),route_runner_sha=p.sha(Path(p.__file__)))
                p.jwrite(o/f'GRAPH_{sid}_SHA256.json',fp);cp=cache/(sid+'.csv.gz');z=p.load_cache(cp,fp,keys)
                with p.NativeGraph(graph,a.scenario_port,binary,o/f'OSRM_{sid}.log') as base:
                    hist=matrix_check(base,sid,matrices,o) if sid in MATRIX_IDS else {'status':'NO_PRIOR_MATRIX_IN_RECOVERED_ARCHIVE'}
                    ww=p.bridge_witnesses(base,witnesses,sid);p.writecsv(o/f'BRIDGE_WITNESSES_{sid}.csv',ww)
                    blocked=closure_for(selected,way);bad=[r for r in ww if int(r['target_id'].split('_WAY_')[1]) in blocked and r.get('two_node_way_direct_edge')==1];p.require(not bad,'CLOSED_DIRECT_EDGE_WITNESS_VIOLATION')
                    if z is None:z=p.evaluate(base,keys,60,a.workers,sid,op);p.save_cache(cp,fp,z)
                # All OD in the same registry; exclude nesting violations, never impute unknowns.
                outcomes={k:p.outcome(op[k],closed[k],v) for k,v in z.items()};vcount=collections.Counter(v[0] for v in outcomes.values());p.require(not vcount['NESTING_VIOLATION'] and not any(v['status']=='FASTER_THAN_OPEN' for v in z.values()),'NESTING_VIOLATIONS_IN_REPLAY');p.require(vcount['FINITE_PAIRED']>0,'NO_FINITE_PAIRED_SUPPORT')
                if new and sid in MATRIX_IDS:reference_build_verified=True
                sc[sid]=z;p.writecsv(o/f'{sid}_PAIR_RESULTS.csv.gz',(p.flatten(k,v) for k,v in z.items()));qa.append(dict(scenario_id=sid,status='ROUTED_CONDITIONAL',historical_matrix_check=json.dumps(hist),outcome_counts=json.dumps(dict(vcount)),new_graph=int(new)))
            except Exception as ex:
                print(sid,'BLOCKED',ex,flush=True);qa.append(dict(scenario_id=sid,status='BLOCKED',detail=str(ex),new_graph=int(new)))
            finally:
                # Only remove our own new temporary graph directory. Cache and diagnostics remain.
                if (build/sid).exists():shutil.rmtree(build/sid)
                p.writecsv(o/'SCENARIO_EXECUTION_QA.csv',qa,fields=['scenario_id','status','historical_matrix_check','outcome_counts','new_graph','detail'])
        rr,base,outs,common=p.aggregate(rows,op,closed,sc,mass,scope);p.writecsv(o/'SCENARIO_COMPONENT_BENEFITS.csv',rr);scaled=p.scale_scenarios(fits,base,sc);p.writecsv(o/'SCALE_SCENARIO_VEHICLE_HOURS.csv',scaled);baseline_common={k for k in keys if p.outcome(op[k],closed[k],op[k])[0]=='FINITE_PAIRED' and p.outcome(op[k],closed[k],s14[k])[0]=='FINITE_PAIRED'};support_qa=[]
        for cm in p.COMPS:
            oldmass=math.fsum(r['weight'] for r in rows if (r['component'],r['model'])==cm and r.get('key') in baseline_common);newmass=math.fsum(r['weight'] for r in rows if (r['component'],r['model'])==cm and r.get('key') in common);support_qa.append(dict(component=cm[0],model=cm[1],p1a_common_mass=oldmass,p1b_common_mass=newmass,retained_fraction=newmass/oldmass if oldmass>0 else 0.))
        p.writecsv(o/'COMMON_SUPPORT_QA.csv',support_qa);support_pass=len(common)>=.99*len(baseline_common) and all(r['retained_fraction']>=.99 for r in support_qa);gate.update(common_support_99pct_engineering_gate=bool(support_pass),p1a_common_keys=len(baseline_common));reg,rank=regrets(scaled,cands,total) if support_pass else ([],[]);p.writecsv(o/'CANDIDATE_REGRET.csv',reg);p.writecsv(o/'CANDIDATE_MINIMAX_RANK.csv',rank)
        p.writecsv(o/'PAIRED_CANDIDATE_DIFFERENCES.csv',paired_differences(rows,outs,common,cands,fits));gate.update(scenarios_computed=list(sc),common_finite_unique_keys=len(common),fixed_candidate_comparison_complete=all(s in sc for s in CANDIDATES),a18_routed='A18_EXCEPT_S14' in sc,scenario_graph_review_required=True,regret_definition='max relative regret on five prior fixed candidates, common finite OD support; absolute regret also reported; not a global optimum',status='PORTFOLIO_REPLAY_COMPLETE_REVIEW_REQUIRED' if all(s in sc for s in ORDER) else 'PARTIAL_PORTFOLIO_REPLAY_REVIEW_REQUIRED');code=0;return code
    except Exception as ex:gate.update(status='BLOCKED_OR_PARTIAL',error=str(ex));traceback.print_exc();return 1
    finally:p.jwrite(o/'GATE.json',gate);shutil.rmtree(f,ignore_errors=True);shutil.rmtree(pr,ignore_errors=True);shutil.rmtree(build,ignore_errors=True)
if __name__=='__main__':sys.exit(main())
