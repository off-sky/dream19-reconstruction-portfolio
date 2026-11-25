import csv,json,pathlib,sys,tempfile,unittest
from unittest.mock import patch
import numpy as np
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]/'code'))
import p0d as m

class GraphFixTests(unittest.TestCase):
 def setUp(self):
  self.xy=np.array([[25.,49.]]*m.N); self.T=np.full((m.N,m.N),60.);np.fill_diagonal(self.T,0.)
 def test_raw_and_demand_snap_separate(self):
  answer={'code':'Ok','waypoints':[{'distance':1500.},{'distance':2.}],'routes':[{'duration':3600.,'distance':50000.,'geometry':{'coordinates':[[25.,49.],[26.,49.]]}}]}
  with patch.object(m,'get_json',return_value=answer):
   raw,status=m.raw_route('mock',(25,49),(26,49));self.assertEqual(status,'OK');self.assertEqual(raw[3],1500.);self.assertEqual(raw[0],60.)
   self.assertEqual(m.route('mock',(25,49),(26,49)),(None,'SNAP_GT_500M'))
 def test_legacy_fingerprint_accepts_matching_time_not_access(self):
  results=[((60.,50000.,[[25,49],[26,49]],(800. if k<8 else 10.)),'OK') for k in range(12)]+[((62.,50000.,[[25,49],[26,49]],10.),'OK')]*12
  with tempfile.TemporaryDirectory() as d,patch.object(m,'raw_route',side_effect=results):
   out=pathlib.Path(d);self.assertEqual(m.preflight('mock',self.xy,self.T,out),'old_reference_time_fingerprint_12_of_12');s=json.loads((out/'GRAPH_PREFLIGHT_SUMMARY.json').read_text());self.assertEqual(s['old']['n_snap_le_500'],4);self.assertEqual(s['old']['n_time_match_1s'],12);self.assertEqual(s['new']['n_time_match_1s'],0);self.assertFalse(s['raw_routes_used_for_demand']);
   with (out/'GRAPH_PREFLIGHT.csv').open() as f: rows=list(csv.DictReader(f));self.assertEqual(len(rows),24);self.assertEqual(rows[0]['demand_route_status'],'SNAP_GT_500M');self.assertEqual(float(rows[0]['live_min']),60.)
 def test_wrong_legacy_time_rejected_even_if_new_matches(self):
  r=((60.,100.,[[25,49],[26,49]],10.),'OK');wrong=((60.1,100.,[[25,49],[26,49]],10.),'OK')
  with tempfile.TemporaryDirectory() as d,patch.object(m,'raw_route',side_effect=[wrong]+[r]*23):
   out=pathlib.Path(d)
   with self.assertRaisesRegex(AssertionError,'LEGACY_REFERENCE_TIME_FINGERPRINT_FAIL'):m.preflight('mock',self.xy,self.T,out)
   self.assertEqual(json.loads((out/'GRAPH_PREFLIGHT_SUMMARY.json').read_text())['old']['n_time_match_1s'],11)
 def test_unavailable_reference_route_rejected(self):
  r=((60.,100.,[[25,49],[26,49]],10.),'OK')
  with tempfile.TemporaryDirectory() as d,patch.object(m,'raw_route',side_effect=[(None,'NoRoute')]+[r]*23):
   out=pathlib.Path(d)
   with self.assertRaises(AssertionError):m.preflight('mock',self.xy,self.T,out)
   self.assertEqual(json.loads((out/'GRAPH_PREFLIGHT_SUMMARY.json').read_text())['old']['n_raw_ok'],11)
 def test_nonfinite_route_rejected(self):
  answer={'code':'Ok','waypoints':[{'distance':float('nan')},{'distance':2.}],'routes':[{'duration':3600.,'distance':50000.,'geometry':{'coordinates':[[25.,49.],[26.,49.]]}}]}
  with patch.object(m,'get_json',return_value=answer):self.assertEqual(m.raw_route('mock',(25,49),(26,49)),(None,'DEGENERATE_ROUTE'))
if __name__=='__main__':unittest.main()
