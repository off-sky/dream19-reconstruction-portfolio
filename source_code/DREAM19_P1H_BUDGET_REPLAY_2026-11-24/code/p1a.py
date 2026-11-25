#!/usr/bin/env python3
"""Freeze the conditional P0E demand ledger; evaluate existing graph counterfactuals.
No new demand generation, AADT fit, or optimization. All positive-weight, OPEN-OK
OD keys are retained, with unaggregated weights/strata for conditional sums and RSE.
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, csv, gzip, hashlib, io, json, math, os, random, re, shutil, socket, subprocess, sys, time, traceback, urllib.request, urllib.error, zipfile
from pathlib import Path, PurePosixPath
ROOT=Path(__file__).resolve().parents[1]
SOURCE='P0E_RESULTS_20261124T155705Z.zip'
SHA='835891c4457ab54288c3e3cdf7c2c174882fd903d6c3d64228e3b7fdf6574c18'
P0E_CODE_SHA='2e3e3f93ff5dea5c7a9f327d19ba3782e1b506187f9db12d1fdd1e8c7fa74cb6'
COMPS=(('inter','MAIN'),('local','MAIN'),('inter','U1'))
FIELDS=('lon_o','lat_o','lon_d','lat_d'); SNAP=500.; POS_TOL=2.; TIME_TOL=1.
DEFAULT_SCENARIOS=[dict(scenario_id='CLOSED',restored_bridges='',graph_base='stage13e2_all19_local_results/graph/ukraine_261111_all19_closed_duration.osrm',provenance='Previously used native Stage13e2 all-19-closed graph'),dict(scenario_id='S14',restored_bridges='S14',graph_base='stage13e3_S14_restore_local_results/graph/ukraine_261111_18closed_S14_restored.osrm',provenance='Previously used native Stage13e3 S14-only-restored graph')]

def sha(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for a in iter(lambda:f.read(1024*1024),b''):h.update(a)
    return h.hexdigest()
def jwrite(p,obj):p.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False,default=str)+'\n',encoding='utf8')
def readcsv(p):
    with open(p,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def writecsv(p,rows,fields=None):
    rows=list(rows); fields=fields or (list(rows[0]) if rows else ['empty']); op=gzip.open if str(p).endswith('.gz') else open
    with op(p,'wt',encoding='utf8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
def require(ok,msg):
    if not ok:raise ValueError(msg)
def source_unpack(dest):
    src=ROOT/'frozen'/SOURCE; require(sha(src)==SHA,'FROZEN_SOURCE_SHA_MISMATCH');require(sha(ROOT/'frozen'/'DREAM19_P0E_TARGETED_PRECISION_2026-11-24.zip')==P0E_CODE_SHA,'FROZEN_CODE_SHA_MISMATCH');dest.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(src) as z:
        require(z.testzip() is None,'SOURCE_CRC_FAIL');roots=set()
        for x in z.infolist():
            p=PurePosixPath(x.filename); require(not p.is_absolute() and '..' not in p.parts and len(p.parts)>=2 and not x.is_dir(),'UNSAFE_SOURCE_MEMBER'); roots.add(p.parts[0]); q=dest.joinpath(*p.parts[1:]);q.parent.mkdir(parents=True,exist_ok=True);q.write_bytes(z.read(x))
        require(len(roots)==1,'SOURCE_ROOTS')
    manifest=json.loads((dest/'SHA256SUMS.json').read_text()); actual={p.relative_to(dest).as_posix() for p in dest.rglob('*') if p.is_file() and p.name!='SHA256SUMS.json'}
    require(set(manifest)==actual and all(sha(dest/k)==v for k,v in manifest.items()),'SOURCE_MEMBER_HASH_MISMATCH')
    gate=json.loads((dest/'GATE.json').read_text());require(gate['status']=='ONE_PRECISION_PASS_COMPLETE_REVIEW_REQUIRED' and not gate['structure_changed'] and not gate['parameters_tuned_by_AADT'],'SOURCE_STAGE_NOT_ACCEPTED')
    return len(manifest)
def key(r):return tuple(round(float(r[k]),7) for k in FIELDS)
def load_ledger(p):
    rows=readcsv(p);keys={};seen=set();tot=collections.Counter();unknown=collections.Counter();n=collections.Counter()
    for r in rows:
        c=(r['component'],r['model']);require(c in COMPS,'UNKNOWN_COMPONENT');d=(r['draw'],*c);require(d not in seen,'DUPLICATE_DRAW');seen.add(d);w=float(r['weight']);require(math.isfinite(w) and w>=0,'BAD_WEIGHT');r['weight']=w;n[c]+=1
        require((int(r['parent_o'])==int(r['parent_d']))==(c[0]=='local'),'DOUBLE_COUNT_PARENT')
        tot[c]+=w
        if r['route_status']!='OK':unknown[c]+=w;continue
        if w<=0:continue
        k=key(r); t=float(r['duration_min'])*60; snap=float(r['max_snap_m']);require(math.isfinite(t) and t>0 and 0<=snap<=SNAP,'BAD_OPEN_RECORD');r['key']=k
        if k in keys:
            require(abs(keys[k]['time']-t)<1e-7 and abs(keys[k]['max_snap']-snap)<1e-6,'CONFLICTING_OPEN_KEY')
        else:keys[k]=dict(time=t,max_snap=snap,hits=r.get('hits',''))
    return rows,keys,tot,unknown,n

def fmt(x):return f'{x[0]:.7f},{x[1]:.7f}'
def request(url,tries=2,timeout=45):
    last='UNKNOWN'
    for i in range(tries):
        try:
            with urllib.request.urlopen(url,timeout=timeout) as f:return json.load(f)
        except urllib.error.HTTPError as e:
            try:
                j=json.loads(e.read());
                if isinstance(j,dict) and 'code' in j:return j
            except Exception:pass
            last=f'HTTP_{e.code}'
        except Exception as e:last=type(e).__name__
        if i+1<tries:time.sleep(.1)
    return {'code':'REQUEST_FAILED','detail':last}
def point(w):
    p=w.get('location');d=w.get('distance');require(isinstance(p,list) and len(p)==2,'BAD_WAYPOINT');p=tuple(map(float,p));d=float(d);require(all(math.isfinite(x) for x in p+(d,)) and d>=0 and abs(p[0])<=180 and abs(p[1])<=90,'BAD_WAYPOINT');return p,d
def result(status,seconds=None,a=None,b=None,mode=''):
    return dict(status=status,seconds=seconds,origin=a,destination=b,mode=mode)
def valid_time(t):return t is not None and math.isfinite(float(t)) and float(t)>0

def pair_route(base,k):
    j=request(f'{base}/route/v1/driving/{fmt(k[:2])};{fmt(k[2:])}?overview=false&steps=false&alternatives=false&annotations=false')
    code=j.get('code','BAD_RESPONSE')
    if code!='Ok':return result('UNREACHABLE_UNCONFIRMED' if code=='NoRoute' else code,mode='ROUTE')
    try:
        a,b=map(point,j['waypoints']);t=float(j['routes'][0]['duration']);return result('OK' if valid_time(t) and max(a[1],b[1])<=SNAP else 'BAD_SNAP_OR_DURATION',t,a,b,'ROUTE')
    except Exception:return result('MALFORMED_ROUTE',mode='ROUTE')
def table_group(base,group):
    # A single common origin, explicit destination indices. Avoid N*N waste.
    require(len(group)>0 and len({k[:2] for k in group})==1,'BAD_GROUP'); coords=';'.join([fmt(group[0][:2])]+[fmt(k[2:]) for k in group]); di=';'.join(map(str,range(1,len(group)+1)))
    j=request(f'{base}/table/v1/driving/{coords}?sources=0&destinations={di}&annotations=duration')
    if j.get('code')!='Ok':return {k:result('TABLE_'+str(j.get('code','BAD_RESPONSE')),mode='TABLE') for k in group}
    try:
        a=point(j['sources'][0]);v=j['durations'][0];ds=j['destinations'];require(len(v)==len(ds)==len(group),'TABLE_LENGTH')
        out={}
        for k,t,b0 in zip(group,v,ds):
            b=point(b0);st='OK' if valid_time(t) else 'TABLE_NULL' if t is None else 'BAD_DURATION'
            if max(a[1],b[1])>SNAP:st='BAD_SNAP_OR_DURATION'
            out[k]=result(st,float(t) if valid_time(t) else None,a,b,'TABLE')
        return out
    except Exception:return {k:result('MALFORMED_TABLE',mode='TABLE') for k in group}
def metres(a,b):
    a,b=a[0],b[0];p1,p2=map(math.radians,(a[1],b[1]));dlat=p2-p1;dlon=math.radians(b[0]-a[0]);h=math.sin(dlat/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dlon/2)**2;return 6371008.8*2*math.asin(min(1,math.sqrt(max(0,h))))
def same_endpoints(a,b):
    return all(a.get(k) is not None and b.get(k) is not None and metres(a[k],b[k])<=POS_TOL for k in ('origin','destination'))
def matches_ledger(r,ref):
    return r['status']=='OK' and abs(r['seconds']-ref['time'])<=TIME_TOL and abs(max(r['origin'][1],r['destination'][1])-ref['max_snap'])<=1.
def open_one(base,k,r,ref):
    if not matches_ledger(r,ref):r=pair_route(base,k)
    if not matches_ledger(r,ref):r['status']='OPEN_LEDGER_MISMATCH';return r
    return r

def scenario_one(base,k,r,op):
    if op['status']!='OK':return result('OPEN_UNPAIRED')
    if op['mode']=='ROUTE' or r['status'] not in ('OK','TABLE_NULL') or not same_endpoints(r,op) or (r['status']=='OK' and r['seconds']<op['seconds']-TIME_TOL):
        r=pair_route(base,k)
    elif r['status']=='TABLE_NULL':
        # A null with unchanged valid table snaps is checked using a dedicated route query.
        q=pair_route(base,k)
        if q['status']=='UNREACHABLE_UNCONFIRMED':return result('UNREACHABLE',None,r['origin'],r['destination'],'TABLE_NULL_AND_ROUTE_NOROUTE')
        r=q
    if r['status']=='UNREACHABLE_UNCONFIRMED':return result('UNREACHABLE_UNCONFIRMED',mode=r['mode'])
    if r['status']!='OK':return r
    if not same_endpoints(r,op):r['status']='SNAP_LOCATION_CHANGED';return r
    if r['seconds']<op['seconds']-TIME_TOL:r['status']='FASTER_THAN_OPEN';return r
    return r

def witness_set(keys):
    # Geometry-based witnesses, not AADT; terminal-node evidence is not identity certification.
    refs=readcsv(ROOT/'inputs'/'13b_candidate_way_QA.csv'); lookup={}
    for r in refs:
        tag=r['bridge_id']+'_WAY_'+r['candidate_way_id'];lookup[tag]=r
    hits=collections.defaultdict(list)
    for k,v in keys.items():
        for tag in set(filter(None,v['hits'].split(';'))):
            if tag in lookup:hits[tag].append(k)
    out=[]
    for tag,r in sorted(lookup.items()):
        g=sorted(hits[tag]); rng=random.Random(20261124+int(r['candidate_way_id']));pick=rng.sample(g,min(2,len(g)))
        for k in pick:out.append((tag,r,k))
    return out

def bridge_witnesses(base,witnesses,sid):
    out=[]
    for tag,r,k in witnesses:
        j=request(f'{base}/route/v1/driving/{fmt(k[:2])};{fmt(k[2:])}?overview=false&steps=false&alternatives=false&annotations=nodes');status=j.get('code','BAD_RESPONSE');nodes=[];duration=None;max_snap=None
        if status=='Ok':
            try:
                route=j['routes'][0];duration=float(route['duration']);max_snap=max(point(w)[1] for w in j['waypoints']);nodes=[int(n) for leg in route['legs'] for n in leg['annotation']['nodes']]
            except Exception:status='MALFORMED_ANNOTATION'
        u=int(r['endpoint_start_node_id']);v=int(r['endpoint_end_node_id']);both=int(u in nodes and v in nodes) if nodes else None;adj=int(any((x,y) in ((u,v),(v,u)) for x,y in zip(nodes,nodes[1:]))) if nodes and int(r['way_nodes_count'])==2 else None
        out.append(dict(scenario_id=sid,target_id=tag,**dict(zip(FIELDS,k)),route_status=status,duration_seconds=duration,max_snap_m=max_snap,annotation_nodes=len(nodes),both_candidate_terminal_nodes=both,two_node_way_direct_edge=adj,interpretation='CANDIDATE_NODE_DIAGNOSTIC_ONLY_NOT_IDENTITY_CERTIFICATION'))
    return out

def groups(keys,chunk):
    by=collections.defaultdict(list)
    for k in sorted(keys):by[k[:2]].append(k)
    return [rr[i:i+chunk] for rr in by.values() for i in range(0,len(rr),chunk)]
def evaluate(base,keys,chunk,workers,label,op=None):
    use=keys if op is None else {k:v for k,v in keys.items() if op[k]['status']=='OK'}; gg=groups(use,chunk);out={}; print(label,'OD keys',len(use),'table batches',len(gg),flush=True)
    def run(g):
        batch=table_group(base,g)
        return {k:(open_one(base,k,r,keys[k]) if op is None else scenario_one(base,k,r,op[k])) for k,r in batch.items()}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for i,dd in enumerate(ex.map(run,gg),1):
            out.update(dd)
            if i%500==0 or i==len(gg):print(label,'batches',i,'/',len(gg),flush=True)
    if op is not None:
        for k in keys:
            if k not in out:out[k]=result('OPEN_UNPAIRED')
    require(set(out)==set(keys),'PAIR_KEY_COVERAGE');return out

def flatten(k,r):
    a,b=r.get('origin'),r.get('destination');return dict(zip(FIELDS,k))|dict(status=r['status'],seconds=r.get('seconds'),mode=r.get('mode'),snap_o_lon=a[0][0] if a else '',snap_o_lat=a[0][1] if a else '',snap_o_m=a[1] if a else '',snap_d_lon=b[0][0] if b else '',snap_d_lat=b[0][1] if b else '',snap_d_m=b[1] if b else '')
def unflatten(r):
    def get(x):return ((float(r[f'snap_{x}_lon']),float(r[f'snap_{x}_lat'])),float(r[f'snap_{x}_m'])) if r[f'snap_{x}_lon']!='' else None
    return result(r['status'],float(r['seconds']) if r['seconds']!='' else None,get('o'),get('d'),r['mode'])
def load_cache(p,expected,keys):
    m=p.with_suffix('.json');
    if not p.is_file() or not m.is_file():return None
    try:
        d=json.loads(m.read_text());require(d['fingerprint']==expected and d['sha']==sha(p),'CACHE_HASH');
        with gzip.open(p,'rt',encoding='utf8',newline='') as f:r={key(x):unflatten(x) for x in csv.DictReader(f)}
        require(set(r)==set(keys),'CACHE_KEYS');return r
    except Exception:return None
def save_cache(p,fp,rs):
    writecsv(p,(flatten(k,r) for k,r in rs.items()));jwrite(p.with_suffix('.json'),dict(fingerprint=fp,sha=sha(p)))
def status_summary(rs):return dict(collections.Counter(x['status'] for x in rs.values()))

def graph_parts(p):
    # Native MLD has .osrm.* files; .osrm base need not itself exist.
    require(p.name.endswith('.osrm'),'BAD_GRAPH_BASE'); require(Path(str(p)+'.mldgr').is_file() and Path(str(p)+'.cells').is_file(),'MLD_GRAPH_MISSING '+str(p));
    return sorted(q for q in p.parent.glob(p.name+'*') if q.is_file() and (q.name==p.name or q.name.startswith(p.name+'.')) and not q.name.endswith(('.log','.txt','.json','.csv','.md')))
def graph_manifest(p):return {q.name:{'bytes':q.stat().st_size,'sha256':sha(q)} for q in graph_parts(p)}
def free_port(port):
    with socket.socket() as s:return s.connect_ex(('127.0.0.1',port))!=0
class NativeGraph:
    def __init__(self,path,port,bin,out):self.path=path;self.port=port;self.bin=bin;self.out=out;self.proc=None
    def __enter__(self):
        require(free_port(self.port),'SCENARIO_PORT_OCCUPIED: '+str(self.port));self.log=open(self.out,'w');self.proc=subprocess.Popen([self.bin,'--algorithm','mld','--port',str(self.port),str(self.path)],stdout=self.log,stderr=subprocess.STDOUT);base=f'http://127.0.0.1:{self.port}'
        try:
            for _ in range(180):
                if self.proc.poll() is not None:raise RuntimeError('OSRM_START_FAILED '+str(self.out))
                if not free_port(self.port):return base
                time.sleep(.5)
            raise RuntimeError('OSRM_START_TIMEOUT '+str(self.out))
        except BaseException:self.__exit__(None,None,None);raise
    def __exit__(self,*args):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        self.log.close()

def read_scenarios(p):
    rr=readcsv(p) if p else [dict(r) for r in DEFAULT_SCENARIOS];ids=set()
    for r in rr:
        sid=r['scenario_id'];require(re.fullmatch(r'[A-Za-z0-9_-]{1,60}',sid) and sid!='OPEN' and sid not in ids,'INVALID_SCENARIO_ID');ids.add(sid);require(r['graph_base'].endswith('.osrm') and r['provenance'].strip(),'SCENARIO_PROVENANCE_REQUIRED');
        bs=[x.strip() for x in r.get('restored_bridges','').split(';') if x.strip()];require(all(re.fullmatch(r'S(?:0[1-9]|1[0-9])',x) for x in bs) and len(set(bs))==len(bs),'BAD_RESTORED_SET');require((sid=='CLOSED')==(len(bs)==0),'CLOSED_SET_CONFLICT')
    require('CLOSED' in ids,'CLOSED_REQUIRED');return sorted(rr,key=lambda r:(r['scenario_id']!='CLOSED',r['scenario_id']))
def discover_graphs(bridges,out):
    rr=[]
    for p in sorted(bridges.rglob('*.osrm.mldgr')):
        b=Path(str(p)[:-6]);rr.append(dict(graph_base=str(b.relative_to(bridges)),cells_exists=int(Path(str(b)+'.cells').is_file()),mldgr_bytes=p.stat().st_size,identity='LOCAL_FILENAME_ONLY_NOT_SCENARIO_VERIFICATION'))
    writecsv(out/'SCENARIO_GRAPH_INVENTORY.csv',rr)
    return rr

def collect_prior(bridges,out):
    # Copy only compact prior economic/scenario provenance; never new AADT/WIM files.
    names={'DREAM19_STAGE13E9T_MINIMAX_REGRET_2026-11-18.zip','DREAM19_STAGE13E9D_RESULTS_20261116T150808Z.zip','DREAM19_STAGE13E9D_BOUNDS_CHECKPOINT_2026-11-16.zip','DREAM19_STAGE13E9G_ECONOMIC_REFRAMING_2026-11-17.zip','DREAM19_STAGE13E9B_VERIFIED_ADDENDUM_2026-11-16.zip','DREAM19_STAGE13E9B_SMALL_RESULTS(3).zip'}; rec=[];budget=20*1024*1024
    with zipfile.ZipFile(out/'PRIOR_FROZEN_PORTFOLIO_METADATA.zip','w',zipfile.ZIP_DEFLATED) as zz:
        for n in sorted(names):
            matches=sorted(bridges.rglob(n));
            if not matches:rec.append(dict(archive=n,status='NOT_FOUND',member=''));continue
            if len(matches)>1:
                hashes={sha(p) for p in matches};
                if len(hashes)>1:rec.append(dict(archive=n,status='AMBIGUOUS',member=''));continue
            p=matches[0];h=sha(p)
            try:
                with zipfile.ZipFile(p) as z:
                    for i in z.infolist():
                        m=PurePosixPath(i.filename);name=m.name.lower();
                        if i.is_dir() or m.is_absolute() or '..' in m.parts or i.file_size>2*1024*1024 or i.file_size>budget:continue
                        if m.suffix.lower() not in ('.csv','.json','.md','.tsv','.txt'):continue
                        if not re.search(r'portfolio|capex|scenario|budget|regret|manifest|bridge_registry|r1|qa|sha256',name) or re.search(r'aadt|wim|count|draw|route_ledger',name):continue
                        zz.writestr(n+'/'+i.filename,z.read(i));budget-=i.file_size;rec.append(dict(archive=n,status='COPIED_METADATA',member=i.filename,source_sha=h,bytes=i.file_size))
            except (zipfile.BadZipFile,OSError) as e:rec.append(dict(archive=n,status='READ_ERROR',member='',detail=str(e)))
    writecsv(out/'PRIOR_PORTFOLIO_METADATA_INDEX.csv',rec,fields=['archive','status','member','source_sha','bytes','detail'])

def outcome(o,c,s):
    # Compare CLOSED versus restored on identical endpoints, with OPEN as monotonic reference.
    if o['status']!='OK':return 'OPEN_UNPAIRED',None
    if c['status'] not in ('OK','UNREACHABLE'):return 'CLOSED_UNRESOLVED',None
    if s['status'] not in ('OK','UNREACHABLE'):return 'SCENARIO_UNRESOLVED',None
    if c['status']=='UNREACHABLE':return ('CONNECTIVITY_RESTORED' if s['status']=='OK' else 'BOTH_UNREACHABLE'),None
    if s['status']=='UNREACHABLE':return 'NESTING_VIOLATION',None
    if s['seconds']>c['seconds']+TIME_TOL or s['seconds']<o['seconds']-TIME_TOL:return 'NESTING_VIOLATION',None
    # tolerance represents rounding; never invent positive savings from negative numeric noise.
    return 'FINITE_PAIRED',max(0.,c['seconds']-s['seconds'])/60

def aggregate(rows,op,closed,scenarios,massqa,scope):
    result_rows=[];scale_base={};nkeys=len(op); ids=list(scenarios)
    outcomes={sid:{k:outcome(op[k],closed[k],s[k]) for k in op} for sid,s in scenarios.items()}; common={k for k in op if all(outcomes[sid][k][0]=='FINITE_PAIRED' for sid in ids)}
    for sid,oset in outcomes.items():
        for comp,model in COMPS:
            rr=[r for r in rows if (r['component'],r['model'])==(comp,model)]; masses=collections.Counter();strata=collections.defaultdict(lambda:[0,0.,0.,False]);dmin=0.;common_min=0.;guard_min=0.
            for r in rr:
                w=r['weight'];st= str(r['stratum']);acc=strata[st];acc[0]+=1;acc[3]=r['sampling']=='EXACT'
                if w<=0:continue
                if r['route_status']!='OK':masses['BASE_OPEN_UNROUTED']+=w;continue
                k=r['key'];status,dt=oset[k];masses[status]+=w
                if status=='FINITE_PAIRED':
                    x=w*dt;dmin+=x;acc[1]+=x;acc[2]+=x*x
                    if k in common:common_min+=x
                    if comp=='local' and scope.get(int(r['parent_o']))=='GUARD':guard_min+=x
            variance=sum(max(0,(n*s2-s1*s1)/(n-1)) for n,s1,s2,exact in strata.values() if n>1 and not exact)
            m=massqa[(comp,model)]; outside=float(m['outside']);diag=float(m['same_cell']);unk=float(m['table_unknown']);known=float(m['known_routed_mass']);finite=masses['FINITE_PAIRED'];unresolved=sum(v for k,v in masses.items() if k not in ('FINITE_PAIRED','CONNECTIVITY_RESTORED','BOTH_UNREACHABLE'))+outside+diag+unk
            expected=float(m['draw_mass'])+outside+diag+unk;require(abs(sum(masses.values())+outside+diag+unk-expected)<1e-8,'MASS_ACCOUNTING_FAILURE')
            rec=dict(scenario_id=sid,component=comp,model=model,known_open_mass=known,paired_finite_mass=finite,connectivity_restored_mass=masses['CONNECTIVITY_RESTORED'],both_unreachable_mass=masses['BOTH_UNREACHABLE'],unresolved_total_mass=unresolved,base_open_unrouted_mass=masses['BASE_OPEN_UNROUTED'],open_unpaired_mass=masses['OPEN_UNPAIRED'],closed_unresolved_mass=masses['CLOSED_UNRESOLVED'],scenario_unresolved_mass=masses['SCENARIO_UNRESOLVED'],nesting_violation_mass=masses['NESTING_VIOLATION'],outside_local_mass=outside,same_cell_local_mass=diag,table_unknown_local_mass=unk,finite_minutes_per_component_unit=dmin,mc_se_minutes=math.sqrt(variance),mc_RSE=math.sqrt(variance)/dmin if dmin>0 else '',common_finite_minutes_per_component_unit=common_min,guard_local_minutes_per_unit=guard_min,draw_mass_plus_structural_reserve=expected,status='CONDITIONAL_PAIRED_SAMPLE_NOT_NATIONAL_BENEFIT')
            result_rows.append(rec);scale_base[sid,comp,model]=rec
    return result_rows,scale_base,outcomes,common

def scale_scenarios(fits,base,scenarios):
    rr=[];seen=set()
    for f in fits:
        qi,ql,qu=(float(f[k]) for k in ('Q_inter','Q_local','Q_U1')); keyq=(qi,ql,qu)
        if keyq in seen:continue
        seen.add(keyq)
        for sid in scenarios:
            i=base[sid,'inter','MAIN'];l=base[sid,'local','MAIN'];u=base[sid,'inter','U1'];day=(qi*i['finite_minutes_per_component_unit']+ql*l['finite_minutes_per_component_unit'])/60;common=(qi*i['common_finite_minutes_per_component_unit']+ql*l['common_finite_minutes_per_component_unit'])/60
            rr.append(dict(scenario_id=sid,scale_scenario=f['fold'],Q_inter=qi,Q_local=ql,Q_U1=qu,local_share=ql/(qi+ql),MAIN_vehicle_hours_day=day,MAIN_common_vehicle_hours_day=common,MAIN_annual_vehicle_hours_365day_assumption=day*365,U1_vehicle_hours_day=qu*u['finite_minutes_per_component_unit']/60,MAIN_connectivity_restored_trips_day=qi*i['connectivity_restored_mass']+ql*l['connectivity_restored_mass'],MAIN_unresolved_model_mass_scaled_trips_day=qi*i['unresolved_total_mass']+ql*l['unresolved_total_mass'],status='ILLUSTRATIVE_SCALE_NOT_CALIBRATED_NATIONAL_FLOW_NO_MONETIZATION'))
    return rr

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bridges',required=True,type=Path);ap.add_argument('--out',required=True,type=Path);ap.add_argument('--open-url',default='http://127.0.0.1:5001');ap.add_argument('--scenario-port',type=int,default=5002);ap.add_argument('--osrm-bin',default='osrm-routed');ap.add_argument('--scenario-map',type=Path);ap.add_argument('--workers',type=int,default=8);ap.add_argument('--chunk',type=int,default=60);ap.add_argument('--offline',action='store_true');a=ap.parse_args();o=a.out;o.mkdir(parents=True,exist_ok=True);frozen=o/'_frozen';gate=dict(stage='P1A',status='STARTED',demand_structure_frozen=True,absolute_scale_validated=False,external_validation=False,portfolio_algorithm_changed=False,source_sha=SHA,scenario_graph_identity='PREVIOUS_NATIVE_PATH_PLUS_HASH_AND_ROUTE_QA_NOT_FULL_CERTIFICATION');results={};paths=[]
    try:
        require(1<=a.workers<=32 and 1<=a.chunk<=90,'BAD_EXECUTION_SETTINGS');require(a.open_url=='http://127.0.0.1:5001','OPEN_URL_MUST_BE_FROZEN_LOCAL_5001');require(a.scenario_port!=5001 and a.scenario_port!=5000,'DO_NOT_USE_OPEN_OR_CONTROL_CENTER_PORT')
        n=source_unpack(frozen);rows,keys,tot,unk,counts=load_ledger(frozen/'ROUTE_LEDGER.csv');require(len(rows)==218354 and len(keys)==143076,'FROZEN_LEDGER_COUNT_CHANGED');massqa={(r['component'],r['model']):r for r in readcsv(frozen/'MASS_QA.csv')};scope={int(r['parent']):r['scope'] for r in readcsv(frozen/'PARENT_SCOPE_941.csv')}; fits=readcsv(frozen/'RETROSPECTIVE_FIT.csv');
        for cm in COMPS:
            require(abs(tot[cm]-float(massqa[cm]['draw_mass']))<1e-8 and abs(tot[cm]-unk[cm]-float(massqa[cm]['known_routed_mass']))<1e-8,'FROZEN_MASS_CHANGED')
        gate.update(manifest_items=n,frozen_rows=len(rows),accepted_unique_keys=len(keys),frozen_geometry_unchanged=True);jwrite(o/'GATE.json',gate)
        for name in ('MASS_QA.csv','RETROSPECTIVE_METRICS.csv','RETROSPECTIVE_FIT.csv','FIXED_EVAL_SUPPORT.csv','PRECISION_COVERAGE_BY_ROAD.csv','BRIDGE_CANDIDATE_PRECISION.csv'):
            shutil.copy2(frozen/name,o/name)
        scenarios=read_scenarios(a.scenario_map);writecsv(o/'REQUESTED_SCENARIOS.csv',scenarios);discover_graphs(a.bridges,o);collect_prior(a.bridges,o);witnesses=witness_set(keys);gate['candidate_witness_pairs']=len(witnesses)
        if a.offline:gate['status']='FROZEN_LEDGER_AND_SOURCE_QA_PASS_NO_NEW_COUNTERFACTUAL_ROUTES';return 0
        # Fail early when the indispensable previous CLOSED graph is absent.
        cp=Path(scenarios[0]['graph_base']);cp=(cp if cp.is_absolute() else a.bridges/cp).resolve();require(cp.is_relative_to(a.bridges.resolve()),'GRAPH_OUTSIDE_BRIDGES');graph_parts(cp)
        require(shutil.which(a.osrm_bin) or Path(a.osrm_bin).is_file(),'OSRM_ROUTED_BINARY_NOT_FOUND');require(free_port(a.scenario_port),'SCENARIO_PORT_OCCUPIED')
        # Pin all previously accepted OD keys and explicitly re-observe OPEN snap locations.
        # No use of OPEN cut hits to omit any route.
        cache=a.bridges/'P1A_FROZEN_ROUTE_CACHE';cache.mkdir(exist_ok=True);source_fp=dict(source_sha=SHA,runner_sha=sha(Path(__file__)),position_tolerance_m=POS_TOL,time_tolerance_s=TIME_TOL,snap_limit_m=SNAP,route_mode='TABLE_ONE_ORIGIN_THEN_PAIR_FALLBACK_V1')
        # Live check of representative accepted routes before reusing an OPEN cache.
        rng=random.Random(2026112420);sample=rng.sample(sorted(keys),24); fp=[]
        for k in sample:
            r=pair_route(a.open_url,k);ok=matches_ledger(r,keys[k]);fp.append(dict(zip(FIELDS,k))|dict(ledger_s=keys[k]['time'],live_s=r['seconds'],match=int(ok),status=r['status']))
        writecsv(o/'OPEN_FINGERPRINT_24.csv',fp);require(all(r['match'] for r in fp),'OPEN_FINGERPRINT_FAIL')
        ocache=cache/'OPEN.csv.gz';op=load_cache(ocache,source_fp,keys)
        if op is None:op=evaluate(a.open_url,keys,a.chunk,a.workers,'OPEN');save_cache(ocache,source_fp,op)
        # Always include actual canonical OPEN endpoint snaps and statuses in result archive.
        writecsv(o/'OPEN_PAIR_RESULTS.csv.gz',(flatten(k,r) for k,r in op.items()));gate['open_status']=status_summary(op);jwrite(o/'GATE.json',gate)
        binary=shutil.which(a.osrm_bin) or (a.osrm_bin if Path(a.osrm_bin).is_file() else None);require(binary,'OSRM_ROUTED_BINARY_NOT_FOUND')
        gqa=[];processed={};witness_rows=bridge_witnesses(a.open_url,witnesses,'OPEN');writecsv(o/'BRIDGE_ROUTE_WITNESSES.csv',witness_rows)
        for r in scenarios:
            sid=r['scenario_id'];p=Path(r['graph_base']);p=(p if p.is_absolute() else a.bridges/p).resolve()
            try:
                require(p.is_relative_to(a.bridges.resolve()),'GRAPH_OUTSIDE_BRIDGES');parts=graph_manifest(p);gfp=source_fp|dict(open_cache_sha=sha(ocache),scenario_id=sid,restored_bridges=r['restored_bridges'],graph_files=parts,osrm_routed_binary_sha256=sha(Path(binary)));jwrite(o/f'GRAPH_{sid}_SHA256.json',dict(graph_base=str(p),provenance=r['provenance'],**gfp));pc=cache/(sid+'.csv.gz');z=load_cache(pc,gfp,keys)
                with NativeGraph(p,a.scenario_port,binary,o/f'OSRM_{sid}.log') as base:
                    witness_rows.extend(bridge_witnesses(base,witnesses,sid));writecsv(o/'BRIDGE_ROUTE_WITNESSES.csv',witness_rows)
                    if z is None:
                        z=evaluate(base,keys,a.chunk,a.workers,sid,op);save_cache(pc,gfp,z)
                processed[sid]=z;writecsv(o/f'{sid}_PAIR_RESULTS.csv.gz',(flatten(k,v) for k,v in z.items()));gqa.append(dict(scenario_id=sid,status='ROUTED_CONDITIONAL',graph_base=str(p),status_counts=json.dumps(status_summary(z))))
            except Exception as ex:
                print(sid,'BLOCKED:',ex,flush=True);gqa.append(dict(scenario_id=sid,status='BLOCKED',graph_base=str(p),status_counts=str(ex)))
                if sid=='CLOSED':break
            writecsv(o/'SCENARIO_EXECUTION_QA.csv',gqa);gate['scenario_status']=gqa;jwrite(o/'GATE.json',gate)
        writecsv(o/'SCENARIO_EXECUTION_QA.csv',gqa);gate['scenario_status']=gqa;require('CLOSED' in processed,'CLOSED_GRAPH_OR_ROUTING_BLOCKED')
        closed=processed['CLOSED'];sc={'OPEN':op}|{s:z for s,z in processed.items() if s!='CLOSED'}
        rr,base,outs,common=aggregate(rows,op,closed,sc,massqa,scope);writecsv(o/'SCENARIO_COMPONENT_BENEFITS.csv',rr);writecsv(o/'SCALE_SCENARIO_VEHICLE_HOURS.csv',scale_scenarios(fits,base,sc));writecsv(o/'PAIR_CONTRAST_STATUS.csv.gz',(dict(zip(FIELDS,k))|dict(scenario_id=sid,status=st,delta_minutes=d) for sid,oo in outs.items() for k,(st,d) in oo.items()))
        gate.update(status='CONDITIONAL_COUNTERFACTUAL_ROUTING_COMPLETE_REVIEW_REQUIRED' if len(processed)==len(scenarios) else 'PARTIAL_COUNTERFACTUAL_ROUTING_REVIEW_REQUIRED',scenarios_computed=list(sc),common_finite_unique_keys=len(common),no_national_benefit_claim=True,no_portfolio_choice_claim=True,graph_identity_requires_review=True);return 0
    except Exception as ex:gate.update(status='BLOCKED_OR_PARTIAL',error=str(ex));traceback.print_exc();return 1
    finally:jwrite(o/'GATE.json',gate);shutil.rmtree(frozen,ignore_errors=True)
if __name__=='__main__':sys.exit(main())
