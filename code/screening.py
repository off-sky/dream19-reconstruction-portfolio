"""Frozen selector from P1H code/p1h.py; only dependency p.require replaced by assert. Not an exact optimizer."""
from functools import lru_cache
from decimal import Decimal as D
import numpy as np
def mask(s): return sum(1<<(int(x[1:])-1) for x in set(s))
def members(m): return {f'S{i+1:02}' for i in range(19) if m&(1<<i)}
def selector(mm,vals,cost,budget,mandatory=0,forbid=0):
    """Two bounded forward searches per score (marginal / cost, marginal),
    using single bridges plus observed required bridge bundles; up to 3 improving
    single-for-single swaps. Retain best robust and ALL_TRAIN proxy candidates.
    Not a global optimizer. All comparisons in exact arithmetic for cost.
    """
    bits=[mask([s]) for s in sorted(cost)]; c=[cost[s] for s in sorted(cost)]
    @lru_cache(maxsize=None)
    def spent(m):return sum((v for bit,v in zip(bits,c) if m&bit),D(0))
    den=vals.sum(axis=0);assert np.all(den>0), 'SCREEN_ZERO_REFERENCE'
    cache={}
    def v(m):
        if m not in cache:cache[m]=vals[(mm&m)==mm].sum(axis=0)/den
        return cache[m]
    bundles=sorted(set(bits)|set(map(int,mm)))
    eligible=[x for x in bundles if not x&forbid and spent(x|mandatory)<=budget]
    if spent(mandatory)>budget: return []
    found=set()
    for obj in range(5):
        def score(m):
            t=v(m);return float(t[obj] if obj<4 else t.mean())
        for ratio in (True,False):
            m=mandatory
            while True:
                opts=[];old=score(m);cm=spent(m)
                for add in eligible:
                    n=m|add;cn=spent(n)
                    if n==m or cn>budget:continue
                    delta=score(n)-old
                    if delta>1e-14:opts.append((delta/float(cn-cm) if ratio else delta,delta,-float(cn),-n,n))
                if not opts:break
                m=max(opts)[-1]
            for _ in range(3):
                opts=[];old=score(m)
                for remove in [0]+[x for x in bits if m&x and not mandatory&x]:
                    for add in eligible:
                        n=(m&~remove)|add;cn=spent(n)
                        if n!=m and cn<=budget and score(n)>old+1e-14:opts.append((score(n),-float(cn),-n,n))
                if not opts:break
                m=max(opts)[-1]
            found.add(m)
    found=sorted(found);best=np.max(np.array([v(m) for m in found]),axis=0)
    def regret(m):return float(np.max(np.divide(best-v(m),best,out=np.zeros_like(best),where=best>0)))
    chosen={min(found,key=lambda m:(regret(m),spent(m),m)),max(found,key=lambda m:(v(m)[0],-float(spent(m)),-m)),max(found,key=lambda m:(v(m)[2],-float(spent(m)),-m))}
    return [(m,regret(m),v(m).tolist(),m in chosen) for m in found]
