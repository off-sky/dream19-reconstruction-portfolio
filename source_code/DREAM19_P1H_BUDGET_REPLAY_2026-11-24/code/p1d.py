#!/usr/bin/env python3
"""Population-per-historical-cost comparator + time-only economic sensitivity.
P0E/P1C are immutable. Only the population-only comparator may require a new graph.
"""
from __future__ import annotations
import argparse, collections, csv, json, math, random, shutil, sys, traceback, zipfile
from decimal import Decimal as D
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.windows import Window, from_bounds
from pyproj import Transformer, Geod
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import transform
import p1a as p
import p1b as b
ROOT=p.ROOT
P1B_SHA='e6d2878f6dc10e4f7697c1a9532dc63893ad0176bab0f05f5ce8fd5b4a532f23'
P1B_ZIP='P1B_FIX1_RESULTS_20261124T163244Z.zip'
BASE='SIMPLE_POP20'
RADIUS=20000
VOTS=(50,100,200)
HORIZONS=(10,20,30)
RATES=(.03,.05,.08)
WGS_M=Transformer.from_crs(4326,3035,always_xy=True)
M_WGS=Transformer.from_crs(3035,4326,always_xy=True)
WGS_R=Transformer.from_crs(4326,'ESRI:54009',always_xy=True)
GEOD=Geod(ellps='WGS84')

def centers(way):
    fs=json.loads((ROOT/'inputs/13b_bridge_candidate_lines.geojson').read_text())['features']
    out=collections.defaultdict(dict)
    for f in fs:
        x=f['properties']; bid=x['bridge_id']; wid=int(x['way_id'])
        if wid not in way[bid]:continue
        p.require(wid not in out[bid],'DUPLICATE_WAY')
        g=transform(WGS_M.transform,shape(f['geometry']));p.require(g.length>0,'EMPTY_WAY')
        pt=g.interpolate(.5,normalized=True);out[bid][wid]=(pt.x,pt.y)
    p.require(set(out)==set(way) and all(set(out[s])==way[s] for s in way),'WAY_CENTER_CROSSWALK')
    return {s:M_WGS.transform(*np.mean(list(v.values()),axis=0)) for s,v in out.items()}

def circle(lon,lat):
    # Geodesic 20km circle sampled every degree; raster-centre inclusion.
    lo,la,_=GEOD.fwd(np.full(360,lon),np.full(360,lat),np.arange(360,dtype=float),np.full(360,RADIUS))
    return transform(WGS_R.transform,Polygon(zip(lo,la)))

def resolve_pop(bridges):
    m=json.loads((ROOT/'inputs/RASTER_SHA256.json').read_text());ret=[]
    for name,h in sorted(m.items()):
        q=Path(name);n=q.name
        if not n.startswith('GHS_POP_'):continue
        preferred=bridges/'stage12d_local_results'/'ghsl_tiles'/n
        options=[preferred] if preferred.is_file() else sorted(bridges.rglob(n))
        valid=[x for x in options if x.is_file() and p.sha(x)==h]
        p.require(valid,'MISSING_OR_CHANGED_POP_TILE '+n);ret.append(valid[0])
    p.require(len(ret)==5,'FIVE_POP_TILES_REQUIRED');return ret

