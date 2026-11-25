#!/usr/bin/env python3
"""Offline calculation from frozen route ledger and common OD weights/times.
No new OSRM queries or refit of structural parameters. Never imputes unknown route mass.
"""
from pathlib import Path
from collections import defaultdict, Counter
from decimal import Decimal as D
from itertools import combinations
import argparse, math, json, hashlib, gzip, csv, time, sys
import numpy as np
import pandas as pd
from screening import selector, mask, members
ROOT=Path(__file__).resolve().parents[1]; DATA=ROOT/'data'
FOLDS=['ALL_TRAIN','M-21','H-11','H-24']; ROADS=['M-21','H-11','H-24','M-22']; TRAIN=ROADS[:3]
COMPS=[('inter','MAIN'),('local','MAIN'),('inter','U1')]; KEYS=['lon_o_e7','lat_o_e7','lon_d_e7','lat_d_e7']
def read(sub,**kw):return pd.read_csv(DATA/sub,**kw)
def save(df,out,name):pd.DataFrame(df).to_csv(out/name,index=False,float_format='%.17g')
def close(a,b,atol=1e-9):
 a=np.asarray(a,dtype=float);b=np.asarray(b,dtype=float);assert a.shape==b.shape and np.allclose(a,b,rtol=2e-10,atol=atol,equal_nan=True),('numeric mismatch',a.shape,b.shape,float(np.nanmax(abs(a-b))) if a.size else 0)
def nnls(X,y,w):
 A=X/y[:,None]*np.sqrt(w[:,None]);b=np.sqrt(w);cand=[np.zeros(X.shape[1])];v=np.linalg.lstsq(A,b,rcond=None)[0]
 if np.all(v>=0):cand.append(v)
 for k in range(X.shape[1]):
  x=np.zeros(X.shape[1]);den=np.dot(A[:,k],A[:,k]);x[k]=max(0,np.dot(A[:,k],b)/den) if den else 0;cand.append(x)
 return min(cand,key=lambda x:np.sum((A@x-b)**2))
def hit_stats(led,targets,group=False):
 # Include every sampled row in stratum denominator, even no-hit and unknown routes.
 n=led.groupby(['component','model','stratum'],sort=False).size().to_dict(); kinds=led.groupby(['component','model','stratum'],sort=False).sampling.first().to_dict(); agg=defaultdict(lambda:[0.,0.,0]); per=defaultdict(lambda:[0.,0.]);
 for r in led.itertuples(index=False):
  if r.route_status!='OK':continue
  ts={targets[h] for h in r.hits.split(';') if h in targets};w=r.weight;st=(r.component,r.model,r.stratum)
  for t in ts:
   z=agg[(t,r.component,r.model)];z[0]+=w;z[1]+=w*w;z[2]+=1;s=per[(t,)+st];s[0]+=w;s[1]+=w*w
 var=defaultdict(float)
 for (t,c,m,st),(s,ss) in per.items():
  k=(c,m,st);ni=n[k]
  if kinds[k]!='EXACT' and ni>1:var[t,c,m]+=max(0,(ni*ss-s*s)/(ni-1))
 out=[]
 for t in sorted(set(targets.values())):
  for c,m in COMPS:
   v,ss,nh=agg[t,c,m];ess=v*v/ss if ss else 0.;rse=math.sqrt(var[t,c,m])/v if v else np.nan
   out.append(dict(target=t,component=c,model=m,share=v,ESS=ess,RSE=rse,hit_records=nh,precision_pass=int(ess>=20 and np.isfinite(rse) and rse<=.2)))
 return pd.DataFrame(out)
