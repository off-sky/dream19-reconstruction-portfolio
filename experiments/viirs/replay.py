#!/usr/bin/env python3
"""Execute historical VIIRS rules without licensed comparison values."""
from pathlib import Path
import argparse,subprocess,sys,json
import pandas as pd
import numpy as np
R=Path(__file__).resolve().parent;D=R/'data';p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
r=pd.read_csv(D/'viirs_monthly_2021_2025_zones_full.csv');assert len(r)==1680 and len(r[['zone_id','year','month']].drop_duplicates())==1680 and int(r.ntl_monthly_spatial_mean.isna().sum())==143
s=[]
for name,days,months in [('strict',5,8),('POSTHOC_d3_m6',3,6),('POSTHOC_d4_m5',4,5),('POSTHOC_d5_m4',5,4)]:
 d=a.out/name;d.mkdir(exist_ok=True)
 subprocess.run([sys.executable,str(R/'code/run_viirs_model.py'),'--qa-min-days',str(days),'--qa-min-months',str(months),'--base-nodes',str(D/'expanded_area_masses.csv'),'--routing-zip',str(D/'routing_cache.zip'),'--ghsl',str(D/'ghsl_2020_2025_zones_full.csv'),'--viirs',str(D/'viirs_monthly_2021_2025_zones_full.csv'),'--bridge-master',str(D/'bridge_costs.csv'),'--out',str(d)],check=True)
 for n in ['bridge_information_models.csv','viirs_zone_QA.csv','scenario_portfolios.csv']:
  x=pd.read_csv(d/n);ref=pd.read_csv(R/'reference'/name/n);x=x[ref.columns];assert x.shape==ref.shape
  for c in x:
   if pd.api.types.is_numeric_dtype(ref[c]):assert np.allclose(x[c],ref[c],rtol=1e-10,atol=1e-12,equal_nan=True),(name,n,c)
   else:assert list(x[c].fillna(''))==list(ref[c].fillna('')),(name,n,c)
 meta=json.loads((d/'comparison_manifest.json').read_text());good=meta['viirs_good_zone_n'];assert good==(0 if name=='strict' else 70)
 po=pd.read_csv(d/'scenario_portfolios.csv');base=po[po.model=='G1_population_nres2020'].set_index('budget_pct').selected_bridges.fillna('')
 for mod,g in po.groupby('model'):
  sel=g.set_index('budget_pct').selected_bridges.fillna('');changed=int((sel!=base).sum());assert changed==0;s.append(dict(qa=name,model=mod,zones_passing=good,budget_levels=len(sel),changed_vs_G1=changed))
pd.DataFrame(s).to_csv(a.out/'VIIRS_QA_DECISION_REPLAY.csv',index=False);res={'status':'PASS_VIIRS_QA_AND_SELECTION','monthly_records':1680,'missing_monthly_values':143,'strict_pass':0,'posthoc_pass':70,'changed_portfolios':0,'external_comparison':'NOT_INCLUDED_REQUEST_PRIVATE_ACCESS'};(a.out/'VIIRS_STATUS.json').write_text(json.dumps(res,indent=2)+'\n');print(res)
