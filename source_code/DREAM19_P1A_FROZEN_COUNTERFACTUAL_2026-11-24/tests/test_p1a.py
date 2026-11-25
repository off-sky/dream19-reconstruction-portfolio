import sys,unittest,tempfile,json,math,csv
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'code'))
import p1a as p
K=(30.,50.,30.1,50.1);K2=(30.,50.,30.2,50.2)
A=((30.,50.),1.);B=((30.1,50.1),2.);D=((30.2,50.2),3.)
def ok(t,a=A,b=B,mode='TABLE'):return p.result('OK',t,a,b,mode)
def row(c,m,k,w,st='s',sampling='EXACT',route='OK'):
    return dict(component=c,model=m,weight=w,key=k,stratum=st,sampling=sampling,route_status=route,parent_o='1')
class T(unittest.TestCase):
    def test_group(self):self.assertEqual(p.groups({K:1,K2:2},1),[[K],[K2]])
    def test_table_shape(self):
        j={'code':'Ok','sources':[{'location':[30,50],'distance':1}], 'destinations':[{'location':[30.1,50.1],'distance':2},{'location':[30.2,50.2],'distance':3}], 'durations':[[100,None]]}
        with patch.object(p,'request',return_value=j) as m:
            r=p.table_group('http://x',[K,K2]);self.assertEqual(r[K]['seconds'],100);self.assertEqual(r[K2]['status'],'TABLE_NULL');self.assertIn('sources=0&destinations=1;2',m.call_args.args[0])
    def test_table_bad_length(self):
        with patch.object(p,'request',return_value={'code':'Ok','sources':[],'destinations':[],'durations':[]}):self.assertEqual(p.table_group('x',[K])[K]['status'],'MALFORMED_TABLE')
    def test_table_http_failure(self):
        with patch.object(p,'request',return_value={'code':'REQUEST_FAILED'}):self.assertEqual(p.table_group('x',[K])[K]['status'],'TABLE_REQUEST_FAILED')
    def test_open_ledger_exact(self):self.assertEqual(p.open_one('x',K,ok(100),{'time':100,'max_snap':2})['status'],'OK')
    def test_open_fallback(self):
        with patch.object(p,'pair_route',return_value=ok(100,mode='ROUTE')) as f:self.assertEqual(p.open_one('x',K,ok(110),{'time':100,'max_snap':2})['mode'],'ROUTE');f.assert_called_once()
    def test_open_fail_closed(self):
        with patch.object(p,'pair_route',return_value=ok(110)):self.assertEqual(p.open_one('x',K,ok(110),{'time':100,'max_snap':2})['status'],'OPEN_LEDGER_MISMATCH')
    def test_snap_relocation(self):
        q=ok(130,b=((30.11,50.1),2.))
        with patch.object(p,'pair_route',return_value=q):self.assertEqual(p.scenario_one('x',K,q,ok(100))['status'],'SNAP_LOCATION_CHANGED')
    def test_snap_recovery(self):
        q=ok(130,b=((30.11,50.1),2.))
        with patch.object(p,'pair_route',return_value=ok(120,mode='ROUTE')):self.assertEqual(p.scenario_one('x',K,q,ok(100))['seconds'],120)
    def test_faster_than_open(self):
        with patch.object(p,'pair_route',return_value=ok(80)):self.assertEqual(p.scenario_one('x',K,ok(80),ok(100))['status'],'FASTER_THAN_OPEN')
    def test_noroute_confirmed(self):
        with patch.object(p,'pair_route',return_value=p.result('UNREACHABLE_UNCONFIRMED')):self.assertEqual(p.scenario_one('x',K,p.result('TABLE_NULL',None,A,B,'TABLE'),ok(100))['status'],'UNREACHABLE')
    def test_noroute_not_confirmed(self):
        with patch.object(p,'pair_route',return_value=p.result('UNREACHABLE_UNCONFIRMED')):self.assertEqual(p.scenario_one('x',K,p.result('TABLE_NULL',None,A,((30.12,50.1),2.),'TABLE'),ok(100))['status'],'UNREACHABLE_UNCONFIRMED')
    def test_outcome_finite(self):self.assertEqual(p.outcome(ok(100),ok(220),ok(160)),('FINITE_PAIRED',1.))
    def test_outcome_rounding(self):self.assertEqual(p.outcome(ok(100),ok(160),ok(160.5)),('FINITE_PAIRED',0.))
    def test_outcome_nesting(self):self.assertEqual(p.outcome(ok(100),ok(160),ok(180)),('NESTING_VIOLATION',None))
    def test_outcome_connectivity(self):self.assertEqual(p.outcome(ok(100),p.result('UNREACHABLE'),ok(150)),('CONNECTIVITY_RESTORED',None))
    def test_outcome_noroute_scenario(self):self.assertEqual(p.outcome(ok(100),ok(150),p.result('UNREACHABLE')),('NESTING_VIOLATION',None))
    def test_outcome_closed_unknown(self):self.assertEqual(p.outcome(ok(100),p.result('SNAP_LOCATION_CHANGED'),ok(150)),('CLOSED_UNRESOLVED',None))
    def test_outcome_open_unknown(self):self.assertEqual(p.outcome(p.result('OPEN_LEDGER_MISMATCH'),ok(200),ok(150)),('OPEN_UNPAIRED',None))
    def test_cache_hash_and_key(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'r.csv.gz';p.save_cache(f,{'a':1},{K:ok(100)});self.assertEqual(p.load_cache(f,{'a':1},{K:1})[K],ok(100));self.assertIsNone(p.load_cache(f,{'a':2},{K:1}));self.assertIsNone(p.load_cache(f,{'a':1},{K2:1}));f.write_bytes(b'x');self.assertIsNone(p.load_cache(f,{'a':1},{K:1}))
    def test_graph_base(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'g.osrm';self.assertRaises(ValueError,p.graph_parts,f);Path(str(f)+'.mldgr').write_bytes(b'x');Path(str(f)+'.cells').write_bytes(b'y');self.assertEqual(len(p.graph_parts(f)),2)
    def test_default_scenarios(self):self.assertEqual([x['scenario_id'] for x in p.read_scenarios(None)],['CLOSED','S14'])
    def test_scenario_reject_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            f=Path(td)/'s.csv';p.writecsv(f,[dict(scenario_id='CLOSED',restored_bridges='S01',graph_base='a.osrm',provenance='x')]);self.assertRaises(ValueError,p.read_scenarios,f)
    def test_aggregate_units_unknown_no_renormalization(self):
        rows=[];mass={}
        for c,m in p.COMPS:
            rows.extend([row(c,m,K,.25),row(c,m,K2,.05,route='SNAP_GT_500M')]);mass[c,m]=dict(outside=.5,same_cell=.15,table_unknown=.05,known_routed_mass=.25,draw_mass=.3)
        op={K:ok(100)};cl={K:ok(220)};sc={'OPEN':op,'S14':{K:ok(160)}}
        a,b,_,common=p.aggregate(rows,op,cl,sc,mass,{1:'GUARD'});self.assertEqual(len(a),6);self.assertAlmostEqual(b['S14','local','MAIN']['finite_minutes_per_component_unit'],.25);self.assertAlmostEqual(b['S14','local','MAIN']['unresolved_total_mass'],.75);self.assertEqual(common,{K});self.assertAlmostEqual(b['S14','local','MAIN']['guard_local_minutes_per_unit'],.25)
        fit=[dict(fold='ALL_TRAIN',Q_inter=1000,Q_local=2000,Q_U1=1000)];r=p.scale_scenarios(fit,b,sc);self.assertAlmostEqual(r[1]['MAIN_vehicle_hours_day'],12.5)
    def test_aggregate_connectivity_separate(self):
        rows=[row(c,m,K,.3) for c,m in p.COMPS]; mass={cm:dict(outside=.7,same_cell=0,table_unknown=0,known_routed_mass=.3,draw_mass=.3) for cm in p.COMPS}
        op={K:ok(100)};cl={K:p.result('UNREACHABLE')};rr,_,_,_=p.aggregate(rows,op,cl,{'OPEN':op},mass,{1:'CORE'});self.assertAlmostEqual(rr[0]['connectivity_restored_mass'],.3);self.assertEqual(rr[0]['finite_minutes_per_component_unit'],0)
    def test_witness_two_node(self):
        ref={'endpoint_start_node_id':'10','endpoint_end_node_id':'20','way_nodes_count':'2'};j={'code':'Ok','routes':[{'duration':100,'legs':[{'annotation':{'nodes':[5,10,20,30]}}]}],'waypoints':[{'location':[30,50],'distance':1},{'location':[30.1,50.1],'distance':2}]}
        with patch.object(p,'request',return_value=j):self.assertEqual(p.bridge_witnesses('x',[('S01_WAY_1',ref,K)],'OPEN')[0]['two_node_way_direct_edge'],1)
    def test_witness_multinode_no_edge_claim(self):
        ref={'endpoint_start_node_id':'10','endpoint_end_node_id':'20','way_nodes_count':'6'};j={'code':'Ok','routes':[{'duration':100,'legs':[{'annotation':{'nodes':[10,11,12,20]}}]}],'waypoints':[{'location':[30,50],'distance':1},{'location':[30.1,50.1],'distance':2}]}
        with patch.object(p,'request',return_value=j):self.assertIsNone(p.bridge_witnesses('x',[('S14_WAY_1',ref,K)],'OPEN')[0]['two_node_way_direct_edge'])
if __name__=='__main__':unittest.main()