def validate(out,led):
 targets=read('model/TARGET_GEOMETRY.csv');orig=read('validation/ROUTED_TARGET_SHARES.csv');stat=hit_stats(led,{x:x for x in orig.target_id.unique()});x=orig.merge(stat,left_on=['target_id','component','model'],right_on=['target','component','model'],validate='1:1');assert len(x)==465
 close(x.share,x.known_lower_share,1e-13);close(x.ESS_y,x.ESS_x);close(x.RSE_y,x.RSE_x);assert (x.precision_pass==x.new_precision_pass).all();save(stat,out,'TARGET_SHARES_RECOMPUTED.csv')
 fixed=read('validation/FIXED_EVAL_SUPPORT.csv'); counts=read('inputs/COUNTS_2025_145.csv');classes=['cars','minibuses','buses_medium','buses_heavy','trucks_light','trucks_medium','trucks_heavy','roadtrains_semitrailer','roadtrains_trailer','motorcycles_other'];assert len(counts)==145 and counts.site_id.is_unique;assert (counts[classes].sum(axis=1)==counts.aadt_2025_all_vehicles_per_day).all()
 st=stat.pivot(index='target',columns=['component','model'],values='share'); fixed=fixed.set_index('site_id');
 for col,cm in [('main_inter',COMPS[0]),('main_local',COMPS[1]),('U1',COMPS[2])]:close(fixed[col],st[cm].reindex(fixed.index),1e-13)
 close(fixed.aadt,counts.set_index('site_id').aadt_2025_all_vehicles_per_day.reindex(fixed.index)); fixed=fixed.reset_index(); frows=[];pred=[];metrics=[]
 for fold in ['ALL_TRAIN']+ROADS:
  tr=fixed[fixed.road.isin(TRAIN)&(fixed.road!=fold)&(fixed.main_inter>0)&(fixed.U1>0)&(fixed.main_local>=0)];te=fixed[fixed.road.isin(TRAIN) if fold=='ALL_TRAIN' else fixed.road==fold];y=tr.aadt.to_numpy();weight=1/tr.road.map(tr.road.value_counts()).to_numpy();q=nnls(tr[['main_inter','main_local']].to_numpy(),y,weight);u=nnls(tr[['U1']].to_numpy(),y,weight)[0];frows.append(dict(fold=fold,fit_n=len(tr),Q_inter=q[0],Q_local=q[1],Q_U1=u,local_scale_fraction=q[1]/q.sum()));
  for t in te.itertuples():
   p=q@[t.main_inter,t.main_local];p0=u*t.U1;pred.append(dict(fold=fold,site_id=t.site_id,road=t.road,aadt=t.aadt,main_pred=p,U1_pred=p0,strict=t.strict))
 f=pd.DataFrame(frows); ref=read('validation/RETROSPECTIVE_FIT.csv');ff=ref.merge(f,on='fold',suffixes=('_old',''));assert len(ff)==5
 for c in ['fit_n','Q_inter','Q_local','Q_U1','local_scale_fraction']:close(ff[c],ff[c+'_old'],1e-6)
 pr=pd.DataFrame(pred); ref=read('validation/RETROSPECTIVE_PREDICTIONS.csv');pp=ref.merge(pr,on=['fold','site_id'],suffixes=('_old',''));assert len(pp)==len(pr)==len(ref)
 for c in ['main_pred','U1_pred']:close(pp[c],pp[c+'_old'],1e-7)
 for fold,g in pr.groupby('fold'):
  for scope in ['ALL_FIXED_PAIRED_POSITIVE','STRICT_PAIRED_POSITIVE']:
   h=g[(g.main_pred>0)&(g.U1_pred>0)&((g.strict==1) if scope.startswith('STRICT') else True)]
   for m,col in [('MAIN','main_pred'),('U1','U1_pred')]:
    e=np.log(h[col]/h.aadt);metrics.append(dict(fold=fold,scope=scope,model=m,n=len(h),mean_abs_log=e.abs().mean(),median_abs_log=e.abs().median(),median_ratio=np.exp(e.median()),mean_log_bias=e.mean()))
 met=pd.DataFrame(metrics);ref=read('validation/RETROSPECTIVE_METRICS.csv');z=ref.merge(met,on=['fold','scope','model'],suffixes=('_old',''));assert len(z)==len(met)==len(ref)
 for c in ['n','mean_abs_log','median_abs_log','median_ratio','mean_log_bias']:close(z[c],z[c+'_old'])
 hold=pr[(pr.fold!='ALL_TRAIN')&(pr.main_pred>0)&(pr.U1_pred>0)];assert len(hold)==83
 summary=[dict(model=m,n=len(hold),MALE=float(np.log(hold[c]/hold.aadt).abs().mean())) for m,c in [('MAIN','main_pred'),('U1','U1_pred')]]
 save(f,out,'FIT_RECOMPUTED.csv');save(pr,out,'PREDICTIONS_RECOMPUTED.csv');save(met,out,'METRICS_RECOMPUTED.csv');save(summary,out,'POOLED_MALE.csv');return f.set_index('fold'),stat

