import sys,unittest,tempfile,json,math
from pathlib import Path
from decimal import Decimal as D
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'code'))
import p1d as m
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
class T(unittest.TestCase):
    def test_greedy_budget_and_skip(self):
        s,c,r=m.greedy({'a':100,'b':110,'c':5},{'a':D(4),'b':D(5),'c':D(1)},D(5));self.assertEqual(s,{'a','c'});self.assertEqual(c,5)
    def test_tie_deterministic(self):
        s,c,r=m.greedy({'b':10,'a':10},{'b':D(1),'a':D(1)},D(1));self.assertEqual(s,{'a'})
    def test_no_missing(self):
        with self.assertRaises(ValueError):m.greedy({'a':2},{'a':D(1),'b':D(1)},D(1))
    def test_actual_registry(self):
        c,cost,total,w=m.b.candidates();self.assertEqual(total,D('9062287919.44'));self.assertEqual(len(m.centers(w)),19);self.assertEqual(w['S19'],{284329114})
    def test_circle(self):
        g=m.circle(30.,50.);self.assertTrue(g.is_valid)
        from pyproj import Transformer
        inv=Transformer.from_crs('ESRI:54009',4326,always_xy=True)
        x,y=inv.transform(*g.exterior.coords[42]);_,_,dist=m.GEOD.inv(30.,50.,x,y)
        self.assertAlmostEqual(dist,20000,places=2)
    def test_annuity(self):self.assertAlmostEqual(m.annuity(20,.05),12.462210342539992,places=10)
    def test_finance(self):
        with tempfile.TemporaryDirectory() as d:
            m.finance([{'scenario_id':'a','scale_scenario':'BASE','MAIN_common_vehicle_hours_day':100}],{'a':D(1000000)},Path(d));r=m.p.readcsv(Path(d)/'ANNUAL_TIME_VALUE_SENSITIVITY.csv');self.assertEqual(len(r),3);self.assertEqual(float(r[1]['annual_time_value_UAH']),3650000.)
    def test_raster_missingness_and_alignment(self):
        with tempfile.TemporaryDirectory() as d:
            paths=[]
            for i,a in enumerate([np.array([[2.,-200.],[3.,4.]],dtype='float32'),np.array([[1.,2.],[3.,4.]],dtype='float32')]):
                f=Path(d)/f'{i}.tif';paths.append(f)
                with rasterio.open(f,'w',driver='GTiff',width=2,height=2,count=1,dtype='float32',crs='ESRI:54009',transform=from_origin(i*200,200,100,100),nodata=-200) as ds:ds.write(a,1)
            q=m.raster_population(box(0,0,400,200),paths);self.assertEqual(q['pop_known'],19);self.assertEqual(q['pixels_expected'],8);self.assertEqual(q['pixels_nodata'],1)
            with self.assertRaises(ValueError):m.raster_population(box(0,0,400,200),paths[:1])
            with self.assertRaises(ValueError):m.raster_population(box(0,0,400,200),paths+paths[:1])
    def test_comparison_common(self):
        with tempfile.TemporaryDirectory() as d:
            rr=[dict(scale_scenario='x',scenario_id=s,MAIN_common_vehicle_hours_day=h,U1_common_vehicle_hours_day=h/2) for s,h in [(m.BASE,10),('B20',12),('N03',9)]];m.pair_comparison(rr,{m.BASE:D(1),'B20':D(2),'N03':D(1)},Path(d));z=m.p.readcsv(Path(d)/'SIMPLE_VS_ALGORITHM.csv');self.assertEqual(len(z),2);self.assertAlmostEqual(float(z[0]['relative_gain_MAIN']),.2);self.assertEqual(float(z[1]['delta_hours_day_MAIN']),-1)
if __name__=='__main__':unittest.main()
