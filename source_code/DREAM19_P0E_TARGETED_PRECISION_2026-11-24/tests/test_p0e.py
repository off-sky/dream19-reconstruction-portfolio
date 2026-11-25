import sys,unittest,tempfile,math
from pathlib import Path
from unittest.mock import patch
import numpy as np
from shapely.geometry import LineString
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'code'))
import p0e as e
import p0d as b

class Tests(unittest.TestCase):
    def test_blend_full_support(self):
        q=np.array([[0.,.2],[.8,0.]])
        p=e.blend(q,np.array([[0.,1.],[0.,0.]]),np.zeros((2,2)))
        self.assertAlmostEqual(p.sum(),1);self.assertTrue(np.all(p[q>0]>=.5*q[q>0]));self.assertEqual(p[0,0],0.)
    def test_blend_no_targeting(self):
        q=np.array([.1,.9]);np.testing.assert_allclose(e.blend(q,np.zeros(2),np.zeros(2)),q)
    def test_ht_exact_expectation(self):
        q=np.array([.02,.2,.08]);p=e.blend(q,np.array([1.,0.,0.]),np.array([0.,1.,0.]));self.assertAlmostEqual(float((q/p*p).sum()),q.sum());self.assertAlmostEqual(float((q/p*p)[0]),q[0])
    def test_group_pilot_no_double_bridge(self):
        rr=[dict(component='inter',route_status='OK',weight=.1,hits='S1_WAY_1;S1_WAY_2',parent_o=0,parent_d=1,model='MAIN'),dict(component='inter',route_status='OK',weight=.1,hits='S1_WAY_1',parent_o=1,parent_d=0,model='MAIN')]
        labels=['S1_WAY_1','S1_WAY_2'];p=e.pilot_inter(rr,set(labels),labels,[('bridge_candidate','S1')]*2,2);self.assertAlmostEqual(p[0,1],.5);self.assertAlmostEqual(p[1,0],.5)
    def test_exact_and_sampling_variance_strata(self):
        # Independent old/new HT contribution: same structural mass, separate strata.
        rr=[]
        for k,ws in [('old',[.1,.1]),('new',[.2,.2])]:
            for i,w in enumerate(ws):rr.append(dict(component='inter',model='MAIN',weight=w,route_status='OK',distance_m=100.,hits='a' if i==0 else '',stratum=k,sampling='IID_HT',parent_o=0))
        d=b.stats(rr,['a'],np.array([0.]),[],np.array([1.]));v=d['inter','MAIN'];self.assertAlmostEqual(v['hit'][0],.3);self.assertAlmostEqual(v['rse'][0],math.sqrt(.05)/.3)
    def test_cache_duplicate_conflict(self):
        r=dict(lon_o=1.,lat_o=2.,lon_d=3.,lat_d=4.,route_status='OK',weight=.2,duration_min=1.,distance_m=50.,max_snap_m=10.,hits='a');self.assertEqual(len(e.cache_from_old([r,r])),1)
        with self.assertRaises(AssertionError):e.cache_from_old([r,dict(r,hits='b')])
    def test_cache_hit_uses_table_time_guard(self):
        r=dict(lon_o=1.,lat_o=2.,lon_d=3.,lat_d=4.,component='local',weight=.2,reference_local_time_min=5.)
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(e.route_new('mock',[r],{e.key(r):('OK',20.,100.,10.,'a')},[LineString([(0,0),(1,1)])],['a'],Path(d),1),0);self.assertEqual(r['route_status'],'TABLE_ROUTE_TIME_MISMATCH');self.assertEqual(r['hits'],'')
    def test_local_exact_replaces_sample_and_ht(self):
        v=[dict(node_id=f'n{i}',parent=0,pop=1.,nres=0.,lon=25.+i*.01,lat=49.) for i in range(4)]
        t=np.full((4,4),5.);np.fill_diagonal(t,0.);mat,diag,unk=b.local_matrix([1.]*4,t,np.zeros(4,dtype=bool));mass=mat.sum();q=dict(parent=0,nodes=4,n_pairs=12,n_requested=2,route_sampling='IID_STRATIFIED',national_pop_weight=.5,same_cell_mass=.5*diag,unknown_table_reserve=.5*unk,routable_mass=.5*mass,scope='CORE')
        rr=[dict(component='local',model='MAIN',parent_o=0,parent_d=0,node_o='n0',node_d='n1',reference_local_time_min=5.,weight=.5*mass/2,route_status='OK',hits='',stratum='Z000',sampling='IID_STRATIFIED',draw=f'old{k}') for k in range(2)]
        with tempfile.TemporaryDirectory() as d,patch.object(b,'table',return_value=(t,np.zeros(4,dtype=bool))):
            old,new,qa=e.build_local('mock',rr,[q],{0:v},set(),[],[],[],12,42,Path(d));self.assertEqual(len(old),0);self.assertEqual(len(new),12);self.assertAlmostEqual(sum(x['weight'] for x in new),q['routable_mass']);self.assertEqual(qa[0]['route_sampling'],'EXACT')
            old,new,qa=e.build_local('mock',rr,[q],{0:v},set(),[],[],[],4,42,Path(d));self.assertEqual(len(old),2);self.assertEqual(len(new),4);self.assertAlmostEqual(sum(x['weight'] for x in old+new),q['routable_mass']);self.assertEqual(qa[0]['route_sampling'],'MIXED_OLD_IID_NEW_HT')
    def test_local_table_change_rejected(self):
        v=[dict(node_id='n0'),dict(node_id='n1')];mat=np.array([[0,.2],[.2,0]]);q=dict(parent=0,national_pop_weight=.5,n_pairs=2,same_cell_mass=.3,unknown_table_reserve=0.,routable_mass=.2)
        with self.assertRaises(AssertionError):e.validate_local_table(q,mat,.6,0.,np.array([[0.,8.],[8.,0.]]),v,[dict(node_o='n0',node_d='n1',reference_local_time_min=5.,draw='a')])
    def test_weak_zero_not_accepted(self):
        a=dict(hit=np.array([0.,1.]),ess=np.array([100.,100.]),rse=np.array([0.,.1]));st={x:a for x in e.COMPS};self.assertEqual(e.weak_targets(st,['x','y'],[('count','H-24')]*2,{'x':'H-24','y':'H-24'}),{'inter':{'x'},'local':{'x'}})
if __name__=='__main__':unittest.main()