def fill(order,cost,budget):
 spent=D(0);sel=[]
 for s in order:
  if spent+cost[s]<=budget:sel.append(s);spent+=cost[s]
 return tuple(sorted(sel))
def verify_rules(out,fit,od,led):
 p=read('portfolios/RAW_GHSL_POP20_QA.csv');pop=dict(zip(p.bridge_id,p.pop_known));assert (p.nodata_fraction==0).all()
 c=read('inputs/HISTORICAL_COSTS_19.csv',dtype=str);cost={r.bridge_id:D(r.historical_cost_UAH) for r in c.itertuples()};total=sum(cost.values());ways={r.bridge_id:set(r.operational_way_ids.split(';')) for r in c.itertuples()};reg=read('inputs/bridge_registry_19.csv',dtype=str).set_index('sample_id'); old=read('portfolios/HISTORICAL_IDENTITY_AND_SCORES.csv',dtype=str).set_index('sample_id'); plan=read('portfolios/BUDGET_CANDIDATE_PLAN.csv',dtype={'cost_UAH':str,'budget_UAH':str});known=read('portfolios/PORTFOLIO_COSTS_AND_MEMBERS.csv');assert len(cost)==19
 for r in plan.itertuples():
  s=r.selected.split('+');assert len(s)==len(set(s)) and sum(cost[x] for x in s)==D(r.cost_UAH)<=D(r.budget_UAH)==total*D(r.budget_pct)/100
 # Rebuild historical rank envelope from known compatible way sets, with unknown ranks enumerated.
 rule=[]
 for pct,g in plan.groupby('budget_pct'):
  budget=total*D(pct)/100;eligible={s for s in cost if cost[s]<=budget};simple=fill(sorted(cost,key=lambda s:(-D(str(pop[s]))/cost[s],reg.loc[s,'dream_id'])),cost,budget); s0=tuple(g[g.role=='SIMPLE'].iloc[0].selected.split('+'));assert simple==s0
  if pct in (40,60,100): assert all(tuple(r.selected.split('+'))==simple for r in g[g.role=='LEGACY'].itertuples());continue
  scores={}
  for s in eligible:
   if s in old.index and s!='S14' and old.loc[s,'dream_id']==reg.loc[s,'dream_id'] and set(old.loc[s,'old_way_ids'].split(';'))==ways[s]: scores[s]=D(str(pop[s]))*D(old.loc[s,'old_detour1km_min'])/cost[s]
  order=sorted(scores,key=lambda s:(-scores[s],reg.loc[s,'dream_id']));orders=[order]
  for u in sorted(eligible-set(scores)):orders=[a[:i]+[u]+a[i:] for a in orders for i in range(len(a)+1)]
  env=Counter(fill(a,cost,budget) for a in orders);observed={tuple(r.selected.split('+')):int(r.rank_orders_count) for r in g[g.role=='LEGACY'].itertuples()};assert dict(env)==observed,(pct,env,observed)
  for s,n in env.items():rule.append(dict(budget_pct=pct,selected='+'.join(s),rank_orders_count=n,rank_orders_total=len(orders)))
 save(rule,out,'LEGACY_ENVELOPE_RECOMPUTED.csv')
 # Screen contribution by OPEN route-hit bridge masks (exact replay of the frozen surrogate).
 tags={f'{s}_WAY_{wid}':s for s,ws in ways.items() for wid in ws}; m=defaultdict(lambda:np.zeros(2));keyindex=pd.MultiIndex.from_frame(od[KEYS]);delta=pd.Series(od.dt_min_OPEN.to_numpy(),index=keyindex)
 sel=led[(led.route_status=='OK')&(led.model=='MAIN')].copy();sel[KEYS]=np.rint(sel[['lon_o','lat_o','lon_d','lat_d']].to_numpy()*1e7).astype(np.int64);sel['dt']=delta.reindex(pd.MultiIndex.from_frame(sel[KEYS])).to_numpy();sel=sel[sel.dt.notna() & (sel.dt>0)]
 for r in sel.itertuples():m[mask(tags[t] for t in r.hits.split(';') if t in tags)][0 if r.component=='inter' else 1]+=r.weight*r.dt
 raw=read('portfolios/SCREEN_HIT_MASSES.csv');
 for r in raw.itertuples():close(m[r.hitmask],[r.inter,r.local],1e-11)
 mm=np.array([i for i in sorted(m) if i and m[i].sum()>0],dtype=np.int64);ww=np.array([m[i] for i in mm]);vals=ww@fit.loc[FOLDS,['Q_inter','Q_local']].to_numpy().T/60;screen=[]
 for pct in [10,30,80]:
  rr=selector(mm,vals,cost,total*D(pct)/100,mandatory=mask(['S14']) if pct==80 else 0)
  for x,rg,vs,keep in rr:screen.append(dict(budget_pct=pct,selected='+'.join(sorted(members(x))),proxy_max_regret=rg,selected_for_exact_replay=int(keep)))
 scr=pd.DataFrame(screen);ref=read('portfolios/PROXY_SCREEN_CANDIDATES.csv');z=scr.merge(ref,on=['budget_pct','selected'],suffixes=('','_old'));assert len(z)==len(scr)==len(ref);close(z.proxy_max_regret,z.proxy_max_regret_old);assert (z.selected_for_exact_replay==z.selected_for_exact_replay_old).all();save(scr,out,'SCREEN_RECOMPUTED.csv')
 return plan,cost,total

