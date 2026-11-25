#!/usr/bin/env python3
"""P0E: one bounded precision pass, no change to P0D demand specification.
The old and new importance estimators are combined with fixed sample-count weights.
Known support only: no renormalization of unobserved local mass or failed routes.
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, csv, hashlib, json, math, sys, traceback, zipfile
from pathlib import Path, PurePosixPath
import numpy as np
from shapely.geometry import LineString
from shapely.strtree import STRtree
import p0d as b
ROOT=Path(__file__).resolve().parents[1]
OLD_NAME='P0D_FIX1_RESULTS_20261124T153631Z.zip'
OLD_SHA='81a14a736a095a449f6c1e642bc45bef25a3f4a2bb474641f819acbcb11a8730'
SEED=2026112419
NEW_INTER=24000
NEW_LOCAL=768
COMPS=[('inter','MAIN'),('inter','U1'),('local','MAIN')]

def readcsv(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def read_json(p):return json.loads(p.read_text())

def parse_ledger(path):
    rr=readcsv(path)
    for r in rr:
        for k in ('parent_o','parent_d','planned_n'):r[k]=int(r[k])
        for k in ('weight','lon_o','lat_o','lon_d','lat_d'):r[k]=float(r[k])
        for k in ('reference_local_time_min','duration_min','distance_m','max_snap_m'):
            r[k]=float(r[k]) if r.get(k) not in ('',None) else ''
        assert math.isfinite(r['weight']) and r['weight']>=0
        assert (r['parent_o']==r['parent_d'])==(r['component']=='local')
    assert len({(r['draw'],r['component'],r['model']) for r in rr})==len(rr)
    return rr

def parse_qa(path):
    rr=readcsv(path)
    for r in rr:
        for k in ('parent','nodes','n_pairs','n_requested'):r[k]=int(r[k])
        for k in ('national_pop_weight','same_cell_mass','unknown_table_reserve','routable_mass'):r[k]=float(r[k])
    assert len({r['parent'] for r in rr})==len(rr)
    return rr

def unpack_verified(src,dest):
    assert b.sha(src)==OLD_SHA,'FROZEN_RESULT_ARCHIVE_HASH_FAIL'
    dest.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(src) as z:
        assert z.testzip() is None,'CRC_FAIL'
        names=z.namelist();roots={PurePosixPath(x).parts[0] for x in names};assert len(roots)==1
        for i in z.infolist():
            p=PurePosixPath(i.filename);assert not p.is_absolute() and '..' not in p.parts and not i.is_dir() and len(p.parts)==2
            with z.open(i) as f:(dest/p.name).write_bytes(f.read())
    sha=read_json(dest/'SHA256SUMS.json'); assert set(sha)=={p.name for p in dest.iterdir() if p.name!='SHA256SUMS.json'}
    assert all(b.sha(dest/k)==v for k,v in sha.items()),'OLD_RESULT_MEMBER_HASH_FAIL'
    return len(sha)

def source(out):
    old=out/'_frozen'; n=unpack_verified(ROOT/'frozen'/OLD_NAME,old)
    gate=read_json(old/'GATE.json');assert gate['status']=='CONDITIONAL_INTEGRATED_ROUTING_COMPLETE_REVIEW_REQUIRED'
    assert gate['time_sha']==b.TIME_SHA and gate['polygon_sha']==b.POLY_SHA and gate['h_minutes']==b.H
    assert gate['inter_draws']==12000 and gate['local_draws_per_parent']==256
    plan=parse_ledger(old/'ROUTE_LEDGER.csv');qa=parse_qa(old/'LOCAL_TABLE_QA.csv');
    scope=sorted(readcsv(old/'PARENT_SCOPE_941.csv'),key=lambda r:int(r['parent']));assert len(scope)==b.N
    dist=np.array([float(r['distance_m']) for r in scope]);P=np.array([float(r['pop']) for r in scope]);
    cutspec=read_json(old/'TARGET_CUTS_PROJECTED_3035.json');cuts=[LineString(r['coordinates']) for r in cutspec];labels=[r['target_id'] for r in cutspec];meta=[(r['kind'],r['road_or_bridge']) for r in cutspec]
    assert len(labels)==len(set(labels))==155 and len(cuts)==155
    assert all(set(filter(None,r['hits'].split(';')))<=set(labels) for r in plan)
    nodes=collections.defaultdict(list)
    for r in readcsv(old/'MICROZONES.csv'):
        r['parent']=int(r['parent']);r['grid_m']=int(r['grid_m']);
        for k in ('lon','lat','pop','nres'):r[k]=float(r[k])
        nodes[r['parent']].append(r)
    return old,n,gate,plan,qa,dist,P,cuts,labels,meta,dict(nodes)

def audit(old,plan,qa,dist,P,cuts,labels,meta,n,out):
    st=b.stats(plan,labels,dist,qa,P)
    orig={(r['component'],r['model'],r['target_id']):r for r in readcsv(old/'ROUTED_TARGET_SHARES.csv')}
    errs=collections.defaultdict(float)
    for (c,m),v in st.items():
        for k,s in enumerate(labels):
            row=orig[c,m,s]
            for field,key in [('known_lower_share','hit'),('ESS','ess'),('RSE','rse'),('guard_parent_share','guard')]:
                a=v[key][k];z=float(row[field]) if row[field]!='' else np.nan
                if not np.isfinite(a) or not np.isfinite(z):assert np.isnan(a) and np.isnan(z)
                else:
                    assert np.isclose(a,z,rtol=2e-10,atol=1e-14),(field,s,a,z)
                    errs[field]=max(errs[field],abs(a-z))
    assert len(orig)==3*len(labels)
    masses=[]
    for c,m in COMPS:
        v=st[c,m];rr=[r for r in plan if r['component']==c and r['model']==m]
        ok=math.fsum(r['weight'] for r in rr if r['route_status']=='OK')
        masses.append(dict(component=c,model=m,n_rows=len(rr),n_ok=sum(r['route_status']=='OK' for r in rr),draw_mass=v['draw_mass'],ok_mass=ok,unknown_total=v['unknown'],omitted=v['omitted'],same_cell=v['diag'],table_unknown=v['tableunk'],conditional_vkt_per_unit=v['vkt']))
        assert np.isclose(ok+v['unknown'],v['draw_mass']+v['omitted']+v['diag']+v['tableunk'],rtol=1e-10,atol=1e-12)
    b.write(out/'OLD_MASS_AUDIT.csv',masses)
    # Reproduce the exact previous calibration, without using AADT in proposal design.
    ids,xy,PP,R,I,counts,fs=b.read_inputs();assert np.allclose(P,PP,rtol=1e-12,atol=1e-8)
    aout=out/'old_eval';aout.mkdir(exist_ok=True)
    reg,fits=b.evaluation(st,labels,fs,counts,aout)
    aa=readcsv(old/'RETROSPECTIVE_METRICS.csv');bb=readcsv(aout/'RETROSPECTIVE_METRICS.csv'); assert len(aa)==len(bb)
    err=0.
    for x,y in zip(aa,bb):
        assert tuple(x[k] for k in ('fold','scope','model','n'))==tuple(y[k] for k in ('fold','scope','model','n'))
        for k in ('mean_abs_log','median_abs_log','median_ratio','mean_log_bias'):
            if x[k]=='':assert y[k]=='';continue
            e=abs(float(x[k])-float(y[k]));err=max(err,e);assert e<1e-9
    b.js(out/'OLD_AUDIT.json',dict(source_sha=OLD_SHA,internal_hashes=n,route_rows=len(plan),share_rows=len(orig),max_abs_share_qa_diff=dict(errs),max_abs_metric_diff=err,strict_count=sum(r['strict'] for r in reg),sampled_local_parents=sum(r['route_sampling']=='IID_STRATIFIED' for r in qa),new_WIM_opened=False))
    return st,reg,(ids,xy,PP,R,I,counts,fs)

def key(r):return tuple(round(float(r[k]),7) for k in ('lon_o','lat_o','lon_d','lat_d'))

def weak_targets(st,labels,meta,fs):
    # Only prior route hit and precision information, never AADT, sets proposals.
    active={s for s,(k,_) in zip(labels,meta) if k=='bridge_candidate' or s in fs}
    ans={}
    for c in ('inter','local'):
        group=[st[c,m] for cc,m in COMPS if cc==c]
        ans[c]={s for i,s in enumerate(labels) if s in active and any(not(v['hit'][i]>0 and v['ess'][i]>=20 and np.isfinite(v['rse'][i]) and v['rse'][i]<=.2) for v in group)}
    return ans

def grouped_targets(ids,labels,meta):
    # Parallel carriageway/candidate cuts do not receive multiple sampling budgets.
    d={s:(r if k=='bridge_candidate' else s) for s,(k,r) in zip(labels,meta) if s in ids}
    return d

def pilot_inter(plan,weak,labels,meta,n):
    groups=grouped_targets(weak,labels,meta);acc=collections.defaultdict(lambda:collections.defaultdict(float))
    for r in plan:
        if r['component']!='inter' or r['route_status']!='OK' or r['weight']<=0:continue
        g={groups[s] for s in r['hits'].split(';') if s in groups}
        ix=r['parent_o']*n+r['parent_d']
        for s in g:acc[(s,r['model'])][ix]+=r['weight']
    out=np.zeros(n*n)
    for d in acc.values():
        den=math.fsum(d.values());
        for i,w in d.items():out[i]+=w/den
    return out.reshape(n,n)

def blend(base,geom,pilot):
    # Exact mixture; missing targeting distributions revert to the base, not a restricted support.
    base=b.norm(base);out=.5*base
    for x in (geom,pilot):out+=.25*(b.norm(x) if np.sum(x)>0 else base)
    out/=out.sum(); assert np.all(out[base>0]>=.499999999*base[base>0]); assert np.all(out[base==0]==0)
    return out

def inter_geom(qbar,xy,weak,labels,meta,cuts):
    x,y=b.WGS_M.transform(xy[:,0],xy[:,1]);acc=np.zeros_like(qbar); groups=grouped_targets(weak,labels,meta);ng=collections.Counter(groups.values())
    for s,cut in zip(labels,cuts):
        if s not in groups:continue
        pt=cut.centroid; a=np.exp(-np.hypot(x-pt.x,y-pt.y)/60000);v=qbar*np.outer(a,a);z=v.sum()
        if z>0:acc+=v/z/ng[groups[s]]
    return acc

def build_inter(old,st,weak,labels,meta,cuts,xy,nodes,targets,T,q0,q1,dist,n,seed,out):
    pold=b.proposal(q0,q1,xy,targets,meta)
    # Reproduce original HT weights before using previous draws.
    for r in old:
        if r['component']!='inter':continue
        i,j=r['parent_o'],r['parent_d'];q=q0 if r['model']=='U1' else q1
        assert np.isclose(r['weight'],q[i,j]/pold[i,j]/12000,rtol=2e-10,atol=1e-15),'OLD_INTER_PROPOSAL_MISMATCH'
    geom=inter_geom((q0+q1)/2,xy,weak,labels,meta,cuts);pilot=pilot_inter(old,weak,labels,meta,len(xy));p=blend(pold,geom,pilot)
    assert np.all(p[~np.eye(len(xy),dtype=bool)]>0)
    new=b.inter_plan(q0,q1,p,xy,nodes,dist,n,seed)
    for r in new:r['draw']='E'+r['draw'];r['stratum']='national_new';r['phase']='P0E_NEW'
    factor=12000/(12000+n);prev=[]
    for r in old:
        if r['component']=='inter':prev.append(dict(r,weight=r['weight']*factor,stratum='national_old',phase='P0D_REUSED'))
    for r in new:r['weight']*=1-factor
    b.js(out/'INTER_PROPOSAL_QA.json',dict(old_draws=12000,new_draws=n,old_factor=factor,new_factor=1-factor,weak_targets=len(weak),geom_nonzero=int(np.count_nonzero(geom)),pilot_nonzero=int(np.count_nonzero(pilot)),min_proposal=float(p[p>0].min()),max_q_over_proposal_MAIN=float(np.max(np.divide(q1,p,out=np.zeros_like(p),where=p>0))),max_q_over_proposal_U1=float(np.max(np.divide(q0,p,out=np.zeros_like(p),where=p>0))),full_support=True,AADT_used_for_proposal=False))
    return prev,new

def local_geom(mat,coords,weak,labels,meta,cuts):
    x,y=map(np.asarray,b.WGS_M.transform(*zip(*coords)));acc=np.zeros_like(mat);groups=grouped_targets(weak,labels,meta);ng=collections.Counter(groups.values())
    for s,cut in zip(labels,cuts):
        if s not in groups:continue
        pt=cut.centroid;d=np.hypot(x-pt.x,y-pt.y)
        if np.min(d)>40000:continue
        a,z=list(cut.coords)[0],list(cut.coords)[-1];sign=(z[0]-a[0])*(y-pt.y)-(z[1]-a[1])*(x-pt.x)
        score=np.outer(np.exp(-d/20000),np.exp(-d/20000))*(np.outer(sign,sign)<=0)
        v=mat*score;total=v.sum()
        if total>0:acc+=v/total/ng[groups[s]]
    return acc

def local_pilot(mat,idx,old,weak,labels,meta):
    groups=grouped_targets(weak,labels,meta);acc=collections.defaultdict(lambda:collections.defaultdict(float));out=np.zeros_like(mat)
    for r in old:
        if r['route_status']!='OK' or r['weight']<=0:continue
        i,j=idx[r['node_o']],idx[r['node_d']]
        if mat[i,j]<=0:continue
        for s in {groups[s] for s in r['hits'].split(';') if s in groups}:acc[s][(i,j)]+=r['weight']
    for d in acc.values():
        den=math.fsum(d.values())
        for (i,j),w in d.items():out[i,j]+=w/den
    return out

def validate_local_table(q,mat,diag,unk,t,v,old):
    z=q['national_pop_weight'];pairs=int(np.count_nonzero(mat))
    assert pairs==q['n_pairs'],'LOCAL_TABLE_SUPPORT_CHANGED'
    for a,k in ((diag,'same_cell_mass'),(unk,'unknown_table_reserve'),(mat.sum(),'routable_mass')):
        assert np.isclose(z*a,q[k],rtol=2e-7,atol=1e-12),('LOCAL_TABLE_MASS_CHANGED',q['parent'],k,z*a,q[k])
    idx={r['node_id']:k for k,r in enumerate(v)}
    for r in old:
        i,j=idx[r['node_o']],idx[r['node_d']]
        assert mat[i,j]>0 and abs(t[i,j]-float(r['reference_local_time_min']))<=1/60+1e-8,('LOCAL_TABLE_TIME_CHANGED',q['parent'],r['draw'])
    return idx

def build_local(base,old,qa,nodes,weak,labels,meta,cuts,n,seed,out):
    rng=np.random.default_rng(seed);by=collections.defaultdict(list)
    for r in old:
        if r['component']=='local':by[r['parent_o']].append(r)
    prev=[];new=[];newqa=[];alloc=[];todo=[r for r in qa if r['route_sampling']=='IID_STRATIFIED'];print('sampled parent strata to revisit:',len(todo),flush=True)
    for q in qa:
        z=q['parent'];rr=by[z];nd=q['n_pairs'];assert len(rr)==q['n_requested'];
        if q['route_sampling']=='EXACT':
            prev.extend(dict(r,phase='P0D_EXACT_REUSED') for r in rr);newqa.append(dict(q));continue
        v=[r for r in nodes[z] if r['pop']>0];coords=[(r['lon'],r['lat']) for r in v];pop=[r['pop'] for r in v];assert len(v)==q['nodes']
        t,bad=b.table(base,coords);mat,diag,unk=b.local_matrix(pop,t,bad);idx=validate_local_table(q,mat,diag,unk,t,v,rr);zone=q['national_pop_weight'];flat=np.flatnonzero(mat.ravel()>0)
        if nd<=n:
            sample=flat;probs=None;factor=1.;sampling='EXACT';action='EXACT_REPLACES_OLD_SAMPLE';st=f'Z{z:03d}'
        else:
            geom=local_geom(mat,coords,weak,labels,meta,cuts);pilot=local_pilot(mat,idx,rr,weak,labels,meta);probs=blend(mat,geom,pilot).ravel();sample=rng.choice(mat.size,size=n,p=probs,replace=True);factor=n/(len(rr)+n);sampling='IID_HT';action='INDEPENDENT_ADDITIONAL_HT';st=f'Z{z:03d}_new'
            prev.extend(dict(r,weight=r['weight']*(1-factor),stratum=f'Z{z:03d}_old',phase='P0D_LOCAL_REUSED') for r in rr)
        for k,jj in enumerate(sample):
            i,j=divmod(int(jj),len(coords));a,d=coords[i],coords[j];w=zone*mat[i,j] if probs is None else zone*mat[i,j]/probs[int(jj)]/len(sample)*factor
            new.append(dict(draw=f'EL{z:03d}_{k:05d}',component='local',model='MAIN',parent_o=z,parent_d=z,node_o=v[i]['node_id'],node_d=v[j]['node_id'],lon_o=a[0],lat_o=a[1],lon_d=d[0],lat_d=d[1],weight=float(w),stratum=st,sampling=sampling,route_status='PENDING',planned_n=len(sample),reference_local_time_min=float(t[i,j]),phase='P0E_LOCAL_NEW'))
        # The structural mass ledger remains unchanged; requested count is now actual combined rows.
        newqa.append(dict(q,route_sampling='EXACT' if probs is None else 'MIXED_OLD_IID_NEW_HT',n_requested=len(sample)+(len(rr) if probs is not None else 0)))
        alloc.append(dict(parent=z,old_n=len(rr),positive_pairs=nd,new_n=len(sample),old_factor=1-factor,new_factor=factor,action=action,known_parent_mass=q['routable_mass']))
        if len(alloc)%10==0:print('local revisited',len(alloc),'/',len(todo),flush=True)
    b.write(out/'LOCAL_PRECISION_ALLOCATION.csv',alloc);b.write(out/'LOCAL_TABLE_QA.csv',newqa)
    return prev,new,newqa

def cache_from_old(plan):
    cache={}
    for r in plan:
        if r['weight']<=0 or r['route_status']=='ZERO_TARGET_MASS' or r['route_status']=='TABLE_ROUTE_TIME_MISMATCH':continue
        k=key(r);d=tuple(r[x] for x in ('route_status','duration_min','distance_m','max_snap_m','hits'))
        if k in cache:assert cache[k]==d,('INCONSISTENT_OLD_ROUTE',k,cache[k],d)
        else:cache[k]=d
    return cache

def query_one(base,k,tree,labels):
    r,status=b.route(base,k[:2],k[2:]);hits=''
    if r:
        x,y=b.WGS_M.transform(*zip(*r[2]));g=LineString(list(zip(x,y)));hits=';'.join(sorted({labels[int(i)] for i in tree.query(g,predicate='intersects')}))
    return (status,r[0] if r else '',r[1] if r else '',r[3] if r else '',hits)

def fingerprint_cache(base,old,cache,cuts,labels,out):
    tree=STRtree(cuts);rng=np.random.default_rng(SEED+3);rr=[];keys=[]
    for c in ('inter','local'):
        a=sorted({key(r) for r in old if r['component']==c and r['route_status']=='OK' and r['weight']>0});k=min(12,len(a));keys.extend(a[i] for i in sorted(rng.choice(len(a),k,replace=False)))
    for k in keys:
        a=cache[k];z=query_one(base,k,tree,labels);ok=a[0]==z[0]=='OK' and abs(a[1]-z[1])<=1/60 and abs(a[2]-z[2])<=1 and abs(a[3]-z[3])<=1 and a[4]==z[4]
        rr.append(dict(key=str(k),old_status=a[0],new_status=z[0],old_time=a[1],new_time=z[1],old_hits=a[4],new_hits=z[4],match=int(ok)))
    b.write(out/'CACHED_ROUTE_FINGERPRINT.csv',rr);assert len(rr)==24 and all(r['match'] for r in rr),'CACHED_ROUTE_FINGERPRINT_FAIL'

def route_new(base,new,cache,cuts,labels,out,workers):
    buckets=collections.defaultdict(list)
    for r in new:
        if r['weight']==0:r.update(route_status='ZERO_TARGET_MASS',duration_min='',distance_m='',max_snap_m='',hits='');continue
        buckets[key(r)].append(r)
    missing=[k for k in buckets if k not in cache];reuse=len(buckets)-len(missing);tree=STRtree(cuts)
    print('new unique keys',len(buckets),'cached',reuse,'OSRM requests',len(missing),flush=True)
    def go(k):return k,query_one(base,k,tree,labels)
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for i,(k,z) in enumerate(ex.map(go,missing),1):
            cache[k]=z
            if i%1000==0:print('new routes',i,'/',len(missing),flush=True)
    for k,rr in buckets.items():
        status,t,d,s,h=cache[k]
        for r in rr:
            st=status;hit=h
            if st=='OK' and r['component']=='local' and abs(t-r['reference_local_time_min'])>max(1.,.1*r['reference_local_time_min']):st='TABLE_ROUTE_TIME_MISMATCH';hit=''
            r.update(route_status=st,duration_min=t,distance_m=d,max_snap_m=s,hits=hit)
    b.js(out/'ROUTE_REUSE_QA.json',dict(new_unique_keys=len(buckets),cache_reused=reuse,new_OSRM_requests=len(missing)))
    return len(missing)

def precision_table(st,oldst,labels,meta,out):
    rr=[]
    def passed(v,i):return bool(v['hit'][i]>0 and v['ess'][i]>=20 and np.isfinite(v['rse'][i]) and v['rse'][i]<=.2)
    for c,m in COMPS:
        v=st[c,m];a=oldst[c,m]
        for i,s in enumerate(labels):
            rr.append(dict(target_id=s,kind=meta[i][0],road_or_bridge=meta[i][1],component=c,model=m,known_lower_share=float(v['hit'][i]),ESS=float(v['ess'][i]),RSE=float(v['rse'][i]) if np.isfinite(v['rse'][i]) else '',guard_parent_share=float(v['guard'][i]),guard_fraction_of_known_hit=float(v['guard'][i]/v['hit'][i]) if v['hit'][i]>0 else '',global_unknown_share=v['unknown'],old_share=float(a['hit'][i]),old_ESS=float(a['ess'][i]),old_RSE=float(a['rse'][i]) if np.isfinite(a['rse'][i]) else '',old_precision_pass=int(passed(a,i)),new_precision_pass=int(passed(v,i)),zero_hit_unresolved=int(v['hit'][i]==0)))
    b.write(out/'ROUTED_TARGET_SHARES.csv',rr)
    return rr

def final_reports(st,oldst,labels,meta,reg,counts,fs,plan,qa,out):
    rr=precision_table(st,oldst,labels,meta,out);reg2,fits=b.evaluation(st,labels,fs,counts,out);s0={r['site_id']:r for r in reg};rmap={r['site_id']:r for r in reg2}
    b.write(out/'PRECISION_COVERAGE_BY_ROAD.csv',[dict(road=road,fixed_n=sum(r['road']==road for r in reg2),old_strict=sum(r['strict'] for r in reg if r['road']==road),new_strict=sum(r['strict'] for r in reg2 if r['road']==road),main_inter_zero=sum(r['main_inter']<=0 for r in reg2 if r['road']==road),main_local_zero=sum(r['main_local']<=0 for r in reg2 if r['road']==road),U1_zero=sum(r['U1']<=0 for r in reg2 if r['road']==road)) for road in b.ROADS])
    # Candidate cuts are deliberately not summed; especially not S19.
    b.write(out/'BRIDGE_CANDIDATE_PRECISION.csv',[r for r in rr if r['kind']=='bridge_candidate'])
    # Keep all test rows, including zero predictions; positivity-filtered MALE is not a coverage claim.
    pp=readcsv(out/'RETROSPECTIVE_PREDICTIONS.csv');cov=[]
    for fold in ['ALL_TRAIN']+b.ROADS:
        aa=[r for r in pp if r['fold']==fold]
        cov.append(dict(fold=fold,total=len(aa),paired_positive=sum(float(r['main_pred'])>0 and float(r['U1_pred'])>0 for r in aa),main_zero=sum(float(r['main_pred'])<=0 for r in aa),U1_zero=sum(float(r['U1_pred'])<=0 for r in aa),strict=sum(int(r['strict']) for r in aa)))
    b.write(out/'PREDICTION_COVERAGE_ALL_FIXED.csv',cov)
    mass=[]
    for c,m in COMPS:
        v=st[c,m];old=oldst[c,m];a=[r for r in plan if r['component']==c and r['model']==m];ok=math.fsum(r['weight'] for r in a if r['route_status']=='OK');known0=old['draw_mass']-old['unknown']+old['omitted']+old['diag']+old['tableunk'];
        assert np.isclose(ok+v['unknown'],v['draw_mass']+v['omitted']+v['diag']+v['tableunk'],rtol=1e-10,atol=1e-12)
        mass.append(dict(component=c,model=m,rows=len(a),known_routed_mass=ok,old_known_routed_mass=known0,draw_mass=v['draw_mass'],unknown_total=v['unknown'],outside=v['omitted'],same_cell=v['diag'],table_unknown=v['tableunk'],conditional_vkt_per_unit=v['vkt']))
    b.write(out/'MASS_QA.csv',mass)
    return reg2,fits,mass

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bridges',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--base',default='http://127.0.0.1:5001');ap.add_argument('--time');ap.add_argument('--workers',type=int,default=8);ap.add_argument('--offline',action='store_true');a=ap.parse_args();out=a.out;out.mkdir(parents=True,exist_ok=True)
    gate=dict(stage='P0E',status='STARTED',model_frozen=False,new_WIM_opened=False,independent_validation=False,R1_changed=False,source_sha=OLD_SHA,seed=SEED,new_inter_draws=NEW_INTER,new_local_budget_per_sampled_parent=NEW_LOCAL,structure_changed=False,parameters_tuned_by_AADT=False)
    try:
        old,n,og,plan,qa,dist,P,cuts,labels,meta,nodes=source(out);oldst,reg,inputs=audit(old,plan,qa,dist,P,cuts,labels,meta,n,out);ids,xy,P,R,I,counts,fs=inputs
        weak=weak_targets(oldst,labels,meta,fs);b.js(out/'PRECISION_TARGETS_GEOMETRY_ONLY.json',{k:sorted(v) for k,v in weak.items()});gate.update(old_audit_pass=True,old_strict=sum(r['strict'] for r in reg),weak_inter=len(weak['inter']),weak_local=len(weak['local']))
        if a.offline:gate['status']='OFFLINE_REAL_OLD_LEDGER_AUDIT_PASS_NO_NEW_ROUTES';return 0
        # P0D's exact T, geometry, and microzones: no GHSL reread and no recalibration of weights.
        tp=b.find_pinned(a.bridges,[b.TIME_NAME],b.TIME_SHA,a.time);T,q0,q1,err=b.verify_time(tp,ids,P,R,I);gate['old_weight_max_diff']=err
        targets,cuts0,labels0,meta0,geo=b.make_targets();assert labels0==labels and meta0==meta and all(x.equals_exact(y,1e-6) for x,y in zip(cuts0,cuts)),'TARGET_CUT_GEOMETRY_CHANGED'
        b.preflight(a.base,xy,T,out);cache=cache_from_old(plan);fingerprint_cache(a.base,plan,cache,cuts,labels,out);gate['graph_and_cache_fingerprint_pass']=True
        pi,ni=build_inter(plan,oldst,weak['inter'],labels,meta,cuts,xy,nodes,targets,T,q0,q1,dist,NEW_INTER,SEED,out)
        pl,nl,lqa=build_local(a.base,plan,qa,nodes,weak['local'],labels,meta,cuts,NEW_LOCAL,SEED+1,out)
        new=ni+nl;prev=pi+pl;gate['new_rows']=len(new);gate['retained_old_rows']=len(prev);b.js(out/'GATE.json',gate)
        gate['new_unique_OSRM_requests']=route_new(a.base,new,cache,cuts,labels,out,a.workers);combined=prev+new
        assert len({(r['draw'],r['component'],r['model']) for r in combined})==len(combined)
        assert all((r['parent_o']==r['parent_d'])==(r['component']=='local') for r in combined)
        cols=list(plan[0])+['phase'];b.write(out/'ROUTE_LEDGER.csv',combined,cols)
        for file in ('TARGET_GEOMETRY.csv','TARGET_CUTS_PROJECTED_3035.json','PARENT_SCOPE_941.csv','MICROZONES.csv'):
            (out/file).write_bytes((old/file).read_bytes())
        st=b.stats(combined,labels,dist,lqa,P);reg2,fits,mass=final_reports(st,oldst,labels,meta,reg,counts,fs,combined,lqa,out)
        gate.update(status='ONE_PRECISION_PASS_COMPLETE_REVIEW_REQUIRED',strict_common_sites=sum(r['strict'] for r in reg2),strict_train_sites=sum(r['strict'] for r in reg2 if r['road'] in b.TRAIN),not_certified_national_Q=True,calibration_is_diagnostic=True,conditional_only=True,mass_qa=mass)
        return 0
    except Exception as e:
        gate.update(status='BLOCKED_OR_PARTIAL',error=str(e));traceback.print_exc();return 1
    finally:
        b.js(out/'GATE.json',gate)
        # Do not re-archive the embedded source files; source hashes are recorded separately.
        import shutil
        shutil.rmtree(out/'_frozen',ignore_errors=True)
if __name__=='__main__':sys.exit(main())
