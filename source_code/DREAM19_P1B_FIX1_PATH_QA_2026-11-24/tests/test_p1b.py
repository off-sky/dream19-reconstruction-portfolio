import sys,unittest,tempfile,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'code'))
import p1b as b
class T(unittest.TestCase):
 def test_registry(self):
  c,cost,total,way=b.candidates();self.assertEqual(len(c),6);self.assertEqual(str(total),'9062287919.44');self.assertEqual(way['S19'],{284329114});self.assertEqual(len(b.closure_for(set(),way)),22);self.assertEqual(len(b.closure_for(c['A18_EXCEPT_S14']['selected'],way)),2)
 def test_literal(self):
  s='local blocked = { [11] = true, [12] = true }\nreturn blocked\n';t=b.table_templates(s,{11,12});self.assertEqual(len(t),1);new=b.render(t[0],{12});n=b.table_templates(new,{12});self.assertEqual(len(n),1);self.assertEqual(b.canonical(t[0]),b.canonical(n[0]));self.assertIn('[12] = true',new);self.assertNotIn('[11]',new)
 def test_list(self):
  s='local blocked = {11, 12, 13}\nreturn blocked';t=b.table_templates(s,{11,12,13})[0];self.assertEqual(b.render(t,{11,13}),'local blocked = {11, 13}\nreturn blocked')
 def test_string_set(self):
  s='local b = {["11"]=true,["12"]=true}\nreturn b';t=b.table_templates(s,{11,12})[0];self.assertIn('["12"] = true',b.render(t,{12}))
 def test_reject_extra_or_duplicate(self):
  self.assertFalse(b.table_templates('local x={11,12,99}',{11,12}));self.assertFalse(b.table_templates('local x={11,12,12}',{11,12}));self.assertFalse(b.table_templates('local x={11,12,foo()}',{11,12}))
 def test_assign(self):
  s='local x={}\nx[11]=true\nx[12]=true\nreturn x\n';t=b.table_templates(s,{11,12})[0];n=b.render(t,{12});self.assertEqual(b.canonical(t),b.canonical(b.table_templates(n,{12})[0]))
 def test_assign_intervening_executable(self):self.assertFalse(b.table_templates('x[11]=true\nfoo()\nx[12]=true\n',{11,12}))
 def test_profile_match(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);c=root/'closed.lua';s=root/'s14.lua';o=root/'out';o.mkdir();c.write_text('local x={11,12,13}\nreturn x');s.write_text('local x={12,13}\nreturn x');p,t=b.scan_profiles(root,{11,12,13},{12,13},o,[str(c),str(s)]);self.assertEqual(p,c.resolve());self.assertEqual(t['kind'],'list')
 def test_profile_root_alias_explicit(self):
  # Reproduce /var -> /private/var with portable temporary symlink.
  with tempfile.TemporaryDirectory() as td:
   base=Path(td);real=base/'private'/'var';real.mkdir(parents=True);alias=base/'var';alias.symlink_to(real,target_is_directory=True)
   c=alias/'closed.lua';s=alias/'s14.lua';o=base/'out';o.mkdir();c.write_text('local x={11,12,13}\nreturn x');s.write_text('local x={12,13}\nreturn x')
   p,t=b.scan_profiles(alias,{11,12,13},{12,13},o,[str(c),str(s)]);self.assertEqual(p,c.resolve());self.assertEqual(t['kind'],'list');self.assertIn('closed.lua', (o/'PROFILE_INVENTORY.csv').read_text());self.assertNotIn(str(real.resolve()),(o/'PROFILE_INVENTORY.csv').read_text())
 def test_profile_root_alias_auto(self):
  with tempfile.TemporaryDirectory() as td:
   base=Path(td);real=base/'real';real.mkdir();alias=base/'alias';alias.symlink_to(real,target_is_directory=True);o=base/'out';o.mkdir()
   c=real/'closed.lua';s=real/'s14.lua';c.write_text('local x={11,12,13}\nreturn x');s.write_text('local x={12,13}\nreturn x');(real/'duplicate.lua').symlink_to(c)
   p,t=b.scan_profiles(alias,{11,12,13},{12,13},o);self.assertEqual(p,c.resolve());self.assertEqual(len((o/'PROFILE_INVENTORY.csv').read_text().splitlines()),3)
 def test_profile_outside_explicit_rejected(self):
  with tempfile.TemporaryDirectory() as td:
   base=Path(td);root=base/'bridges';root.mkdir();o=base/'out';o.mkdir();c=base/'outside.lua';c.write_text('local x={11,12,13}\nreturn x');link=root/'closed.lua';link.symlink_to(c);s=root/'s14.lua';s.write_text('local x={12,13}\nreturn x')
   with self.assertRaisesRegex(ValueError,'PROFILE_OUTSIDE_BRIDGES'):b.scan_profiles(root,{11,12,13},{12,13},o,[str(link),str(s)])
 def test_profile_outside_auto_skipped(self):
  with tempfile.TemporaryDirectory() as td:
   base=Path(td);root=base/'bridges';root.mkdir();o=base/'out';o.mkdir();c=base/'outside.lua';c.write_text('local x={11,12,13}\nreturn x');(root/'closed.lua').symlink_to(c);s=root/'s14.lua';s.write_text('local x={12,13}\nreturn x')
   with self.assertRaisesRegex(ValueError,'NO_MATCHING_NATIVE'):b.scan_profiles(root,{11,12,13},{12,13},o)
   self.assertNotIn('closed.lua',(o/'PROFILE_INVENTORY.csv').read_text())
 def test_profile_logic_reject(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td);c=root/'closed.lua';s=root/'s14.lua';o=root/'out';o.mkdir();c.write_text('local x={11,12,13}\nreturn x');s.write_text('local x={12,13}\nreturn y');self.assertRaises(ValueError,b.scan_profiles,root,{11,12,13},{12,13},o,[str(c),str(s)])
 def test_regret(self):
  c,_,total,_=b.candidates();rr=[dict(scenario_id=s,scale_scenario=f,MAIN_common_vehicle_hours_day=(100-i*5 if f=='A' else 80+i*3)) for i,s in enumerate(b.CANDIDATES) for f in ['A','B']];r,rank=b.regrets(rr,c,total);self.assertEqual(len(r),10);self.assertEqual(len(rank),5);self.assertEqual(rank[0]['relative_minimax_rank'],1);self.assertTrue(all(x['max_relative_regret']>=0 for x in rank));self.assertEqual(b.regrets(rr[:-1],c,total),([],[]))
if __name__=='__main__':unittest.main()
