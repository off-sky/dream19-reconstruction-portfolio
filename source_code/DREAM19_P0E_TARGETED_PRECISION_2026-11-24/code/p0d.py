#!/usr/bin/env python3
"""Targeted integration, conditional known support; no new count data, no weight grid search."""
from __future__ import annotations
import argparse,csv,hashlib,json,math,os,sys,traceback,urllib.request,urllib.error,zipfile,concurrent.futures,collections
from pathlib import Path
import numpy as np
import rasterio
from rasterio.features import geometry_mask,rasterize
from rasterio.windows import from_bounds,Window
from pyproj import Transformer
from shapely.geometry import shape,Point,LineString,mapping
from shapely.ops import transform
from shapely.strtree import STRtree
ROOT=Path(__file__).resolve().parents[1]; IN=ROOT/'inputs'; N=941
ROADS=['M-21','H-11','H-24','M-22']; TRAIN=ROADS[:3]
TIME_SHA='309506bb5f981f3f416b39a12208090b7650db24ec99cb5ff53c9d056d0280ff'
POLY_SHA='997d294e353f855f78df4b510048ef3bf8466cbab10f641ca2eed433b7ff0cdc'
TIME_NAME='13e_open_T_BASELINE_ONLY_FOR_ZONE_IDS.npz'
POLY_NAMES=['11_national_zones_SPATIAL_ONLY.geojson','zones_941.geojson']
H=30; CORE=20000; OUTER=40000; INNER=10000; MICRO=1000; FINE=4000; COARSE=12000; SNAP=500
SEED=2026112409
WGS_M=Transformer.from_crs('EPSG:4326','EPSG:3035',always_xy=True); WGS_R=Transformer.from_crs('EPSG:4326','ESRI:54009',always_xy=True); R_WGS=Transformer.from_crs('ESRI:54009','EPSG:4326',always_xy=True); R_M=Transformer.from_crs('ESRI:54009','EPSG:3035',always_xy=True)
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def rows(name):
 with open(IN/name,encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
def write(path,rr,cols=None):
 rr=list(rr);cols=cols or (list(rr[0]) if rr else ['empty'])
 with open(path,'w',encoding='utf-8',newline='') as f:
  w=csv.DictWriter(f,fieldnames=cols,extrasaction='ignore');w.writeheader();w.writerows(rr)
def js(path,o):path.write_text(json.dumps(o,indent=2,ensure_ascii=False,allow_nan=False,default=str)+'\n')
def num(x):
 try:
  v=float(x);return v if math.isfinite(v) else None
 except (ValueError,TypeError):return None
def norm(x):
 a=np.asarray(x,dtype=float);s=a.sum()
 if s<=0 or not np.all(np.isfinite(a)) or np.any(a<0):raise ValueError('invalid mass')
 return a/s
def read_inputs():
 checks=json.loads((IN/'SHA256.json').read_text());assert all(sha(IN/k)==v for k,v in checks.items()),'INPUT_HASH_MISMATCH'
 attrs=sorted(rows('MODEL_ONLY_ATTRIBUTES_941.csv'),key=lambda r:int(r['zone_index'])); anc=sorted(rows('13e9_anchor_941.csv'),key=lambda r:int(r['zone_index'])); cross=sorted(rows('FROZEN_OD_ZONE_CROSSWALK_941.csv'),key=lambda r:int(r['zone_index']))
 assert len(attrs)==len(anc)==len(cross)==N
 assert all(int(a['zone_index'])==int(b['zone_index'])==int(c['zone_index'])==i and a['zone_id']==b['zone_id']==c['zone_id'] for i,(a,b,c) in enumerate(zip(attrs,anc,cross)))
 ids=[r['zone_id'] for r in attrs]; xy=np.array([[float(r['new_lon']),float(r['new_lat'])] for r in anc]); P=np.array([float(r['ghsl_population_2020']) for r in attrs]);R=np.array([float(r['ghsl_NRES_m2_2020']) for r in attrs]);I=np.array([float(r['economic_poi_2026']) for r in attrs]); I[(P+R)==0]=0
 counts=rows('COUNTS_2025_145.csv'); assert len(counts)==len({r['site_id'] for r in counts})==145
 classes=['cars','minibuses','buses_medium','buses_heavy','trucks_light','trucks_medium','trucks_heavy','roadtrains_semitrailer','roadtrains_trailer','motorcycles_other']
 assert all(int(r['aadt_2025_all_vehicles_per_day'])==int(r['sum_ten_classes'])==sum(int(r[k]) for k in classes) for r in counts)
 frozen=rows('FIXED_COMMON_SUPPORT_113.csv'); fs={r['site_id']:r['road'] for r in frozen}; assert len(fs)==85 and sum(r in TRAIN for r in fs.values())==65 and sum(r=='M-22' for r in fs.values())==20
 return ids,xy,P,R,I,counts,fs

def find_pinned(bridges,names,expected,explicit=None):
 if explicit:
  p=Path(explicit);assert p.is_file() and sha(p)==expected,f'PINNED_HASH_FAIL {p}';return p
 for name in names:
  for p in sorted(bridges.rglob(name)):
   if p.is_file() and sha(p)==expected:return p
 raise FileNotFoundError('Exact pinned input unavailable: '+str(names)+' sha='+expected)
def gravity(P,R,I,T):
 p,r,i=norm(P),norm(R),norm(I);u=.5*p+.5*r; o=.75*p+.25*r;d=.25*p+.5*r+.25*i
 f=2**(-np.asarray(T,dtype=float)/H); np.fill_diagonal(f,0)
 q0=np.outer(u,u)*f;q1=np.outer(o,d)*f
 return q0/q0.sum(),q1/q1.sum()
def verify_time(path,ids,P,R,I):
 with np.load(path,allow_pickle=False) as z:
  t=np.array(z['durations_seconds'],dtype=float)/60
  if 'zone_ids' in z.files:assert list(map(str,z['zone_ids']))==ids,'T_ZONE_ID_ORDER'
 assert t.shape==(N,N) and np.all(np.isfinite(t)) and (t[~np.eye(N,dtype=bool)]>0).all()
 q0,q1=gravity(P,R,I,t); old=rows('OD_IMPORTANCE_DRAWS.csv'); i=np.array([int(r['parent_origin']) for r in old]);j=np.array([int(r['parent_destination']) for r in old]);p=np.array([float(r['proposal_probability']) for r in old]);w=np.array([float(r['importance_h30']) for r in old]);err=float(np.max(abs(q0[i,j]/p-w)));assert err<1e-7,('FROZEN_WEIGHT_REPRO_FAIL',err)
 return t,q0,q1,err

def roadcode(x):return str(x).upper().translate(str.maketrans('МНІ','MHI')).replace(' ','')
def cutline(lon,lat,e,n,width):
 u=math.hypot(e,n);assert u>0;dx=width*e/u/(111195*math.cos(math.radians(lat)));dy=width*n/u/111195;return LineString([WGS_M.transform(lon-dx,lat-dy),WGS_M.transform(lon+dx,lat+dy)])
def make_targets():
 old={r['site_id']:r for r in rows('CUTS_2025_113.csv')}; review=rows('FROZEN_AZ4E_ALL145_GEOMETRY_REVIEW.csv'); assert len(old)==113
 targets=[];cuts=[];labels=[];meta=[]; roads=collections.defaultdict(list)
 for f in json.loads((IN/'FOUR_ROADS_OSM_EXTRACT.geojson').read_text())['features']:
  if f['geometry']['type']!='LineString':continue
  for r in str(f['properties'].get('ref','')).split(';'):
   if roadcode(r) in ROADS:roads[roadcode(r)].append(transform(WGS_M.transform,shape(f['geometry'])))
 trees={r:STRtree(v) for r,v in roads.items()}; geo=[]
 for r in review:
  sid=r['site_id'];lo=num(r['longitude']);la=num(r['latitude']);status='NO_COORDINATES';line=None
  if lo is not None and la is not None:
   x,y=WGS_M.transform(lo,la);targets.append((sid,x,y));status='NO_MATCHING_ROAD_EDGE'
   if sid in old:
    a=old[sid];line=cutline(float(a['lon']),float(a['lat']),float(a['dx_north_m']),float(a['dy_north_m']),float(a['half_width_m']));status='OLD_PROVISIONAL_CUT'
   elif r['road'] in trees:
    pt=Point(x,y);k=trees[r['road']].nearest(pt);g=roads[r['road']][int(k)];d=g.distance(pt)
    if d<=100:
     at=g.project(pt);c=g.interpolate(at);a=g.interpolate(max(0,at-10));b=g.interpolate(min(g.length,at+10));dx=b.x-a.x;dy=b.y-a.y;L=math.hypot(dx,dy)
     if L>0:line=LineString([(c.x-90*dy/L,c.y+90*dx/L),(c.x+90*dy/L,c.y-90*dx/L)]);status='RECONSTRUCTED_PROVISIONAL_CUT'
  geo.append(dict(target_id=sid,type='count',road=r['road'],status=status,physical_verified='NO',lon=lo,lat=la))
  if line is not None:cuts.append(line);labels.append(sid);meta.append(('count',r['road']))
 for f in json.loads((IN/'13b_bridge_candidate_lines.geojson').read_text())['features']:
  p=f['properties'];g=transform(WGS_M.transform,shape(f['geometry']));at=g.length/2;c=g.interpolate(at);a=g.interpolate(max(0,at-5));b=g.interpolate(min(g.length,at+5));dx=b.x-a.x;dy=b.y-a.y;L=math.hypot(dx,dy)
  if not L:continue
  sid=f"{p['bridge_id']}_WAY_{p['way_id']}";targets.append((sid,c.x,c.y));line=LineString([(c.x-25*dy/L,c.y+25*dx/L),(c.x+25*dy/L,c.y-25*dx/L)])
  cuts.append(line);labels.append(sid);meta.append(('bridge_candidate',p['bridge_id']));geo.append(dict(target_id=sid,type='bridge_candidate',road=p['bridge_id'],status='OSM_WAY_MIDPOINT_PROXY',physical_verified='NO',lon=None,lat=None))
 assert len({x[1] for x in meta if x[0]=='bridge_candidate'})==19,'BRIDGE_TARGETS_MISSING'
 return targets,cuts,labels,meta,geo

def geo_plan(poly,ids,targets):
 d={f['properties']['zone_id']:shape(f['geometry']) for f in json.loads(poly.read_text())['features']};assert set(d)==set(ids) and len(d)==N
 pp=[transform(WGS_R.transform,d[k]) for k in ids];mm=[transform(WGS_M.transform,d[k]) for k in ids]; target_pts=[Point(x,y) for _,x,y in targets];tt=STRtree(target_pts)
 dist=np.array([g.distance(target_pts[int(tt.nearest(g))]) for g in mm]);return pp,dist

def resolve_tiles(bridges):
 ret={}
 for r in rows('RASTER_SOURCES_USED.csv'):
  name=r['basename'];direct=bridges/'stage12d_local_results'/'ghsl_tiles'/name
  if not direct.is_file():
   options=list(bridges.rglob(name));uniq={sha(p):p for p in options};assert len(uniq)==1,('MISSING_OR_AMBIGUOUS_RASTER',name);direct=next(iter(uniq.values()))
  ret.setdefault(r['component'],[]).append(direct)
 assert len(ret['POP'])==len(ret['NRES'])==5;return ret

def pixel_groups(poly,tiles,targets,ownership=None):
 # All pixels of a selected parent are assigned ONCE to a nested fixed grid. Fine blocks
 # are selected by distance from any target (union); remaining parent mass is coarse.
 # Aggregate positive known raster values separately; keep NoData count, no imputation.
 groups={};qa={};target=np.array([[x,y] for _,x,y in targets]);
 def add(k,weight,x,y,component):
  a=groups.setdefault(k,[0.,0.,0.,0.,0.,0.,0.,0.]);t=0 if component=='POP' else 1;a[t]+=weight
  # Use a real positive pixel, not a centroid which may lie outside a parent.
  if weight>a[6+t]:a[2+2*t]=x;a[3+2*t]=y;a[6+t]=weight
 for component,paths in tiles.items():
  nvalid=nnodata=0; total=0.;seen=[]
  for p in paths:
   with rasterio.open(p) as ds:
    assert ds.crs.to_string() in ('ESRI:54009','ESRI:54009') or ds.crs==rasterio.crs.CRS.from_string('ESRI:54009');assert abs(ds.transform.a-100)<1e-6 and abs(ds.transform.e+100)<1e-6
    minx,miny,maxx,maxy=poly.bounds;b=ds.bounds
    for c in seen:
     assert min(b.right,c.right)<=max(b.left,c.left)+1e-6 or min(b.top,c.top)<=max(b.bottom,c.bottom)+1e-6, 'OVERLAPPING_RASTER_TILES'
    seen.append(b)
    if maxx<=b.left or minx>=b.right or maxy<=b.bottom or miny>=b.top:continue
    w=from_bounds(max(minx,b.left),max(miny,b.bottom),min(maxx,b.right),min(maxy,b.top),ds.transform); c0=max(0,int(math.floor(w.col_off)));r0=max(0,int(math.floor(w.row_off)));c1=min(ds.width,int(math.ceil(w.col_off+w.width)));r1=min(ds.height,int(math.ceil(w.row_off+w.height)))
    if c1<=c0 or r1<=r0:continue
    w=Window(c0,r0,c1-c0,r1-r0); a=ds.read(1,window=w,masked=True);tr=ds.window_transform(w)
    inside=(rasterize(ownership[1],out_shape=a.shape,transform=tr,fill=0,dtype='int32',all_touched=False)==ownership[0]) if ownership else geometry_mask([mapping(poly)],out_shape=a.shape,transform=tr,invert=True,all_touched=False);valid=inside&~np.ma.getmaskarray(a)&np.isfinite(a.data)&(a.data>=0)
    # Unsigned NRES nodata 65535 is never a physical value; signed POP -200 invalid.
    if component=='NRES':valid &= (a.data!=65535)
    nvalid+=int(valid.sum());nnodata+=int(inside.sum()-valid.sum());rr,cc=np.where(valid&(a.data>0));v=a.data[rr,cc].astype(float);total+=float(v.sum())
    if not len(v):continue
    xx=tr.c+(cc+.5)*tr.a;yy=tr.f+(rr+.5)*tr.e
    # Grid uses global raster origin; adaptive fine/coarse cells nest exactly.
    coarse=np.stack([np.floor(xx/COARSE).astype('int64'),np.floor(yy/COARSE).astype('int64')],1);blocks,inv=np.unique(coarse,axis=0,return_inverse=True)
    cx=(blocks[:,0]+.5)*COARSE;cy=(blocks[:,1]+.5)*COARSE;mx,my=R_M.transform(cx,cy);dd=np.min((np.array(mx)[:,None]-target[:,0])**2+(np.array(my)[:,None]-target[:,1])**2,axis=1)
    fine=dd[inv]<=(CORE+COARSE)**2;sz=np.where(fine,FINE,COARSE)
    # A nested 4-km block is subdivided into 1-km blocks near a target.
    fb=np.stack([np.floor(xx/FINE).astype('int64'),np.floor(yy/FINE).astype('int64')],1);fu,fi=np.unique(fb,axis=0,return_inverse=True);fx,fy=R_M.transform((fu[:,0]+.5)*FINE,(fu[:,1]+.5)*FINE);fd=np.min((np.asarray(fx)[:,None]-target[:,0])**2+(np.asarray(fy)[:,None]-target[:,1])**2,axis=1);sz=np.where(fine&(fd[fi]<=(INNER+FINE)**2),MICRO,sz)
    keys=np.stack([sz,np.floor(xx/sz).astype('int64'),np.floor(yy/sz).astype('int64')],1);u,k=np.unique(keys,axis=0,return_inverse=True)
    sums=np.bincount(k,weights=v);sx=np.bincount(k,weights=v*xx);sy=np.bincount(k,weights=v*yy);dist2=(xx-sx[k]/sums[k])**2+(yy-sy[k]/sums[k])**2
    order=np.lexsort((dist2,k));first=np.r_[True,k[order][1:]!=k[order][:-1]];chosen=order[first];assert np.array_equal(k[chosen],np.arange(len(u)))
    for key,m,x,y in zip(u,sums,xx[chosen],yy[chosen]):add(tuple(map(int,key)),m,x,y,component)
  qa[component]=(total,nvalid,nnodata)
 return groups,qa

def microzones(bridges,poly,dist,ids,xy,P,R,targets,out):
 tiles=resolve_tiles(bridges);js(out/'RASTER_SHA256.json',{str(p):sha(p) for pp in tiles.values() for p in pp});allnodes={};qa=[];node_rows=[];ptree=STRtree(poly)
 # Model-only mass includes explicit previously excluded components; do not use raster values
 # to recover them without review. Normalize observed positive component sums only after QA.
 for z in np.where(dist<=OUTER)[0]:
  z=int(z);neighbors=sorted(map(int,ptree.query(poly[z],predicate='intersects')));ownership=(z+1,[(mapping(poly[i]),i+1) for i in neighbors]);g,info=pixel_groups(poly[z],tiles,targets,ownership); mp,mr=info['POP'][0],info['NRES'][0];
  goodp=P[z]==0 or abs(mp-P[z])/P[z]<=.02; goodr=R[z]==0 or abs(mr-R[z])/R[z]<=.02;status='OK' if goodp and goodr and (P[z]+R[z])>0 else 'MASS_MISMATCH_OR_NO_KNOWN_SUPPORT'
  nodes=[]
  if status=='OK':
   for k,a in sorted(g.items()):
    p0=a[0]*P[z]/mp if mp>0 and P[z]>0 else 0.;r0=a[1]*R[z]/mr if mr>0 and R[z]>0 else 0.
    if p0+r0<=0:continue
    # POP anchor where possible; NRES-only cells retained for destination endpoints.
    x,y=(a[2],a[3]) if a[0]>0 and p0>0 else (a[4],a[5]);lon,lat=R_WGS.transform(x,y)
    node=dict(node_id=f'Z{z:03d}_{k[0]}_{k[1]}_{k[2]}',parent=z,lon=lon,lat=lat,pop=p0,nres=r0,grid_m=k[0]);nodes.append(node)
   if not nodes or abs(sum(n['pop'] for n in nodes)-P[z])>max(1e-6,P[z]*1e-9) or abs(sum(n['nres'] for n in nodes)-R[z])>max(1e-6,R[z]*1e-9):status='MASS_CONSERVATION_FAIL';nodes=[]
  if nodes:allnodes[z]=nodes;node_rows.extend(nodes)
  qa.append(dict(parent=z,zone_id=ids[z],distance_to_target_m=float(dist[z]),scope='CORE' if dist[z]<=CORE else 'GUARD',model_POP=P[z],raster_POP=mp,model_NRES=R[z],raster_NRES=mr,pop_nodata=info['POP'][2],nres_nodata=info['NRES'][2],nodes=len(nodes),status=status))
  if len(qa)%10==0:print('raster parents',len(qa),flush=True)
 write(out/'PARENT_REFINEMENT_QA.csv',qa);write(out/'MICROZONES.csv',node_rows);return allnodes,qa

def get_json(url,timeout=75):
 with urllib.request.urlopen(url,timeout=timeout) as r:return json.load(r)
def fmt(xy):return f'{xy[0]:.7f},{xy[1]:.7f}'
def raw_route(base,a,b):
 # This function records all finite route times and snaps; it is NOT demand-route acceptance.
 try:
  j=get_json(f'{base}/route/v1/driving/{fmt(a)};{fmt(b)}?overview=full&geometries=geojson&steps=false&alternatives=false&annotations=false')
  if j.get('code')!='Ok':return None,j.get('code','BAD_RESPONSE')
  if len(j.get('waypoints',[]))!=2:return None,'BAD_WAYPOINTS'
  snaps=[float(w['distance']) for w in j['waypoints']];r=j['routes'][0];co=r['geometry']['coordinates'];dur=float(r['duration']);dist=float(r['distance'])
  if len(co)<2 or not all(math.isfinite(x) and x>=0 for x in snaps+[dist]) or not math.isfinite(dur) or dur<=0:return None,'DEGENERATE_ROUTE'
  return (dur/60,dist,co,max(snaps)),'OK'
 except Exception as e:return None,type(e).__name__

def route(base,a,b):
 # Scientific route acceptance is unchanged. A large-snap raw route cannot contribute demand.
 r,status=raw_route(base,a,b)
 if r is not None and r[3]>SNAP:return None,'SNAP_GT_500M'
 return r,status

def preflight(base,xy,T,out):
 rng=np.random.default_rng(1401);pairs=[]
 # Exactly the same 12 deterministic reference pairs as the blocked P0D run.
 for i,j in rng.integers(0,N,size=(200,2)):
  if i!=j and 25<=T[i,j]<=600:pairs.append((int(i),int(j)))
  if len(pairs)==12:break
 assert len(pairs)==12,'REFERENCE_PAIR_COUNT_FAIL'
 anchors=sorted(rows('13e9_anchor_941.csv'),key=lambda r:int(r['zone_index']))
 old=np.array([[float(r['old_lon']),float(r['old_lat'])] for r in anchors])
 # The reference matrix was built on the legacy anchor geometry. Confirm that version;
 # use new anchor times only to document the change in endpoint geography.
 rr=[];summ={}
 for kind,points in [('old',old),('new',xy)]:
  v=[];n_accepted=0
  for i,j in pairs:
   r,status=raw_route(base,points[i],points[j]);ref=float(T[i,j]);e=abs(r[0]-ref)/ref if r else None;delta_s=abs(r[0]-ref)*60 if r else None
   if r is not None:
    v.append(delta_s);n_accepted+=int(r[3]<=SNAP)
   rr.append(dict(anchor_version=kind,i=i,j=j,matrix_min=ref,live_min=r[0] if r else None,rel_error=e,abs_error_seconds=delta_s,raw_status=status,max_snap_m=r[3] if r else None,demand_route_status=('OK' if r[3]<=SNAP else 'SNAP_GT_500M') if r else status,reference_time_match_1s=int(delta_s<=1.) if delta_s is not None else None))
  summ[kind]=dict(n_requested=len(pairs),n_raw_ok=len(v),n_time_match_1s=sum(x<=1. for x in v),max_abs_error_seconds=max(v) if v else None,n_snap_le_500=n_accepted)
 # Never use the relaxed geometry comparison from the old test as proof of graph identity.
 # All 12 legacy durations must match, even when legacy access snapping is poor.
 match=summ['old']['n_raw_ok']==12 and summ['old']['n_time_match_1s']==12
 write(out/'GRAPH_PREFLIGHT.csv',rr)
 js(out/'GRAPH_PREFLIGHT_SUMMARY.json',dict(status='LEGACY_REFERENCE_TIME_FINGERPRINT_PASS' if match else 'LEGACY_REFERENCE_TIME_FINGERPRINT_FAIL',legacy_match_required=12,tolerance_seconds=1.,demand_snap_limit_m=SNAP,raw_routes_used_for_demand=False,new_anchor_times_diagnostic_only=True,scope='12 historical reference pairs; not a full graph checksum or bridge topology certification',**summ))
 assert match,'LEGACY_REFERENCE_TIME_FINGERPRINT_FAIL_CHECK_OPEN_GRAPH_OR_MATRIX_ANCHORS'
 return 'old_reference_time_fingerprint_12_of_12'

def proposal(q0,q1,xy,targets,meta):
 # Full-support mixture, independent of counts. Add geometry-targeted component.
 N=len(xy); mx,my=WGS_M.transform(xy[:,0],xy[:,1]); co=np.column_stack([mx,my]);bar=(q0+q1)/2; u=np.ones_like(bar);np.fill_diagonal(u,0);u/=u.sum();p=.1*u+.4*bar
 # Every target contributes once; bridges get one share per bridge via candidate grouping.
 groups=collections.defaultdict(list)
 for sid,x,y in targets:
  grp='BRIDGES' if '_WAY_' in sid else next((r for r in ROADS if sid.startswith(r+'_')),'OTHER');groups[grp].append((x,y))
 for pts in groups.values():
  acc=np.zeros_like(bar)
  for x,y in pts:
   d=np.hypot(co[:,0]-x,co[:,1]-y);a=np.exp(-d/60000);v=bar*np.outer(a,a);s=v.sum()
   if s>0:acc+=v/s
  if acc.sum()>0:p+=.5/len(groups)*acc/acc.sum()
 np.fill_diagonal(p,0);p/=p.sum();assert np.all(p[~np.eye(N,dtype=bool)]>0);return p

def endpoints(nodes,xy,z,role,rng):
 v=nodes.get(z)
 if not v:return tuple(xy[z]),f'Z{z:03d}_PARENT'
 p=np.array([r['pop'] for r in v]);n=np.array([r['nres'] for r in v]);a=(.75,.25) if role=='o' else (.25,.75)
 # POI only in parent attraction. Conditional allocation in a parent is POP/NRES proxy.
 w=np.zeros(len(v));
 if p.sum()>0:w+=a[0]*p/p.sum()
 if n.sum()>0:w+=a[1]*n/n.sum()
 if w.sum()==0:return tuple(xy[z]),f'Z{z:03d}_PARENT'
 k=int(rng.choice(len(v),p=norm(w)));r=v[k];return (r['lon'],r['lat']),r['node_id']

def inter_plan(q0,q1,p,xy,nodes,dist,n,seed):
 rng=np.random.default_rng(seed);N=len(xy);ind=rng.choice(N*N,n,replace=True,p=p.ravel());rr=[]
 for k,v in enumerate(ind):
  i,j=map(int,divmod(int(v),N));a,aa=endpoints(nodes,xy,i,'o',rng);b,bb=endpoints(nodes,xy,j,'d',rng)
  for model,u,c,ac,bc in [('U1',q0,tuple(xy[i]),f'Z{i:03d}_PARENT',f'Z{j:03d}_PARENT'),('MAIN',q1,a,aa,bb)]:
   dest=tuple(xy[j]) if model=='U1' else b
   rr.append(dict(draw=f'I{k:06d}',component='inter',model=model,parent_o=i,parent_d=j,node_o=ac,node_d=bc,lon_o=c[0],lat_o=c[1],lon_d=dest[0],lat_d=dest[1],weight=float(u[i,j]/p[i,j]/n),stratum='national',sampling='IID_HT',route_status='PENDING',planned_n=n))
 return rr

def table(base,coords):
 n=len(coords); t=np.full((n,n),np.nan);bad=np.zeros(n,dtype=bool)
 for i in range(0,n,40):
  for j in range(0,n,40):
   a=coords[i:i+40];b=coords[j:j+40];cc=a+b;url=f'{base}/table/v1/driving/'+ ';'.join(map(fmt,cc))+'?annotations=duration&sources='+';'.join(map(str,range(len(a))))+'&destinations='+';'.join(map(str,range(len(a),len(cc))))
   try:
    d=get_json(url);assert d['code']=='Ok';v=np.array([[float(x)/60 if x is not None else np.nan for x in r] for r in d['durations']]);t[i:i+len(a),j:j+len(b)]=v
    bad[i:i+len(a)]|=np.array([x['distance']>SNAP for x in d['sources']]);bad[j:j+len(b)]|=np.array([x['distance']>SNAP for x in d['destinations']])
   except Exception as e:print('TABLE_BLOCK_FAILED',i,j,type(e).__name__,flush=True)
 t[bad,:]=np.nan;t[:,bad]=np.nan;return t,bad

def local_matrix(pop,t,bad):
 # POP-origin, POP-destination, common half-life. Diagonal is deliberately UNKNOWN:
 # zero time is an upper-bound attractiveness assumption, not a zero-distance road trip.
 # Unknown table entries use maximum possible friction 1 as an explicit conservative reserve.
 p=norm(pop);t=np.asarray(t,dtype=float);valid=np.isfinite(t)&(t>=0);valid[bad,:]=False;valid[:,bad]=False;np.fill_diagonal(valid,False)
 factor=np.ones_like(t);factor[valid]=2**(-t[valid]/H);mass=factor*p[None,:];den=mass.sum(axis=1);joint=p[:,None]*mass/den[:,None];known=np.where(valid,joint,0.);diag=float(np.trace(joint));unknown=float(1-known.sum()-diag)
 return known,diag,max(0.,unknown)
def local_plan(base,nodes,P,dist,n,seed,out):
 rng=np.random.default_rng(seed);rr=[];qa=[];total=P.sum()
 for z,v in sorted(nodes.items()):
  if P[z]<=0:continue
  v=[x for x in v if x['pop']>0]
  coords=[(x['lon'],x['lat']) for x in v];pop=np.array([x['pop'] for x in v]);
  if len(v)>1:t,bad=table(base,coords)
  else:t=np.zeros((1,1));bad=np.array([False])
  mat,diag,unknown=local_matrix(pop,t,bad);mass=float(mat.sum());zone=float(P[z]/total);flat=np.flatnonzero(mat.ravel()>0);nd=len(flat)
  qa.append(dict(parent=z,scope='CORE' if dist[z]<=CORE else 'GUARD',national_pop_weight=zone,nodes=len(v),same_cell_mass=zone*diag,unknown_table_reserve=zone*unknown,routable_mass=zone*mass,n_pairs=nd,route_sampling='EXACT' if nd<=n else 'IID_STRATIFIED',n_requested=min(nd,n)))
  if nd:
   sample=flat if nd<=n else rng.choice(mat.size,size=n,p=norm(mat.ravel()),replace=True)
   for k,vv in enumerate(sample):
    i,j=map(int,divmod(int(vv),len(coords)));a,b=coords[i],coords[j];w=zone*mat[i,j] if nd<=n else zone*mass/n
    rr.append(dict(draw=f'L{z:03d}_{k:05d}',component='local',model='MAIN',parent_o=z,parent_d=z,node_o=v[i]['node_id'],node_d=v[j]['node_id'],lon_o=a[0],lat_o=a[1],lon_d=b[0],lat_d=b[1],weight=float(w),stratum=f'Z{z:03d}',sampling='EXACT' if nd<=n else 'IID_STRATIFIED',route_status='PENDING',planned_n=min(nd,n),reference_local_time_min=float(t[i,j])))
  if len(qa)%10==0:print('local tables',len(qa),flush=True)
  write(out/'LOCAL_TABLE_QA.csv',qa)
 write(out/'LOCAL_TABLE_QA.csv',qa);return rr,qa

def route_ledger(base,plan,cuts,labels,out,workers):
 tree=STRtree(cuts);cache={};buckets=collections.defaultdict(list)
 for i,r in enumerate(plan):
  if r['weight']<=0:r['route_status']='ZERO_TARGET_MASS';continue
  key=tuple(round(float(r[k]),7) for k in ('lon_o','lat_o','lon_d','lat_d'));buckets[key].append(i)
 def one(k):
  r,status=route(base,k[:2],k[2:]);hit=[]
  if r:
   x,y=WGS_M.transform(*zip(*r[2]));g=LineString(list(zip(x,y)));hit=[labels[int(i)] for i in tree.query(g,predicate='intersects')]
  return k,r,status,sorted(set(hit))
 with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
  # map preserves order; all source rows have stable IDs and weights.
  for k,r,status,hits in pool.map(one,buckets):
   for ix in buckets[k]:
    d=plan[ix];st=status;hh=hits
    if r and d['component']=='local' and abs(r[0]-d['reference_local_time_min'])>max(1.,.1*d['reference_local_time_min']):st='TABLE_ROUTE_TIME_MISMATCH';hh=[]
    d.update(route_status=st,duration_min=r[0] if r else '',distance_m=r[1] if r else '',max_snap_m=r[3] if r else '',hits=';'.join(hh))
   if len(cache)%250==0:print('routes',len(cache),'/',len(buckets),flush=True)
   cache[k]=status
 cols=list(plan[0])+[k for k in ('reference_local_time_min','duration_min','distance_m','max_snap_m','hits') if k not in plan[0]];write(out/'ROUTE_LEDGER.csv',plan,cols)
 return len(buckets)

def stats(plan,labels,dist,qa,P):
 ix={s:k for k,s in enumerate(labels)};m=len(labels);out={}
 # estimates and independent stratum sample variances; exact local enumeration contributes no MC variance.
 for comp,model in [('inter','MAIN'),('inter','U1'),('local','MAIN')]:
  subset=[r for r in plan if r['component']==comp and r['model']==model]; strata=collections.defaultdict(list)
  for r in subset:strata[r['stratum']].append(r)
  hit=np.zeros(m);sq=np.zeros(m);var=np.zeros(m);guard=np.zeros(m);unknown=0.;total=0.;vkt=0.
  for st,rr in strata.items():
   n=len(rr);sh=np.zeros(m);ss=np.zeros(m)
   for r in rr:
    w=float(r['weight']);total+=w
    if w==0:continue
    if r['route_status']!='OK':unknown+=w;continue
    vkt+=w*float(r['distance_m'])/1000
    for s in filter(None,r.get('hits','').split(';')):
     k=ix[s];sh[k]+=w;ss[k]+=w*w
     if comp=='local' and dist[int(r['parent_o'])]>CORE:guard[k]+=w
   hit+=sh;sq+=ss
   if rr[0]['sampling']!='EXACT' and n>1:var+=np.maximum(0,(n*ss-sh*sh)/(n-1))
  if comp=='local':
   modelled=sum(x['national_pop_weight'] for x in qa);omitted=max(0,1-modelled);diag=sum(x['same_cell_mass'] for x in qa);tableunk=sum(x['unknown_table_reserve'] for x in qa);unknown+=omitted+diag+tableunk
  else:omitted=diag=tableunk=0.
  ess=np.divide(hit*hit,sq,out=np.zeros(m),where=sq>0);rse=np.divide(np.sqrt(var),hit,out=np.full(m,np.nan),where=hit>0)
  out[(comp,model)]=dict(hit=hit,ess=ess,rse=rse,unknown=unknown,guard=guard,omitted=omitted,diag=diag,tableunk=tableunk,vkt=vkt,draw_mass=total)
 return out

def nonnegative_fit(X,y,w):
 # road-balanced relative least squares, no tuning of structure. Test nonnegative boundaries.
 A=X/y[:,None]*np.sqrt(w[:,None]);b=np.sqrt(w);cand=[np.zeros(X.shape[1])];v=np.linalg.lstsq(A,b,rcond=None)[0]
 if np.all(v>=0):cand.append(v)
 for k in range(X.shape[1]):
  x=np.zeros(X.shape[1]);den=np.dot(A[:,k],A[:,k]);x[k]=max(0,np.dot(A[:,k],b)/den) if den else 0;cand.append(x)
 best=min(cand,key=lambda x:np.sum((A@x-b)**2));norms=np.linalg.norm(A,axis=0);cond=float(np.linalg.cond(A/np.where(norms>0,norms,1))) if np.all(norms>0) else math.inf
 return best,cond

def evaluation(st,labels,fs,counts,out):
 lookup={r['site_id']:r for r in counts};pred=[];summary=[];reg=[];M=st[('inter','MAIN')];U=st[('inter','U1')];L=st[('local','MAIN')];li={s:k for k,s in enumerate(labels)}
 # Support masks derived only from route geometry/MC, before count fit. Preserve all 85 rows.
 for sid,rd in sorted(fs.items()):
  if sid not in li:continue
  k=li[sid];g,u,l=M['hit'][k],U['hit'][k],L['hit'][k];strict=bool(g>0 and u>0 and l>0 and all(np.isfinite(x) and x<=.2 for x in (M['rse'][k],U['rse'][k],L['rse'][k])) and all(x>=20 for x in (M['ess'][k],U['ess'][k],L['ess'][k])))
  reg.append(dict(site_id=sid,road=rd,main_inter=g,main_local=l,U1=u,strict=int(strict),diagnostic=int(g>0 and u>0 and l>=0),aadt=float(lookup[sid]['aadt_2025_all_vehicles_per_day'])))
 write(out/'FIXED_EVAL_SUPPORT.csv',reg)
 for held in ['ALL_TRAIN']+ROADS:
  fit=[r for r in reg if r['road'] in TRAIN and r['road']!=held and r['diagnostic']];test=[r for r in reg if (r['road']==held or held=='ALL_TRAIN' and r['road'] in TRAIN)]
  if not fit or not test or len({r['road'] for r in fit})<2:continue
  y=np.array([r['aadt'] for r in fit]);w=np.array([1/sum(x['road']==r['road'] for x in fit) for r in fit]);X=np.array([[r['main_inter'],r['main_local']] for r in fit]);u=np.array([[r['U1']] for r in fit]);b,cond=nonnegative_fit(X,y,w);bu,_=nonnegative_fit(u,y,w);strict_n=sum(r['strict'] for r in fit)
  for r in test:
   p=b@np.array([r['main_inter'],r['main_local']]);p0=bu[0]*r['U1'];pred.append(dict(fold=held,site_id=r['site_id'],road=r['road'],aadt=r['aadt'],main_pred=float(p),U1_pred=float(p0),main_log_abs=abs(math.log(p/r['aadt'])) if p>0 else '',U1_log_abs=abs(math.log(p0/r['aadt'])) if p0>0 else '',strict=r['strict'],status='RETROSPECTIVE_CONDITIONAL_LOWER_SHARE_FIT'))
  summary.append(dict(fold=held,fit_n=len(fit),fit_strict_n=strict_n,test_n=len(test),Q_inter=float(b[0]),Q_local=float(b[1]),Q_U1=float(bu[0]),local_scale_fraction=float(b[1]/b.sum()) if b.sum()>0 else None,normalized_design_condition=cond if math.isfinite(cond) else None,identified_diagnostic=int(math.isfinite(cond) and cond<20 and min(b)>0),status='NOT_NATIONAL_CALIBRATION'))
 write(out/'RETROSPECTIVE_FIT.csv',summary);write(out/'RETROSPECTIVE_PREDICTIONS.csv',pred)
 metrics=[]
 for f in sorted({r['fold'] for r in pred}):
  for scope in ('ALL_FIXED_PAIRED_POSITIVE','STRICT_PAIRED_POSITIVE'):
   group=[r for r in pred if r['fold']==f and r['main_pred']>0 and r['U1_pred']>0 and (scope.startswith('ALL') or r['strict'])];
   for model,col in [('MAIN','main_pred'),('U1','U1_pred')]:
    e=np.array([math.log(r[col]/r['aadt']) for r in group]);metrics.append(dict(fold=f,scope=scope,model=model,n=len(e),mean_abs_log=float(np.mean(abs(e))) if len(e) else None,median_abs_log=float(np.median(abs(e))) if len(e) else None,median_ratio=float(np.exp(np.median(e))) if len(e) else None,mean_log_bias=float(np.mean(e)) if len(e) else None))
 write(out/'RETROSPECTIVE_METRICS.csv',metrics);return reg,summary

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--bridges',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);ap.add_argument('--time');ap.add_argument('--polygons');ap.add_argument('--base',default='http://127.0.0.1:5001');ap.add_argument('--inter',type=int,default=12000);ap.add_argument('--local-per-parent',type=int,default=256);ap.add_argument('--workers',type=int,default=8);ap.add_argument('--offline',action='store_true');args=ap.parse_args();out=args.out;out.mkdir(parents=True,exist_ok=True);gate={'stage':'P0D_FIX1','status':'STARTED','model_frozen':False,'new_WIM_opened':False,'independent_validation':False,'R1_changed':False,'h_minutes':H,'origin_P_N':[.75,.25],'destination_P_N_POI':[.25,.5,.25],'local_POP_POP':True,'radius_core_km':20,'radius_guard_km':40,'inner_radius_km':10,'micro_grid_m':MICRO,'fine_grid_m':FINE,'coarse_grid_m':COARSE,'parameters_are_predeclared_engineering_candidates_not_calibrated':True,'inter_draws':args.inter,'local_draws_per_parent':args.local_per_parent,'seed':SEED}
 try:
  ids,xy,P,R,I,counts,fs=read_inputs();targets,cuts,labels,meta,geo=make_targets();write(out/'TARGET_GEOMETRY.csv',geo);js(out/'TARGET_CUTS_PROJECTED_3035.json',[dict(target_id=s,kind=k,road_or_bridge=r,coordinates=list(c.coords)) for s,(k,r),c in zip(labels,meta,cuts)]);gate.update(counts=145,train=65,m22_descriptive=20,count_cuts=sum(t[0]=='count' for t in meta),bridge_candidate_cuts=sum(t[0]=='bridge_candidate' for t in meta),n_targets=len(targets),model_pop=float(P.sum()),model_nres=float(R.sum()))
  js(out/'GATE.json',gate)
  if args.offline:gate['status']='OFFLINE_INPUT_AND_GEOMETRY_PASS_NO_ROUTES';return 0
  tp=find_pinned(args.bridges,[TIME_NAME],TIME_SHA,args.time);pp=find_pinned(args.bridges,POLY_NAMES,POLY_SHA,args.polygons);T,q0,q1,err=verify_time(tp,ids,P,R,I);gate.update(time_sha=sha(tp),polygon_sha=sha(pp),old_weight_max_diff=err)
  polygons,dist=geo_plan(pp,ids,targets);gate.update(selected_parent_count=int((dist<=OUTER).sum()),core_parent_count=int((dist<=CORE).sum()),guard_parent_count=int(((dist>CORE)&(dist<=OUTER)).sum()),core_known_pop_share=float(P[dist<=CORE].sum()/P.sum()),guard_known_pop_share=float(P[(dist>CORE)&(dist<=OUTER)].sum()/P.sum()),outside_known_pop_share=float(P[dist>OUTER].sum()/P.sum()))
  write(out/'PARENT_SCOPE_941.csv',[dict(parent=i,zone_id=ids[i],distance_m=float(dist[i]),scope='CORE' if dist[i]<=CORE else 'GUARD' if dist[i]<=OUTER else 'OUTSIDE',pop=P[i]) for i in range(N)])
  gate['reference_matrix_compatible_anchor_version']=preflight(args.base,xy,T,out);gate['graph_preflight_summary']=json.loads((out/'GRAPH_PREFLIGHT_SUMMARY.json').read_text());js(out/'GATE.json',gate);nodes,qa=microzones(args.bridges,polygons,dist,ids,xy,P,R,targets,out);gate['refined_parent_count']=len(nodes);js(out/'GATE.json',gate)
  prop=proposal(q0,q1,xy,targets,meta);ip=inter_plan(q0,q1,prop,xy,nodes,dist,args.inter,SEED);lp,lqa=local_plan(args.base,nodes,P,dist,args.local_per_parent,SEED+1,out);plan=ip+lp;assert abs(sum(r['weight'] for r in lp)-sum(x['routable_mass'] for x in lqa))<1e-9,'LOCAL_WEIGHT_MASS_MISMATCH';assert all((r['parent_o']==r['parent_d'])==(r['component']=='local') for r in plan);write(out/'OD_PLAN.csv',plan,list(ip[0])+['reference_local_time_min']);gate['unique_route_requests']=route_ledger(args.base,plan,cuts,labels,out,args.workers);st=stats(plan,labels,dist,lqa,P)
  rr=[]
  for (comp,model),v in st.items():
   for k,s in enumerate(labels):rr.append(dict(target_id=s,kind=meta[k][0],road_or_bridge=meta[k][1],component=comp,model=model,known_lower_share=float(v['hit'][k]),ESS=float(v['ess'][k]),RSE=float(v['rse'][k]) if np.isfinite(v['rse'][k]) else '',guard_parent_share=float(v['guard'][k]),guard_fraction_of_known_hit=float(v['guard'][k]/v['hit'][k]) if v['hit'][k]>0 else None,global_unknown_share=v['unknown']))
  write(out/'ROUTED_TARGET_SHARES.csv',rr);gate['mass_qa']={f'{c}_{m}':{k:v[k] for k in ('draw_mass','unknown','omitted','diag','tableunk','vkt')} for (c,m),v in st.items()};reg,fits=evaluation(st,labels,fs,counts,out);rmap={r['site_id']:r for r in reg};gmap={r['target_id']:r for r in geo if r['type']=='count'};write(out/'COUNT_COVERAGE_145.csv',[dict(site_id=r['site_id'],road=r['road'],geometry=gmap[r['site_id']]['status'],in_frozen_85=int(r['site_id'] in fs),strict=rmap[r['site_id']]['strict'] if r['site_id'] in rmap else '',status='FIXED_SUPPORT_EVALUATED' if r['site_id'] in rmap else 'NOT_FIXED_EVALUATION_SUPPORT' if r['site_id'] in labels else 'NO_CUT') for r in counts]);gate.update(status='CONDITIONAL_INTEGRATED_ROUTING_COMPLETE_REVIEW_REQUIRED',strict_common_sites=sum(x['strict'] for x in reg),strict_train_sites=sum(x['strict'] for x in reg if x['road'] in TRAIN),not_certified_national_Q=True,calibration_is_diagnostic=True)
  return 0
 except Exception as e:
  gate.update(status='BLOCKED_OR_PARTIAL',error=str(e));traceback.print_exc();return 1
 finally:js(out/'GATE.json',gate)
if __name__=='__main__':sys.exit(main())
