#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Compare G0/G1, projected GHSL2025 and observed VIIRS 2021/2025.

NO routing, NO fitted parameters, NO automated optimization of alpha vs TomTom.
Uses the same stored 70-zone 4,830-OD network and individual bridge-effects.
S11 issue and 70-zone selection occurred after reading TomTom; all results are exploratory.
"""
from __future__ import annotations
import argparse,csv,hashlib,io,itertools,json,math,statistics,zipfile
from pathlib import Path

BRIDGES=['S01','S02','S11','S14','S15','S17']
HALF_LIFE=60.0
LAMBDA_NRES=0.5
ALPHA=0.25
RATIO_CLIP=(0.5,2.0)
MIN_MONTHS=8
MIN_VALID_DAYS=5.0
MIN_COVERAGE=0.5


def read_csv(path):
    with open(path,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))

def parse(v):
    if v is None or str(v).strip()=='':return None
    x=float(v)
    if not math.isfinite(x):raise ValueError(f'Nonfinite: {v}')
    return x

def write_csv(path,rows,fields=None):
    if not rows:return
    with open(path,'w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields or list(rows[0]),extrasaction='ignore')
        w.writeheader();w.writerows(rows)

def open_routing_zip(path):
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        def locate(suffix):
            matches=[k for k in names if k.endswith('/'+suffix) or k==suffix]
            if len(matches)!=1:raise ValueError(f'Expected one {suffix} in archive; found {matches}')
            return list(csv.DictReader(io.StringIO(z.read(matches[0]).decode('utf-8-sig'))))
        od=locate('expanded_open_od.csv')
        effects={b:locate(f'{b}_affected_od.csv') for b in BRIDGES}
        results=locate('bridge_comparison.csv')
        summary_matches=[k for k in names if k.endswith('/final_summary.json')]
        summary=json.loads(z.read(summary_matches[0])) if len(summary_matches)==1 else {}
    return od,effects,results,summary


def normalize(x):
    s=sum(x.values())
    if s<=0:raise ValueError('Nonpositive total mass')
    return {k:v/s for k,v in x.items()}


def attractiveness(p,n,lam=0.5):
    pn=normalize(p); nn=normalize(n)
    return {k:(1-lam)*pn[k]+lam*nn[k] for k in p}


def weights(p, W, od):
    O=normalize(p)
    W=normalize(W)
    by_origin={i:[] for i in p}
    for row in od:
        i=row['origin'];j=row['destination'];t=parse(row['open_min'])
        if t is None:raise ValueError('Missing OPEN route: '+i+'->'+j)
        by_origin[i].append((j,t))
    if len(od)!=len(p)*(len(p)-1):raise ValueError('OD is incomplete or contains self-OD')
    beta=math.log(2)/HALF_LIFE
    q={}
    for i,targets in by_origin.items():
        if len(targets)!=len(p)-1:raise ValueError('Wrong OD count for '+i)
        scalars=[W[j]*math.exp(-beta*t) for j,t in targets]
        denom=sum(scalars)
        if denom<=0:raise ValueError('Origin denominator zero '+i)
        for (j,_),v in zip(targets,scalars):
            key=(i,j)
            if key in q:raise ValueError('Duplicate OD: '+str(key))
            q[key]=O[i]*v/denom
    if abs(sum(q.values())-1)>1e-9:raise ValueError('q mass does not sum to 1')
    return q


def weighted_bridge_results(q,effects,label):
    result=[]
    for b,rows in effects.items():
        f=t=dep=loss=disconnected=0.
        for row in rows:
            k=(row['origin'],row['destination'])
            if k not in q:raise ValueError('Bridge pair not found in q: '+str(k))
            v=q[k];f+=v
            if row['category'] in ('AFFECTED','DISCONNECTED_BY_CLOSURE'):dep+=v
            if row['category']=='DISCONNECTED_BY_CLOSURE':disconnected+=v
            d=parse(row.get('delta_min'))
            if d is not None:
                if d< -1e-4:raise ValueError('Negative bridge loss')
                loss+=v*max(0,d)
        result.append(dict(model=label,bridge=b,traversal_share=f,
            dependency_share=dep,mean_finite_detour_minutes=loss,
            disconnected_demand_mass=disconnected,users_od_count=len(rows)))
    return result


def check_old_baseline(rows,reported):
    baseline={ (r['bridge'],r['model']):r for r in reported }
    err=0
    for r in rows:
        orig=baseline.get((r['bridge'],r['model']))
        if not orig:raise ValueError('Missing archived benchmark '+str(r))
        for k in ('traversal_share','dependency_share','mean_finite_detour_minutes'):
            err=max(err,abs(float(r[k])-float(orig[k])))
    if err>3e-7:raise ValueError('FAIL: G0/G1 do not reproduce archived model! max diff '+str(err))
    return err


def annual_viirs_signal(monthly,ids):
    monthmap={}
    for r in monthly:
        key=(r['zone_id'],int(r['year']),int(r['month']))
        if key in monthmap:raise ValueError('Duplicate VIIRS month: '+str(key))
        monthmap[key]=r
    if len(monthmap) not in (2*12*len(ids),):
        raise ValueError('Incomplete VIIRS CSV: expected 24×70 months, got '+str(len(monthmap)))
    rows=[];L21={};L25={}
    for z in ids:
        y21=[];y25=[];pair=[]
        for m in range(1,13):
            a=monthmap.get((z,2021,m));b=monthmap.get((z,2025,m))
            if a is None or b is None:raise ValueError('Missing VIIRS row '+str((z,m)))
            def val(r):
                l=parse(r.get('ntl_monthly_spatial_mean'))
                coverage=parse(r.get('fraction_pixels_with_observation'))
                days=parse(r.get('mean_valid_days_per_pixel'))
                if (l is None or coverage is None or days is None or
                        l<0 or coverage<MIN_COVERAGE or days<MIN_VALID_DAYS):return None
                return l
            av=val(a);bv=val(b)
            if av is not None:y21.append(av)
            if bv is not None:y25.append(bv)
            if av is not None and bv is not None:pair.append((av,bv))
        status='PASS' if len(pair)>=MIN_MONTHS else 'LOW_COVERAGE'
        rows.append(dict(zone_id=z,valid_months_2021=len(y21),valid_months_2025=len(y25),
                         paired_valid_months=len(pair),status=status,
                         ntl_2021_paired_median=statistics.median(x[0] for x in pair) if pair else '',
                         ntl_2025_paired_median=statistics.median(x[1] for x in pair) if pair else '',
                         ntl_2025_unpaired_median=statistics.median(y25) if y25 else ''))
        if len(pair)>=MIN_MONTHS:
            L21[z]=statistics.median(x[0] for x in pair)
            L25[z]=statistics.median(x[1] for x in pair)
    return rows,L21,L25


def load_ghsl(path,ids,old):
    raw=read_csv(path); got={}
    for r in raw:
        z=r['zone_id']
        if z in got:raise ValueError('Duplicate GHSL zone '+z)
        got[z]=r
    if set(got)!=set(ids):raise ValueError('GHSL 2025 missing zones')
    p25={};n25={};mismatch=[]
    for z in ids:
        r=got[z]
        # 2020 duplicated from export must agree with our previously accepted values.
        for field,oldfield in [('population_2020_check','population_2020'),
                               ('nres_2020_check_m2','nres_builtup_m2')]:
            v=parse(r.get(field));base=parse(old[z][oldfield])
            if v is None or v<0:raise ValueError('Missing GHSL 2020 cross-check')
            if abs(v-base)>max(1.,abs(base)*0.01):
                mismatch.append((z,field,base,v))
        p25[z]=parse(r.get('population_2025'))
        n25[z]=parse(r.get('nres_builtup_2025_m2'))
        if p25[z] is None or n25[z] is None or p25[z]<0 or n25[z]<0:
            raise ValueError('Bad GHSL 2025 for '+z)
    if mismatch:
        raise ValueError('GHSL 2020 re-extraction differs by >1%; inspect polygon/QA, e.g. '+str(mismatch[:3]))
    return p25,n25


def spearman(x,y):
    if len(x)<3:return None
    def rank(v):
        indexes=sorted(range(len(v)),key=lambda i:v[i]);ans=[0.]*len(v)
        start=0
        while start<len(v):
            stop=start+1
            while stop<len(v) and v[indexes[stop]]==v[indexes[start]]:stop+=1
            av=(start+1+stop)/2
            for k in range(start,stop):ans[indexes[k]]=av
            start=stop
        return ans
    a=rank(x);b=rank(y);ma=statistics.mean(a);mb=statistics.mean(b)
    num=sum((i-ma)*(j-mb) for i,j in zip(a,b))
    da=sum((i-ma)**2 for i in a);db=sum((j-mb)**2 for j in b)
    return num/math.sqrt(da*db) if da>0 and db>0 else None


def budget_portfolios(results,bridge_master,out):
    if not bridge_master:return None
    data={r['sample_id']:r for r in read_csv(bridge_master)}
    if set(BRIDGES)-set(data):raise ValueError('Bridge finance/TomTom CSV missing bridge ID')
    costs={b:parse(data[b].get('finance_uah_historical_not_remaining_capex')) for b in BRIDGES}
    if any(v is None or v<=0 for v in costs.values()):
        raise ValueError('Historical finance numbers not available; do not invent CAPEX')
    perf=[];base=sum(costs.values())
    by_model={}
    for r in results:by_model.setdefault(r['model'],{})[r['bridge']]=r
    for model,vals in by_model.items():
        for frac in (.05,.10,.20,.40,.60,.85,.90):
            B=base*frac
            best=None
            for mask in range(1<<len(BRIDGES)):
                ids=[b for i,b in enumerate(BRIDGES) if mask & (1<<i)]
                cost=sum(costs[b] for b in ids)
                if cost>B+1e-6:continue
                benefit=sum(vals[b]['mean_finite_detour_minutes'] for b in ids)
                candidate=(benefit,-cost,','.join(ids))
                if best is None or candidate>best[0]:best=(candidate,ids,cost)
            benefit,chosen,cost=best[0][0],best[1],best[2]
            perf.append({'model':model,'budget_pct':round(frac*100),
              'selected_bridges':';'.join(chosen),'spent_uah_historical':round(cost,2),
              'model_benefit_min_per_trip':benefit,
              'LIMITATION':'Scenario only. Historical finance != remaining CAPEX.'})
    write_csv(out/'scenario_portfolios.csv',perf)
    return len(perf)


def self_test():
    od=[{'origin':'a','destination':'b','open_min':'10'},
        {'origin':'b','destination':'a','open_min':'10'}]
    q=weights({'a':10,'b':30},{'a':1,'b':1},od)
    assert abs(sum(q.values())-1)<1e-9
    assert abs(q[('a','b')]-.25)<1e-10
    assert spearman([1,2,3],[3,2,1])==-1
    e={'S01':[{'origin':'a','destination':'b','category':'AFFECTED','delta_min':'2'}]}
    r=weighted_bridge_results(q,e,'test')[0]
    assert abs(r['mean_finite_detour_minutes']-.5)<1e-9
    print('SELF-TEST PASS: q conservation, relative origin masses, bridge effects, Spearman')


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-nodes',default='expanded_area_masses.csv')
    ap.add_argument('--routing-zip',default='expanded_multibridge_real.zip')
    ap.add_argument('--ghsl',default='',help='ghsl_2020_2025_zones.csv from export_zone_signals.py')
    ap.add_argument('--viirs',default='',help='viirs_monthly_2021_2025_zones.csv')
    ap.add_argument('--bridge-master',default='',help='Optional old bridge_master_2026-11-04.csv (TomTom + historical costs)')
    ap.add_argument('--out',default='information_model_comparison')
    ap.add_argument('--require-signals',action='store_true')
    ap.add_argument('--self-test',action='store_true')
    args=ap.parse_args()
    if args.self_test:return self_test()
    base=read_csv(args.base_nodes)
    old={r['zone_id']:r for r in base}
    if len(old)!=70 or len(base)!=70:raise ValueError('Expected exactly 70 masses')
    ids=list(old)
    p={z:parse(old[z]['population_2020']) for z in ids}
    n={z:parse(old[z]['nres_builtup_m2']) for z in ids}
    if any(v is None or v<=0 for v in p.values()):raise ValueError('Invalid population')
    if any(v is None or v<0 for v in n.values()):raise ValueError('Invalid NRES')
    od,effects,reported,original_summary=open_routing_zip(args.routing_zip)
    if original_summary.get('n_ordered_od')!=4830:raise ValueError('Wrong routing archive')
    if set(effects)!=set(BRIDGES):raise ValueError('Unexpected bridge list')
    model_specs={
        'G0_population2020':(p,normalize(p)),
        'G1_population_nres2020':(p,attractiveness(p,n)),
    }
    outputs=Path(args.out);outputs.mkdir(exist_ok=True,parents=True)
    preliminary=[]
    for name,(origins,attract) in model_specs.items():
        q=weights(origins,attract,od)
        preliminary.extend(weighted_bridge_results(q,effects,'G0' if name.startswith('G0') else 'G1'))
    err=check_old_baseline(preliminary,reported)
    print('BASELINE REGRESSION PASS: max absolute difference=',err,flush=True)
    if args.ghsl:
        p25,n25=load_ghsl(args.ghsl,ids,old)
        model_specs['G1_GHSL2025_projection']=(p25,attractiveness(p25,n25))
    qc=None
    if args.viirs:
        qc,L21,L25=annual_viirs_signal(read_csv(args.viirs),ids)
        write_csv(outputs/'viirs_zone_QA.csv',qc)
        if len(L21)==len(ids):
            area={z:parse(next(r for r in read_csv(args.ghsl) if r['zone_id']==z)['ukraine_area_m2']) for z in ids} if args.ghsl else None
            if area:
                mass={z:L25[z]*area[z] for z in ids}
                if sum(mass.values())>0:
                    w1=model_specs['G1_population_nres2020'][1]
                    w2=normalize(mass)
                    model_specs['G2_VIIRS2025_spatial_level']=(p,{z:.75*w1[z]+.25*w2[z] for z in ids})
            pos=sorted([v for v in L21.values() if v>0])
            if not pos:raise ValueError('2021 light all zero; cannot define eps')
            eps=0.1*statistics.median(pos)
            rr={z:min(RATIO_CLIP[1],max(RATIO_CLIP[0],(L25[z]+eps)/(L21[z]+eps))) for z in ids}
            w1=model_specs['G1_population_nres2020'][1]
            model_specs['G2_VIIRS2021_2025_change']=(p,normalize({z:w1[z]*rr[z]**ALPHA for z in ids}))
            if 'G1_GHSL2025_projection' in model_specs:
                w25=model_specs['G1_GHSL2025_projection'][1]
                model_specs['G2_GHSL2025_plus_VIIRS_change']=(p25,normalize({z:w25[z]*rr[z]**ALPHA for z in ids}))
            (outputs/'viirs_update_settings.json').write_text(json.dumps({'alpha':ALPHA,
              'ratio_clip':RATIO_CLIP,'epsilon':eps,'min_matched_good_months':MIN_MONTHS,
              'min_days_month':MIN_VALID_DAYS,'min_fraction_pixels':MIN_COVERAGE},indent=2))
        else:
            print('STOP VIIRS models: only',len(L21),'of 70 zones have >=8 paired valid months. No imputation.',flush=True)
    if args.require_signals and not (args.ghsl and args.viirs and len(L21)==len(ids)):
        raise ValueError('Requested signals incomplete. Inspect viirs_zone_QA.csv; do not silently invent VIIRS observations.')
    allrows=[]
    for label,(origins,attr) in model_specs.items():
        q=weights(origins,attr,od)
        allrows.extend(weighted_bridge_results(q,effects,label))
    write_csv(outputs/'bridge_information_models.csv',allrows)
    if args.bridge_master:
        budget_portfolios(allrows,args.bridge_master,outputs)
    meta={'baseline_regression_max_error':err,'n_zones':70,'n_od_pairs':4830,
          'models_run':list(model_specs),'viirs_good_zone_n':len(L21) if args.viirs else None,
          'beta_log2_over_60':math.log(2)/60,'no_training':True,
          'interpretation':'All results are exploratory. GHSL2025 is projection; VIIRS is proxy; no new independent test/holdout.',
          'model_selector':'no optimizing alpha, beta, zones, or budgets against TomTom',
          'network':'frozen S14 200km July 2026',
          'warning':'Single-bridge loss sum is not full nonadditive portfolio network benefit.'}
    (outputs/'comparison_manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2)+'\n')
    print('OUTPUT:',outputs, 'models:',list(model_specs))

if __name__=='__main__':main()
