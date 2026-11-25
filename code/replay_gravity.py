#!/usr/bin/env python3
"""Recompute parent gravity from frozen masses and travel times."""
from pathlib import Path
import argparse,json,hashlib
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
f=R/'national/basis/13e_open_T_BASELINE_ONLY_FOR_ZONE_IDS.npz';assert hashlib.sha256(f.read_bytes()).hexdigest()=='309506bb5f981f3f416b39a12208090b7650db24ec99cb5ff53c9d056d0280ff'
with np.load(f,allow_pickle=False) as x:T=x['durations_seconds'].astype(float);ids=list(map(str,x['zone_ids']))
d=pd.read_csv(R/'data/inputs/MODEL_ONLY_ATTRIBUTES_941.csv').sort_values('zone_index');assert len(d)==941 and list(d.zone_id)==ids
P=d.ghsl_population_2020.to_numpy(float);N=d.ghsl_NRES_m2_2020.to_numpy(float);I=d.economic_poi_2026.to_numpy(float);I[(P+N)==0]=0
P/=P.sum();N/=N.sum();I/=I.sum();U=(P+N)/2;O=.75*P+.25*N;D=.25*P+.5*N+.25*I
F=2**(-T/1800);np.fill_diagonal(F,0);q0=np.outer(U,U)*F;q1=np.outer(O,D)*F;q0/=q0.sum();q1/=q1.sum()
r=pd.read_csv(R/'national/verified/PARENT_OD_GRAVITY.csv.gz');i=r.origin_index.to_numpy();j=r.destination_index.to_numpy();assert len(r)==884540 and not r.duplicated(['origin_index','destination_index']).any() and (i!=j).all();assert np.allclose(T[i,j],r.duration_seconds,rtol=0,atol=1e-9)
err=float(max(np.max(abs(q0[i,j]-r.q_U1)),np.max(abs(q1[i,j]-r.q_MAIN))));assert err<1e-14
s={'status':'PASS_PARENT_GRAVITY','zones':941,'directed_OD':len(r),'max_weight_abs_difference':err,'sampling_proposal_recomputed_in_this_release':False,'new_routing':False};(a.out/'NATIONAL_GRAVITY_CHECK.json').write_text(json.dumps(s,indent=2)+'\n');print(s)