def raster_population(poly,paths):
    """Sum known GHSL pixels once; NoData explicitly excluded and counted, not filled."""
    total=0.;inside=known=0;bounds=[];grid=None
    for path in paths:
        with rasterio.open(path) as ds:
            p.require(ds.crs==rasterio.crs.CRS.from_string('ESRI:54009') and abs(ds.transform.a-100)<1e-8 and abs(ds.transform.e+100)<1e-8 and ds.transform.b==ds.transform.d==0,'RASTER_GRID')
            tr=ds.transform
            if grid is None:grid=tr
            p.require(abs((tr.c-grid.c)/100-round((tr.c-grid.c)/100))<1e-8 and abs((tr.f-grid.f)/100-round((tr.f-grid.f)/100))<1e-8,'TILE_ALIGNMENT')
            bb=ds.bounds
            for ob in bounds:p.require(min(bb.right,ob.right)<=max(bb.left,ob.left) or min(bb.top,ob.top)<=max(bb.bottom,ob.bottom),'OVERLAPPING_TILES')
            bounds.append(bb);x0,y0,x1,y1=poly.bounds
            if x1<=bb.left or x0>=bb.right or y1<=bb.bottom or y0>=bb.top:continue
            w=from_bounds(max(x0,bb.left),max(y0,bb.bottom),min(x1,bb.right),min(y1,bb.top),tr)
            c0=max(0,math.floor(w.col_off));r0=max(0,math.floor(w.row_off));c1=min(ds.width,math.ceil(w.col_off+w.width));r1=min(ds.height,math.ceil(w.row_off+w.height))
            if c1<=c0 or r1<=r0:continue
            w=Window(c0,r0,c1-c0,r1-r0);a=ds.read(1,window=w,masked=True);msk=geometry_mask([mapping(poly)],out_shape=a.shape,transform=ds.window_transform(w),invert=True,all_touched=False)
            good=msk&~np.ma.getmaskarray(a)&np.isfinite(a.data)&(a.data>=0);inside+=int(msk.sum());known+=int(good.sum());total+=float(a.data[good].astype('float64').sum())
    p.require(grid is not None,'NO_TILES')
    w=from_bounds(*poly.bounds,grid);c0=math.floor(w.col_off);r0=math.floor(w.row_off);c1=math.ceil(w.col_off+w.width);r1=math.ceil(w.row_off+w.height)
    tr=grid*rasterio.Affine.translation(c0,r0)
    expected=int(geometry_mask([mapping(poly)],out_shape=(r1-r0,c1-c0),transform=tr,invert=True,all_touched=False).sum())
    p.require(expected>0 and inside==expected and 0<known<=inside,'INCOMPLETE_TILE_COVERAGE_OR_NO_KNOWN_POP')
    return dict(pop_known=total,pixels_expected=expected,pixels_known=known,pixels_nodata=inside-known,nodata_fraction=(inside-known)/inside)

def greedy(pop,cost,budget):
    p.require(set(pop)==set(cost) and all(math.isfinite(float(x)) and float(x)>=0 for x in pop.values()) and all(x>0 for x in cost.values()),'GREEDY_INPUT')
    order=sorted(cost,key=lambda s:(-D(str(pop[s]))/cost[s],s));sel=set();spent=D(0);rows=[]
    for i,s in enumerate(order,1):
        take=spent+cost[s]<=budget
        if take:spent+=cost[s];sel.add(s)
        rows.append(dict(rank=i,bridge_id=s,pop_known=pop[s],cost_UAH=str(cost[s]),people_per_million_UAH=float(D(str(pop[s]))/cost[s]*D(1000000)),selected=int(take),cumulative_cost_UAH=str(spent)))
    p.require(spent<=budget and all(spent+cost[s]>budget for s in set(cost)-sel),'GREEDY_BUDGET')
    return sel,spent,rows

def preview(micro,cent,cost,budget,out):
    rows=p.readcsv(micro);xy=np.array(WGS_M.transform([float(x['lon']) for x in rows],[float(x['lat']) for x in rows])).T;pop=np.array([float(x['pop']) for x in rows]);a={s:float(pop[np.linalg.norm(xy-np.array(WGS_M.transform(*pt)),axis=1)<=RADIUS].sum()) for s,pt in cent.items()};sel,spent,rr=greedy(a,cost,budget)
    p.writecsv(out/'APPROX_MICROZONE_POP20_PREVIEW_NOT_BASELINE.csv',rr);return sel,spent

def annuity(n,r):return sum((1+r)**(-t) for t in range(1,n+1))