def portfolio(out,fit,od,plan,cost,total):
 assert len(od)==143010 and not od.duplicated(KEYS).any();w=od[['w_inter_main','w_local_main','w_inter_u1']].to_numpy().T; assert (w>=0).all();ref=read('portfolios/PORTFOLIO_BENEFITS.csv');cols=read('portfolios/PAIR_COLUMNS.csv');rows=[]
 oldcost=read('portfolios/PORTFOLIO_COSTS_AND_MEMBERS.csv').set_index('scenario_id');byid=plan.drop_duplicates('scenario_id').set_index('scenario_id')
 for r in cols.itertuples():
  dt=od[r.delta_column].to_numpy();assert np.isfinite(dt).all() and (dt>=0).all()
  if isinstance(r.raw_new_seconds_column,str):
   sec=od[r.raw_new_seconds_column].to_numpy();assert (sec>=od.open_seconds-1.01).all() and (sec<=od.closed_seconds+1.01).all();close(dt,np.maximum(0,od.closed_seconds-sec)/60,1e-10)
  v=w@dt;s=r.scenario_id;sel=(byid.loc[s,'selected'] if s in byid.index else oldcost.loc[s,'selected']).split('+');c=sum(cost[x] for x in sel)
  for f in FOLDS:
   a=fit.loc[f];rows.append(dict(scenario_id=s,scale_scenario=f,MAIN_hours_day=(a.Q_inter*v[0]+a.Q_local*v[1])/60,U1_hours_day=a.Q_U1*v[2]/60,inter_minutes_per_unit=v[0],local_minutes_per_unit=v[1],u1_minutes_per_unit=v[2],cost_UAH=float(c)))
 ben=pd.DataFrame(rows);z=ref.merge(ben,on=['scenario_id','scale_scenario'],suffixes=('_old',''));assert len(z)==len(ben)==96
 for c in ['MAIN_hours_day','U1_hours_day','inter_minutes_per_unit','local_minutes_per_unit','u1_minutes_per_unit','cost_UAH']:close(z[c],z[c+'_old'],1e-6)
 val=ben.set_index(['scenario_id','scale_scenario']);result=[];ranks=[];money=[]
 for pct,g in plan.groupby('budget_pct'):
  ids=sorted(set(g.scenario_id));mainids=sorted(set(g[g.role.str.startswith('MAIN')].scenario_id));best={f:max(val.loc[(s,f),'MAIN_hours_day'] for s in ids) for f in FOLDS};rg={s:max((best[f]-val.loc[(s,f),'MAIN_hours_day'])/best[f] for f in FOLDS) for s in ids};k=lambda s:(rg[s],val.loc[(s,FOLDS[0]),'cost_UAH'],s);win=min(ids,key=k);pure=min(mainids,key=k)
  for s in sorted(ids,key=k):ranks.append(dict(budget_pct=pct,scenario_id=s,max_relative_regret=rg[s]))
  for alg,s in [('MAIN_AUGMENTED',win),('MAIN_SCREEN_PRIOR',pure)]+[(r.role,r.scenario_id) for r in g.itertuples() if r.role in ('SIMPLE','LEGACY')]:
   for f in FOLDS:
    b=val.loc[(s,f)];h=b.MAIN_hours_day;u=b.U1_hours_day;c=b.cost_UAH;h19=val.loc[('OPEN',f),'MAIN_hours_day'];h18=val.loc[('A18_EXCEPT_S14',f),'MAIN_hours_day'];nos14='S14' not in byid.loc[s,'selected'].split('+');
    result.append(dict(budget_pct=pct,algorithm=alg,scenario_id=s,scale=f,cost_UAH=c,MAIN_hours_day=h,U1_hours_day=u,share_cost_19=c/float(total),share_benefit_19=h/h19,share_cost_18=c/float(total-cost['S14']) if nos14 else np.nan,share_benefit_18=h/h18 if nos14 else np.nan,matched_budget_share_18=float(total*D(pct)/100/(total-cost['S14'])),max_relative_regret=rg[s]))
    for vot in (50,100,200):
     pv=h*365*vot*(1-1.05**-20)/.05;money.append(dict(budget_pct=pct,algorithm=alg,scenario_id=s,scale=f,vot_UAH_vehicle_hour=vot,annual_time_value_UAH=h*365*vot,PV_time_20y_5pct_UAH=pv,historical_cost_UAH=c,PV_minus_historical_cost_UAH=pv-c,break_even_UAH_vehicle_hour=c/(h*365*((1-1.05**-20)/.05)) if h>0 else np.nan))
 comp=pd.DataFrame(result); ref=read('portfolios/BUDGET_COMPARISON.csv');ix=['budget_pct','algorithm','scenario_id','scale'];z=ref.merge(comp,on=ix,suffixes=('_old',''),validate='1:1');assert len(z)==len(ref)==len(comp)
 for c in ['MAIN_hours_day','U1_hours_day','share_benefit_19','share_benefit_18','share_cost_19','share_cost_18','max_relative_regret']:close(z[c],z[c+'_old'])
 rk=pd.DataFrame(ranks);rr=read('portfolios/BUDGET_RANKS.csv').merge(rk,on=['budget_pct','scenario_id'],suffixes=('_old',''));assert len(rr)==len(rk);close(rr.max_relative_regret,rr.max_relative_regret_old)
 money=pd.DataFrame(money);mr=read('economics/BUDGET_MONETIZATION.csv');z=mr.merge(money,on=ix+['vot_UAH_vehicle_hour'],suffixes=('_old',''),validate='1:1');assert len(z)==len(mr)==len(money)
 for c in ['annual_time_value_UAH','PV_time_20y_5pct_UAH']:close(z[c],z[c+'_old'],.0001)
 save(ben,out,'PORTFOLIO_BENEFITS_RECOMPUTED.csv');save(comp,out,'BUDGET_COMPARISON_RECOMPUTED.csv');save(rk,out,'BUDGET_RANKS_RECOMPUTED.csv');save(money,out,'MONETIZATION_RECOMPUTED.csv');return ben,comp

