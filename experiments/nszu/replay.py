#!/usr/bin/env python3
"""Independent CSV replay of corrected six-bridge study."""
from pathlib import Path
import argparse,itertools,json,math
import numpy as np
import pandas as pd
R=Path(__file__).resolve().parent/'data';M=['U0','U1','U2','U2_conf07'];A='S01__S11__S15';B='S01__S15__S17'
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args();out=a.out;out.mkdir(parents=True,exist_ok=True)
def rd(n):return pd.read_csv(R/n)
def ck(x,y,tol=1e-8):assert np.allclose(x,y,rtol=2e-10,atol=tol,equal_nan=True)
z=rd('NSZU_ZONE_MASSES.csv').sort_values('cache_index_0');P=z.POP.to_numpy();N=z.NRES_m2.to_numpy();I=z.economic_POI.to_numpy();J=z.economic_POI_conf07.to_numpy();ids=z.zone_id.to_list();assert len(set(ids))==len(ids)==70
w=rd('NSZU_OD_WEIGHTS_4830.csv');assert len(w)==4830 and not w.duplicated(['origin','destination']).any();w=w.set_index(['origin','destination']);assert all(i!=j for i,j in w.index)
i=np.array([ids.index(x) for x in w.index.get_level_values(0)]);j=np.array([ids.index(x) for x in w.index.get_level_values(1)]);T=w.open_time_min.to_numpy();W=[P/P.sum(),.5*P/P.sum()+.5*N/N.sum(),.5*P/P.sum()+.5*I/I.sum(),.5*P/P.sum()+.5*J/J.sum()];q={}
for m,ww in zip(M,W):
 raw=P[i]*ww[j]*np.exp(-math.log(2)*T/60);q[m]=raw/raw.sum();ck(q[m],w[m],1e-15)
cfg=json.loads((R/'COSTS_AND_BUDGET.json').read_text());costs=cfg['costs_UAH'];budget=cfg['budget_UAH'];fs={('__'.join(c) if c else 'NONE'):sum(costs[b] for b in c) for k in range(7) for c in itertools.combinations(sorted(costs),k) if sum(costs[b] for b in c)<=budget+.001};assert len(fs)==24
od=rd('NSZU_OD_TIMES_120750.csv.gz');st=rd('NSZU_SITE_TIMES_49000.csv.gz');sites=rd('NSZU_SITES_28.csv');assert len(sites)==28 and len(od)==120750 and len(st)==49000;assert not od.duplicated(['scenario','origin','destination']).any() and not st.duplicated(['scenario','origin','site_index_0']).any();assert set(od.scenario)==set(st.scenario)==set(fs)|{'ALL_OPEN'}
od=od.set_index(['scenario','origin','destination']);near=st.groupby(['scenario','origin']).time_min.min();base=od.loc['NONE'].time_min.reindex(w.index).to_numpy();hb=near.loc['NONE'].reindex(ids).to_numpy();rows=[]
for s,c in sorted(fs.items()):
 d=base-od.loc[s].time_min.reindex(w.index).to_numpy();h=hb-near.loc[s].reindex(ids).to_numpy();assert np.isfinite(d).all() and np.isfinite(h).all()
 rows.append(dict(portfolio=s,cost_UAH=c,VH_min_per_resident=float(P@np.maximum(0,h)/P.sum()),affected_pop_GHSL=float(P[h>1e-5].sum()),affected_OD_count=int((d>1e-5).sum()),**{'VT_'+m:float(q[m]@np.maximum(0,d)) for m in M}))
x=pd.DataFrame(rows).set_index('portfolio');ref=rd('NSZU_RECOMPUTED_24_PORTFOLIOS.csv').set_index('portfolio').loc[x.index];errs={k:float(np.max(abs(x[k]-ref[k]))) for k in x};assert max(errs.values())<1e-6,errs;x.to_csv(out/'NSZU_24_REPLAY.csv')
front=[];threshold=[];lo=x.loc[A,'VH_min_per_resident'];hi=x.loc[B,'VH_min_per_resident'];assert abs(hi-x.VH_min_per_resident.max())<1e-12
for m in M:
 col='VT_'+m
 for k,r in x.iterrows():
  dom=((x[col]>=r[col]-1e-9)&(x.VH_min_per_resident>=r.VH_min_per_resident-1e-9)&(x.cost_UAH<=r.cost_UAH+.001)&((x[col]>r[col]+1e-9)|(x.VH_min_per_resident>r.VH_min_per_resident+1e-9)|(x.cost_UAH<r.cost_UAH-.001))).any();front.append(dict(model=m,portfolio=k,pareto_three_objectives=not dom))
 for h in [0,lo,.02,.05,.1,hi,hi+1e-6]:
  f=x[x.VH_min_per_resident>=h-1e-12].reset_index().sort_values([col,'cost_UAH','portfolio'],ascending=[False,True,True]);choice=f.iloc[0].portfolio if len(f) else 'NONE_FEASIBLE';assert choice==(A if h<=lo else B if h<=hi else 'NONE_FEASIBLE');threshold.append(dict(model=m,assumed_health_gain_floor_min_per_resident=h,eligible_count=len(f),choice=choice,VT=f.iloc[0][col] if len(f) else None,VH=f.iloc[0].VH_min_per_resident if len(f) else None,cost_UAH=f.iloc[0].cost_UAH if len(f) else None))
f=pd.DataFrame(front);refp=rd('NSZU_PARETO_4_MODELS.csv');p2=f.merge(refp,on=['model','portfolio']);assert len(p2)==96 and (p2.pareto_three_objectives_x==p2.pareto_three_objectives_y).all();f.to_csv(out/'NSZU_PARETO_REPLAY.csv',index=False)
hr=pd.DataFrame(threshold);refh=rd('NSZU_HEALTH_FLOOR.csv');assert len(hr)==len(refh) and list(hr.choice)==list(refh.choice) and list(hr.eligible_count)==list(refh.eligible_count);ck(hr.VT,refh.VT);ck(hr.VH,refh.VH);hr.to_csv(out/'NSZU_HEALTH_FLOOR_REPLAY.csv',index=False)
res={'status':'PASS_NSZU_CSV_REPLAY','portfolios':len(x),'max_abs_error':errs,'thresholds':len(hr),'new_routing':False};(out/'NSZU_STATUS.json').write_text(json.dumps(res,indent=2)+'\n');print(res)
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
fig,ax=plt.subplots(figsize=(7,4));ax.scatter(x.VH_min_per_resident,x.VT_U1,facecolor='white',edgecolor='.5',s=30)
for k,label in [(A,'А'),(B,'Б')]:
 r=x.loc[k];ax.scatter(r.VH_min_per_resident,r.VT_U1,c='black',s=45);ax.annotate(label,(r.VH_min_per_resident,r.VT_U1),xytext=(6,4),textcoords='offset points')
ax.set(xlabel='Скорочення часу доступу, хв/мешканця',ylabel='Транспортна вигода, хв/відправлення');ax.spines[['top','right']].set_visible(False);fig.tight_layout()
for ext in ['png','svg']:fig.savefig(out/f'07_nszu_frontier.{ext}',dpi=300)
