#!/usr/bin/env python3
"""Monochrome figures from replayed CSV tables; supplementary plots use reported summaries."""
from pathlib import Path
import argparse
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
ROOT=Path(__file__).resolve().parents[1]
def main():
 p=argparse.ArgumentParser();p.add_argument('--results',type=Path,default=ROOT/'recomputed');p.add_argument('--out',type=Path,default=ROOT/'figures');a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 plt.rcParams.update({'font.family':'DejaVu Sans','font.size':10,'axes.spines.top':False,'axes.spines.right':False,'axes.labelcolor':'black','xtick.color':'black','ytick.color':'black','figure.facecolor':'white','savefig.facecolor':'white','svg.fonttype':'none','axes.unicode_minus':False})
 def save(fig,name):
  for ext in ['png','svg']:fig.savefig(a.out/f'{name}.{ext}',dpi=300,bbox_inches='tight',pad_inches=.13)
  plt.close(fig)
 def csv(n):return pd.read_csv(a.results/n)
 # 1 two circuits, with fixed structural model, feedback conceptual only.
 fig,ax=plt.subplots(figsize=(8.5,3.55));ax.set(xlim=(0,10),ylim=(0,5.2));ax.axis('off')
 def box(x,y,w,h,s):
  ax.add_patch(FancyBboxPatch((x,y),w,h,boxstyle='square,pad=0.035',facecolor='white',edgecolor='black',linewidth=.9));ax.text(x+w/2,y+h/2,s,ha='center',va='center',fontsize=9)
 def arr(x1,y1,x2,y2):ax.annotate('',(x2,y2),(x1,y1),arrowprops={'arrowstyle':'->','lw':1,'color':'black','shrinkA':2,'shrinkB':2})
 box(.15,3.1,2.65,1,'DREAM, GHSL,\nOverture, OSM');box(3.65,3.1,2.7,1,'OD-попит і спільна\nмережева вигода');box(7.2,3.1,2.65,1,'Кандидати, бюджет,\nмінімакс жалю')
 arr(2.8,3.6,3.65,3.6);arr(6.35,3.6,7.2,3.6);ax.text(.15,4.55,'Контур 1. Формування рішення',weight='medium')
 box(.15,.5,2.65,1,'Натурні обліки\nAADT; TomTom');box(3.65,.5,2.7,1,'Масштабування та\nзіставлення потоків');box(7.2,.5,2.65,1,'Межі достовірності;\nсценарні висновки');arr(2.8,1,3.65,1);arr(6.35,1,7.2,1);ax.text(.15,1.93,'Контур 2. Емпіричне зіставлення',weight='medium');arr(5,1.5,5,3.1);arr(8.5,3.1,8.5,1.5);ax.text(5.15,2.35,'масштаб',fontsize=8);save(fig,'01_two_contours')
 # 2 scale errors across the same comparison support.
 m=csv('METRICS_RECOMPUTED.csv');m=m[(m.scope=='ALL_FIXED_PAIRED_POSITIVE') & m.fold.isin(['M-21','H-11','H-24','M-22'])];piv=m.pivot(index='fold',columns='model',values='mean_abs_log').loc[['M-21','H-11','H-24','M-22']];n=m[m.model=='MAIN'].set_index('fold').n
 fig,ax=plt.subplots(figsize=(6.7,3.6));x=np.arange(4);w=.32;ax.bar(x-w/2,piv.U1,w,facecolor='white',edgecolor='black',label='U1',hatch='///');ax.bar(x+w/2,piv.MAIN,w,color='.28',label='MAIN');ax.set_xticks(x,[f'{s.replace("H","Н")}\n(n={n[s]})' for s in piv.index]);ax.set_ylabel('Середня абсолютна лог-похибка');ax.legend(frameon=False,ncol=2);ax.set_ylim(0,3.7);ax.grid(axis='y',color='.9');ax.set_axisbelow(True);save(fig,'02_road_error')
 c=csv('BUDGET_COMPARISON_RECOMPUTED.csv');b=c[c.scale=='ALL_TRAIN'];m=b[b.algorithm=='MAIN_AUGMENTED'].sort_values('budget_pct');s=b[b.algorithm=='SIMPLE'].sort_values('budget_pct');l=b[b.algorithm=='LEGACY'].groupby('budget_pct').share_benefit_19.agg(['min','max']).reindex(m.budget_pct);x=m.budget_pct.to_numpy();fig,ax=plt.subplots(figsize=(7.1,4.1));ax.fill_between(x,l['min'].to_numpy()*100,l['max'].to_numpy()*100,color='.85',label='BigData OSM: діапазон');ax.plot(x,m.share_benefit_19*100,'o-',color='black',lw=1.7,label='MAIN');ax.plot(x,s.share_benefit_19*100,'s--',color='.43',lw=1.4,label='Simple');ax.set(ylim=(35,104),xlim=(7,103),xlabel='Бюджет, % історичної вартості 19 мостів',ylabel='Частка вигоди повного відкриття, %');ax.set_xticks(x);ax.grid(axis='y',color='.9');ax.set_axisbelow(True);ax.legend(frameon=False,loc='lower right');save(fig,'04_budget_comparison')
 # 5 rebased cost-benefit, unchanged budgets
 m=m[m.budget_pct.isin([10,20,30])];s=s[s.budget_pct.isin([10,20,30])];fig,ax=plt.subplots(figsize=(6.7,3.65));x=np.arange(3);w=.32;ax.bar(x-w/2,s.share_benefit_18*100,w,facecolor='white',edgecolor='black',hatch='///',label='Simple');ax.bar(x+w/2,m.share_benefit_18*100,w,color='.28',label='MAIN');ax.set(ylim=(0,110),ylabel='Частка вигоди 18 мостів, %');ax.set_xticks(x,[f'{v*100:.1f}%'.replace('.',',') for v in m.matched_budget_share_18]);ax.set_xlabel('Той самий абсолютний бюджет / вартість 18 мостів');ax.legend(frameon=False,ncol=2,loc='upper left');ax.grid(axis='y',color='.9');ax.set_axisbelow(True);save(fig,'05_without_S14')
 # 6 scenario range, no CI
 w=c[c.budget_pct.isin([10,20,30])];p=w.pivot(index=['budget_pct','scale'],columns='algorithm',values='MAIN_hours_day') if not w.duplicated(['budget_pct','scale','algorithm']).any() else w[w.algorithm.isin(['MAIN_AUGMENTED','SIMPLE'])].pivot(index=['budget_pct','scale'],columns='algorithm',values='MAIN_hours_day');d=(p.MAIN_AUGMENTED-p.SIMPLE)*365*100/1e6;g=d.groupby('budget_pct').agg(['min','max']);base=d.xs('ALL_TRAIN',level='scale');fig,ax=plt.subplots(figsize=(6.8,3.35));ys=np.arange(3);ax.hlines(ys,g['min'],g['max'],color='.4',lw=2);ax.plot(base,ys,'o',color='black',label='Базовий сценарій');ax.plot(g['min'],ys,'|',color='.35');ax.plot(g['max'],ys,'|',color='.35');ax.set_yticks(ys,[f'{n}%' for n in g.index]);ax.invert_yaxis();ax.set_xlabel('Додаткова річна вигода, млн грн (100 грн/авт.-год)');ax.set_ylabel('Бюджет');ax.legend(frameon=False,loc='lower right');save(fig,'06_money_range')
if __name__=='__main__':main()