def main():
 p=argparse.ArgumentParser();p.add_argument('--out',type=Path,default=ROOT/'recomputed');args=p.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True)
 led=read('model/ROUTE_LEDGER.csv.gz',keep_default_na=False,low_memory=False);assert len(led)==218354; assert (led[led.component=='local'].parent_o==led[led.component=='local'].parent_d).all() and (led[led.component=='inter'].parent_o!=led[led.component=='inter'].parent_d).all()
 od=read('model/COMMON_OD_WEIGHTS_TIMES.csv.gz');fit,stat=validate(out,led);print('PASS 465 passage shares, 5 scales, paired predictions, 83 pooled',flush=True)
 plan,cost,total=verify_rules(out,fit,od,led);print('PASS simple, legacy rank envelope, route-hit screen, budgets',flush=True)
 ben,comp=portfolio(out,fit,od,plan,cost,total);print('PASS 96 benefits, candidate regret, money',flush=True)
 s={'status':'PASS_MAIN_OFFLINE_REPRODUCTION','external_commercial_validation':'NOT_INCLUDED_REQUEST_PRIVATE_ACCESS','common_OD':len(od),'benefit_evaluations':len(ben)};(out/'REPRODUCTION_STATUS.json').write_text(json.dumps(s,indent=2)); print(json.dumps(s,ensure_ascii=False))
if __name__=='__main__':main()
