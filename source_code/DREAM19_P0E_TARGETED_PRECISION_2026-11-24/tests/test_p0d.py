import sys,unittest,tempfile, pathlib,math,json,zipfile
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box,LineString
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'code'))
import p0d as m
class Test(unittest.TestCase):
 def test_frozen_real_inputs(self):
  ids,xy,p,n,i,counts,fs=m.read_inputs();self.assertEqual(len(ids),941);self.assertEqual(len(fs),85);self.assertEqual(sum(r in m.TRAIN for r in fs.values()),65);self.assertAlmostEqual(p.sum(),39549156.193659045,places=5)
 def test_real_target_geo(self):
  t,c,l,meta,g=m.make_targets();self.assertEqual(sum(x[0]=='count' for x in meta),132);self.assertEqual(sum(x[0]=='bridge_candidate' for x in meta),23);self.assertEqual(len({x[1] for x in meta if x[0]=='bridge_candidate'}),19);self.assertEqual(len(l),len(set(l)));self.assertEqual(sum(x['status']=='NO_COORDINATES' for x in g),13)
 def test_gravity_normalization(self):
  p=np.array([1.,2.,3.]);n=np.array([3.,2.,1.]);i=np.array([1.,1.,2.]);t=np.array([[0,30,60],[30,0,30],[60,30,0.]])
  for q in m.gravity(p,n,i,t):self.assertAlmostEqual(q.sum(),1);self.assertTrue(np.all(np.diag(q)==0));self.assertTrue(np.all(q>=0))
 def test_local_time_reserve(self):
  p=[1,2,3];t=np.array([[0,10,20],[10,0,np.nan],[20,20,0.]])
  q,diag,unk=m.local_matrix(p,t,np.array([False,False,False]));self.assertAlmostEqual(q.sum()+diag+unk,1);self.assertGreater(diag,0);self.assertGreater(unk,0);self.assertEqual(q[1,2],0);self.assertEqual(q.trace(),0)
 def test_local_bad_snap(self):
  q,d,u=m.local_matrix([1,1],[[0,5],[5,0]],np.array([True,False]));self.assertEqual(q.sum(),0);self.assertAlmostEqual(d+u,1)
 def test_one_node_unresolved(self):
  q,d,u=m.local_matrix([100],[[0]],np.array([False]));self.assertEqual(d,1);self.assertEqual(q.sum(),0);self.assertEqual(u,0)
 def test_endpoint_disjoint(self):
  xy=np.array([[25.,49.],[26.,50.],[27.,50.]]);p=np.ones((3,3));np.fill_diagonal(p,0);p=p/p.sum();r=m.inter_plan(p,p,p,xy,{},np.zeros(3),100,10);self.assertEqual(len(r),200);self.assertTrue(all(x['parent_o']!=x['parent_d'] for x in r));self.assertAlmostEqual(sum(x['weight'] for x in r if x['model']=='MAIN'),1)
 def test_local_population_absolute(self):
  v={0:[dict(node_id='a',lon=25,lat=49,pop=2,nres=0),dict(node_id='b',lon=25.02,lat=49,pop=2,nres=0)]}
  with tempfile.TemporaryDirectory() as d,patch.object(m,'table',return_value=(np.array([[0,10],[10,0]]),np.array([False,False]))):
   r,qa=m.local_plan('mock',v,np.array([4.,6.]),np.array([0,50000]),50,1,pathlib.Path(d));self.assertEqual(len(r),2);self.assertAlmostEqual(sum(x['weight'] for x in r)+qa[0]['same_cell_mass']+qa[0]['unknown_table_reserve'],.4);self.assertTrue(all(x['parent_o']==x['parent_d'] for x in r));self.assertTrue(all(x['sampling']=='EXACT' for x in r))
 def test_fit_nonnegative(self):
  X=np.array([[.1,.0],[0.,.2],[.2,.1],[.1,.3]]);y=X@np.array([100.,300.]);v,cond=m.nonnegative_fit(X,y,np.ones(4));np.testing.assert_allclose(v,[100,300]);self.assertTrue(cond<20)
 def test_guard_and_unknown(self):
  p=[dict(component='local',model='MAIN',stratum='Z001',weight=.2,route_status='OK',distance_m=1000,hits='s',parent_o=1,sampling='EXACT')];qa=[dict(national_pop_weight=.4,same_cell_mass=.1,unknown_table_reserve=.1)];s=m.stats(p,['s'],np.array([0,30000]),qa,np.array([1,1]))[('local','MAIN')];self.assertAlmostEqual(s['guard'][0],.2);self.assertAlmostEqual(s['unknown'],.8);self.assertAlmostEqual(s['hit'][0],.2)
 def test_synthetic_real_raster_partition(self):
  with tempfile.TemporaryDirectory() as d:
   paths={};x,y=m.WGS_R.transform(30,50);x=math.floor(x/100)*100;y=math.floor(y/100)*100;t=from_origin(x,y+400,100,100);poly=box(x,y,x+400,y+400)
   pop=np.arange(16,dtype='float32').reshape(4,4);pop[0,0]=-200;nres=np.arange(16,dtype='uint16').reshape(4,4);nres[1,0]=65535
   for c,a,no in [('POP',pop,-200),('NRES',nres,65535)]:
    path=pathlib.Path(d)/(c+'.tif');paths[c]=[path]
    with rasterio.open(path,'w',driver='GTiff',height=4,width=4,count=1,dtype=a.dtype,crs='ESRI:54009',transform=t,nodata=no) as ds:ds.write(a,1)
   xx,yy=m.R_M.transform(x+200,y+200);g,q=m.pixel_groups(poly,paths,[('a',xx,yy)]);self.assertAlmostEqual(sum(a[0] for a in g.values()),float(pop[pop>=0].sum()));self.assertAlmostEqual(sum(a[1] for a in g.values()),float(nres[nres!=65535].sum()));self.assertEqual(q['POP'][2],1);self.assertEqual(q['NRES'][2],1);self.assertTrue(all(poly.covers(m.Point(a[2],a[3])) for a in g.values() if a[0]>0))
 def test_route_ledger_shared(self):
  with tempfile.TemporaryDirectory() as d:
   xy=[(30.,50.),(30.01,50.)];a,b=[m.WGS_M.transform(*p) for p in xy];cut=LineString([((a[0]+b[0])/2,(a[1]+b[1])/2-100),((a[0]+b[0])/2,(a[1]+b[1])/2+100)]);plan=[dict(draw='I',component='inter',model='MAIN',parent_o=0,parent_d=1,node_o='a',node_d='b',lon_o=30.,lat_o=50.,lon_d=30.01,lat_d=50.,weight=.4,stratum='national',sampling='IID_HT',route_status='PENDING',planned_n=1),dict(draw='L',component='local',model='MAIN',parent_o=0,parent_d=0,node_o='a',node_d='b',lon_o=30.,lat_o=50.,lon_d=30.01,lat_d=50.,weight=.2,stratum='Z000',sampling='EXACT',route_status='PENDING',planned_n=1,reference_local_time_min=3.)]
   with patch.object(m,'route',return_value=((3.,1000.,xy,2.),'OK')) as f:
    self.assertEqual(m.route_ledger('mock',plan,[cut],['s'],pathlib.Path(d),1),1);self.assertEqual(f.call_count,1);self.assertEqual(plan[0]['hits'],'s');self.assertEqual(plan[1]['hits'],'s');self.assertEqual(plan[1]['route_status'],'OK')
 def test_pinned_rejects_alternative(self):
  with tempfile.TemporaryDirectory() as d:
   p=pathlib.Path(d)/'wrong';p.write_text('different');self.assertRaises(AssertionError,m.find_pinned,pathlib.Path(d),['wrong'],'0'*64,p)
if __name__=='__main__':unittest.main()
