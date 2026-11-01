"""Optional native QCQP backends; known optima and original-model residuals."""
import math
import unittest
import test_optbin as base
from optbin import Model


class QuadraticSolvers(unittest.TestCase):
    setUp=base.OptBinTests.setUp
    tearDown=base.OptBinTests.tearDown
    convert=base.OptBinTests.convert

    def run_case(self,text,expected,dialect='gurobi'):
        from optbin.quadratic import solve_scip,solve_gurobi
        path=self.convert(text,args=['--mps-dialect',dialect])
        with Model(path) as m:
            results=[]
            try:
                import pyscipopt
                results.append(solve_scip(m,time_limit=15))
            except ImportError:pass
            try:
                import gurobipy as gp
                try:results.append(solve_gurobi(m,TimeLimit=15,NonConvex=2))
                except gp.GurobiError as e:
                    if e.errno not in (10009,10010):raise
            except ImportError:pass
            if not results:self.skipTest('No installed/licensed native quadratic backend')
            for result in results:
                self.assertIn(result['status'],[2,'optimal'])
                self.assertAlmostEqual(result['objective'],expected,delta=2e-6)
                self.assertLess(result['residuals']['row_absolute'],2e-6)
                self.assertLess(result['residuals']['bound_absolute'],2e-6)

    def test_convex_ball_both_dialects(self):
        for dialect,diag in [('gurobi',1),('mosek',1)]:
            with self.subTest(dialect=dialect):
                text=f'''NAME ball
ROWS
 N obj
 L ball
COLUMNS
 X obj -1
 Y obj 0
RHS
 rhs ball 1
BOUNDS
 FR b X
 FR b Y
QCMATRIX ball
 X X {diag}
 Y Y {diag}
ENDATA
'''
                # A fresh filename is needed inside the same test method.
                path=self.root/'model.optb'
                if path.exists():path.unlink()
                self.run_case(text,-1.,dialect)

    def test_nonconvex_bilinear_constraint(self):
        self.run_case('''NAME bilinear
ROWS
 N obj
 L product
COLUMNS
 X obj -1
 Y obj -1
RHS
 rhs product .2
BOUNDS
 UP b X 1
 UP b Y 1
QCMATRIX product
 X Y .5
 Y X .5
ENDATA
''',-1.2)

    def test_quadratic_objective_and_constraint(self):
        self.run_case(base.QP.replace('ENDATA','QCMATRIX qc\n X X 2\n X Y 3\n Y X 3\n Y Y 4\nENDATA'),13.)

    def test_vendor_mosek_source_reader(self):
        try:import mosek
        except ImportError:self.skipTest('MOSEK SDK not installed (reading requires no solver license)')
        for section,mirror in [('QCMATRIX',' Y X 3\n'),('QSECTION','')]:
            text=base.QP.replace('QUADOBJ','QSECTION obj').replace('ENDATA',
                section+' qc\n X X 2\n X Y 3\n'+mirror+' Y Y 4\nENDATA')
            path=self.convert(text,name=section,args=['--mps-dialect','mosek'])
            with mosek.Task() as t:
                t.readdata(str(self.root/(section+'.mps')))
                _,ii,jj,v=t.getqconk(1)
                expected={(min(i,j),max(i,j)):value*.5*(1 if i==j else 2) for i,j,value in zip(ii,jj,v)}
                with Model(path) as model:
                    rows=model.numpy(14);cols=model.numpy(15);values=model.numpy(16);scale=model.numpy(17)[0]
                    actual={(int(i),int(j)):float(value*scale)*(1 if i==j else 2) for i,j,value in zip(rows,cols,values)}
                    self.assertEqual(actual,expected)

    def test_quadratic_equality(self):
        self.run_case('''NAME equality
ROWS
 N obj
 E square
COLUMNS
 X obj 1
RHS
 rhs square 1
BOUNDS
 LO b X -2
 UP b X 2
QCMATRIX square
 X X 1
ENDATA
''',-1.)


if __name__=='__main__':unittest.main(verbosity=2)