def finance(scaled,costs,out):
    annual=[];threshold=[]
    for x in scaled:
        s=x['scenario_id'];fold=x['scale_scenario'];h=float(x['MAIN_common_vehicle_hours_day']);c=float(costs[s]);p.require(h>=0 and c>=0,'FINANCE_INPUT')
        for v in VOTS:annual.append(dict(portfolio=s,scale=fold,hours_day=h,vot_UAH_per_vehicle_hour=v,annual_hours=h*365,annual_time_value_UAH=h*365*v,status='ILLUSTRATIVE_FIXED_VALUE_NOT_EMPIRICAL_VOT'))
        for n in HORIZONS:
            for r in RATES:
                a=annuity(n,r);v=c/(h*365*a) if h else None
                threshold.append(dict(portfolio=s,scale=fold,years=n,real_discount_rate=r,historical_cost_UAH=c,threshold_UAH_per_vehicle_hour=v,PV_time_at_100_UAH=h*365*100*a,PV_time_minus_historical_cost_at_100_UAH=h*365*100*a-c,status='TIME_ONLY_ILLUSTRATIVE_NOT_FULL_ENPV'))
    p.writecsv(out/'ANNUAL_TIME_VALUE_SENSITIVITY.csv',annual);p.writecsv(out/'TIME_ONLY_BREAK_EVEN.csv',threshold)

def pair_comparison(scaled,costs,out):
    by={(r['scale_scenario'],r['scenario_id']):r for r in scaled};rr=[]
    for fold in sorted({r['scale_scenario'] for r in scaled}):
        z=by[fold,BASE]
        for s in ('B20','N03'):
            x=by[fold,s];h=float(x['MAIN_common_vehicle_hours_day']);h0=float(z['MAIN_common_vehicle_hours_day']);u=float(x['U1_common_vehicle_hours_day']) if 'U1_common_vehicle_hours_day' in x else None;u0=float(z['U1_common_vehicle_hours_day']) if 'U1_common_vehicle_hours_day' in z else None
            rr.append(dict(scale=fold,portfolio=s,baseline=BASE,hours_day_MAIN=h,baseline_hours_day_MAIN=h0,delta_hours_day_MAIN=h-h0,relative_gain_MAIN=h/h0-1 if h0 else None,hours_day_U1=u,baseline_hours_day_U1=u0,delta_hours_day_U1=u-u0 if u is not None else None,historical_cost_UAH=str(costs[s]),baseline_cost_UAH=str(costs[BASE]),delta_cost_UAH=str(costs[s]-costs[BASE]),delta_annual_time_value_at_100_UAH=(h-h0)*365*100,status='COMMON_SUPPORT_MODEL_CONDITIONAL'))
    p.writecsv(out/'SIMPLE_VS_ALGORITHM.csv',rr)

def add_u1_common(scaled,base):
    for r in scaled:r['U1_common_vehicle_hours_day']=float(r['Q_U1'])*base[r['scenario_id'],'inter','U1']['common_finite_minutes_per_component_unit']/60

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bridges',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--offline',action='store_true');ap.add_argument('--workers',type=int,default=8);ap.add_argument('--scenario-port',type=int,default=5002);ap.add_argument('--osrm-bin',default='osrm-routed');a=ap.parse_args();o=a.out;o.mkdir(parents=True,exist_ok=True);scratch=o/'_scratch';scratch.mkdir();f=scratch/'p0e';pr=scratch/'p1a';pb=scratch/'p1b';build=scratch/'graphs';build.mkdir();gate=dict(stage='P1D',status='STARTED',freeze_changed=False,rule='GHSL2020_KNOWN_POP_20KM_PER_HISTORICAL_UAH_GREEDY',external_validation=False,absolute_scale_validated=False,financial_assumptions='ILLUSTRATIVE_NOT_OFFICIAL_VOT_OR_FULL_CBA')
    try:
        p.source_unpack(f);b.unpack(ROOT/'frozen'/b.SOURCE,pr,b.SOURCE_SHA);b.unpack(ROOT/'frozen'/P1B_ZIP,pb,P1B_SHA)
        p.require(json.loads((pb/'GATE.json').read_text())['status']=='PORTFOLIO_REPLAY_COMPLETE_REVIEW_REQUIRED','P1B_NOT_COMPLETE')
        cands,cost,total,way=b.candidates();budget=total*D('.2');cent=centers(way);costs={s:v['cost'] for s,v in cands.items()}|{'OPEN':total,'S14':cost['S14']}
        p.writecsv(o/'BASELINE_CENTERS.csv',[dict(bridge_id=s,lon=cent[s][0],lat=cent[s][1],operational_way_ids=';'.join(map(str,sorted(way[s]))),status='FROZEN_OSM_MIDPOINT_PROXY_NOT_OFFICIAL') for s in sorted(cent)])
        sel0,c0=preview(f/'MICROZONES.csv',cent,cost,budget,o);gate.update(preview_selected=sorted(sel0),preview_cost_UAH=str(c0),preview_status='APPROXIMATE_NOT_ACTUAL_RULE_SELECTION')
        prior=p.readcsv(ROOT/'inputs/P1C_INDEPENDENT_SCALE.csv');finance([dict(scenario_id=x['scenario_id'],scale_scenario=x['scale'],MAIN_common_vehicle_hours_day=x['main_hours_day']) for x in prior],costs,o)
        if a.offline:gate['status']='OFFLINE_PREVIEW_AND_FINANCE_ONLY';return 0
        paths=resolve_pop(a.bridges);pp=[]
        for s in sorted(cent):
            v=raster_population(circle(*cent[s]),paths);pp.append(dict(bridge_id=s,**v));print('POP20',s,round(v['pop_known']),flush=True)
        p.writecsv(o/'RAW_GHSL_POP20_QA.csv',pp)
        pop={r['bridge_id']:r['pop_known'] for r in pp};sel,spent,rr=greedy(pop,cost,budget);p.writecsv(o/'SIMPLE_POP20_GREEDY.csv',rr);gate.update(baseline_selected=sorted(sel),baseline_cost_UAH=str(spent),baseline_unknown_pop_area_fraction_max=max(r['nodata_fraction'] for r in pp))
        p.require(sel,'EMPTY_BASELINE');rows,keys,tot,unk,counts=p.load_ledger(f/'ROUTE_LEDGER.csv');p.require(len(rows)==218354 and len(keys)==143076,'OD_COUNTS')
        mass={(r['component'],r['model']):r for r in p.readcsv(f/'MASS_QA.csv')};scope={int(r['parent']):r['scope'] for r in p.readcsv(f/'PARENT_SCOPE_941.csv')};fits=p.readcsv(f/'RETROSPECTIVE_FIT.csv');op,closed,s14=(b.gz_pairs(pr/f'{s}_PAIR_RESULTS.csv.gz',keys) for s in ('OPEN','CLOSED','S14'))
        p.require(all(p.matches_ledger(v,keys[k]) for k,v in op.items()),'OPEN_LEDGER_REPLAY_FAIL')
        sc={'OPEN':op,'S14':s14}|{s:b.gz_pairs(pb/f'{s}_PAIR_RESULTS.csv.gz',keys) for s in b.ORDER}
        match=next((s for s,v in cands.items() if v['selected']==sel),None)
        if match:sc[BASE]=sc[match];gate.update(reused_graph=match)
        else:
            p.require(a.scenario_port not in (5000,5001) and p.free_port(a.scenario_port) and 1<=a.workers<=32,'PORT_OR_WORKERS')
            binary=shutil.which(a.osrm_bin) or (a.osrm_bin if Path(a.osrm_bin).is_file() else None);p.require(binary,'OSRM_BINARY_NOT_FOUND')
            ref=json.loads((pb/'BUILD_B20.json').read_text());p.require(p.sha(Path(binary))==json.loads((pr/'GRAPH_CLOSED_SHA256.json').read_text())['osrm_routed_binary_sha256'],'ROUTED_BINARY_CHANGED')
            p.require(all(p.sha(Path(b.native_bin(n,binary)))==h for n,h in ref['binary_sha256'].items()),'BUILDER_BINARIES_CHANGED')
            for k in random.Random(20261125).sample(sorted(keys),24):
                r=p.pair_route('http://127.0.0.1:5001',k);p.require(p.matches_ledger(r,keys[k]) and p.same_endpoints(r,op[k]),'OPEN_FINGERPRINT_FAIL')
            for s in ('CLOSED','S14'):
                m=json.loads((pr/f'GRAPH_{s}_SHA256.json').read_text());q=Path(m['graph_base']);p.require(q.resolve().is_relative_to(a.bridges.resolve()) and p.graph_manifest(q)==m['graph_files'],'P1A_LOCAL_GRAPH_CHANGED '+s)
            binding=json.loads((pb/'PROFILE_BINDING.json').read_text());profs=[Path(binding[k]) for k in ('closed_profile','s14_profile')]
            p.require(all(x.is_file() for x in profs) and p.sha(profs[0])==binding['closed_sha'] and p.sha(profs[1])==binding['s14_sha'],'NATIVE_PROFILE_CHANGED')
            profile,tmpl=b.scan_profiles(a.bridges,b.closure_for(set(),way),b.closure_for({'S14'},way),o,profs)
            graph=b.build_graph(a.bridges,BASE,sel,way,profile,tmpl,build,o,binary)
            p.jwrite(o/'GRAPH_SIMPLE_POP20_SHA256.json',p.graph_manifest(graph))
            with p.NativeGraph(graph,a.scenario_port,binary,o/'OSRM_SIMPLE_POP20.log') as base:
                witnesses=p.bridge_witnesses(base,p.witness_set(keys),BASE);p.writecsv(o/'BRIDGE_WITNESSES_SIMPLE_POP20.csv',witnesses);blocked=b.closure_for(sel,way);p.require(not any(int(r['target_id'].split('_WAY_')[1]) in blocked and r.get('two_node_way_direct_edge')==1 for r in witnesses),'CLOSED_WITNESS_VIOLATION')
                sc[BASE]=p.evaluate(base,keys,60,a.workers,BASE,op)
            gate['new_graph']=True
        p.require(not any(p.outcome(op[k],closed[k],v)[0]=='NESTING_VIOLATION' or v['status']=='FASTER_THAN_OPEN' for k,v in sc[BASE].items()),'NESTING_VIOLATION')
        p.writecsv(o/'SIMPLE_POP20_PAIR_RESULTS.csv.gz',(p.flatten(k,v) for k,v in sc[BASE].items()))
        ar,base,outs,common=p.aggregate(rows,op,closed,sc,mass,scope)
        oldcommon={k for k in keys if all(outs[s][k][0]=='FINITE_PAIRED' for s in sc if s!=BASE)};p.require(len(oldcommon)==143010,'P1C_SUPPORT_CHANGED')
        cm=[]
        for comp in p.COMPS:
            old=math.fsum(r['weight'] for r in rows if (r['component'],r['model'])==comp and r.get('key') in oldcommon);new=math.fsum(r['weight'] for r in rows if (r['component'],r['model'])==comp and r.get('key') in common);cm.append(dict(component=comp[0],model=comp[1],prior_mass=old,new_mass=new,retained_fraction=new/old))
        p.writecsv(o/'COMMON_SUPPORT_QA.csv',cm);gate.update(common_keys=len(common),old_common_keys=len(oldcommon));p.require(len(common)>=.99*len(oldcommon) and all(r['retained_fraction']>=.99 for r in cm),'COMMON_SUPPORT_BELOW_99PCT')
        scaled=p.scale_scenarios(fits,base,sc);add_u1_common(scaled,base);p.writecsv(o/'SCENARIO_COMPONENT_BENEFITS.csv',ar);p.writecsv(o/'SCALE_SCENARIO_HOURS.csv',scaled)
        costs[BASE]=spent;pair_comparison(scaled,costs,o);finance(scaled,costs,o)
        # Original P1C rank remains untouched; this is an explicitly augmented diagnostic.
        cands[BASE]=dict(selected=sel,cost=spent,budget=budget);b.CANDIDATES=tuple(b.CANDIDATES)+(BASE,)
        reg,rank=b.regrets(scaled,cands,total);p.writecsv(o/'AUGMENTED_CANDIDATE_REGRET.csv',reg);p.writecsv(o/'AUGMENTED_CANDIDATE_RANK.csv',rank);p.writecsv(o/'PAIRED_DIFFERENCES_MC_ONLY.csv',b.paired_differences(rows,outs,common,cands,fits));gate.update(status='COMPARATOR_ROUTED_CONDITIONAL_REVIEW_REQUIRED',rank=[r['scenario_id'] for r in rank]);return 0
    except Exception as ex:gate.update(status='BLOCKED_OR_PARTIAL',error=str(ex));traceback.print_exc();return 1
    finally:p.jwrite(o/'GATE.json',gate);shutil.rmtree(scratch,ignore_errors=True)
if __name__=='__main__':sys.exit(main())
