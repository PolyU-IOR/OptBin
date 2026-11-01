"""Optional numerical checks against independently known optimal values."""
import json
import math
import sys
import unittest
from pathlib import Path
import test_optbin as base
from test_optbin import Model, QP


def cbf(n, var, m, cones, a, b, c, extra=''):
    return ('VER\n3\n'+extra+'OBJSENSE\nMIN\nVAR\n'+f'{n} {len(var)}\n'+'\n'.join(var)+
            '\nCON\n'+f'{m} {len(cones)}\n'+'\n'.join(cones)+'\nOBJACOORD\n'+
            str(len(c))+'\n'+''.join(f'{i} {v}\n' for i,v in c)+'ACOORD\n'+str(len(a))+'\n'+
            ''.join(f'{i} {j} {v}\n' for i,j,v in a)+'BCOORD\n'+str(len(b))+'\n'+
            ''.join(f'{i} {v}\n' for i,v in b))


class SolverTests(unittest.TestCase):
    setUp=base.OptBinTests.setUp
    tearDown=base.OptBinTests.tearDown
    convert=base.OptBinTests.convert

    def test_infeasible_and_unbounded_have_no_objective(self):
        try:
            from optbin.solvers import solve
            import scs,clarabel,numpy,scipy
        except ImportError as e:self.skipTest(str(e))
        cases=[('infeasible',cbf(1,['F 1'],2,['L+ 2'],
                  [(0,0,1),(1,0,-1)],[(0,-1)],[])),
               ('unbounded',cbf(1,['F 1'],1,['L+ 1'],[(0,0,1)],[],[(0,-1)]))]
        for name,text in cases:
            path=self.convert(text,format='cbf',name=name)
            with Model(path) as model:
                for solver in ['scs','clarabel']:
                    with self.subTest(case=name,solver=solver):
                        result=solve(model,solver)
                        self.assertTrue('infeasible' in result['status'].lower() or 'unbounded' in result['status'].lower())
                        self.assertIsNone(result['objective'])
                        self.assertIsNone(result['x'])
                        self.assertIsNotNone(result['certificate'])


def test_solvers(self):
    try:
        from optbin.solvers import solve
        import scs,clarabel,numpy,scipy
    except ImportError as e:
        self.skipTest(str(e))
    cases=[]
    cases.append(('socp',(Path(__file__).resolve().parents[1]/'examples/socp.cbf').read_text(),3.))
    rsoc=cbf(3,['F 3'],4,['QR 3','L= 1'],[(0,0,1),(1,1,1),(2,2,1),(3,2,1)],[(3,-2)],[(0,1),(1,1)])
    cases.append(('rsoc',rsoc,2*math.sqrt(2)))
    exp=cbf(3,['F 3'],5,['EXP 3','L= 2'],[(0,0,1),(1,1,1),(2,2,1),(3,1,1),(4,2,1)],[(3,-1),(4,-1)],[(0,1)])
    cases.append(('exp',exp,math.e))
    power=cbf(3,['@0:POW 3'],2,['L= 2'],[(0,0,1),(1,1,1)],[(0,-1),(1,-1)],[(2,-1)],'POWCONES\n1 2\n2\n2\n2\n')
    cases.append(('power',power,-1.))
    # PSD order 3 distinguishes SCS lower-triangle order from Clarabel upper order.
    sdp='''VER
3
OBJSENSE
MIN
PSDVAR
1
3
CON
1 1
L= 1
OBJFCOORD
3
0 0 0 1
0 1 1 1
0 2 2 1
FCOORD
1
0 0 0 2 0.5
BCOORD
1
0 -1
'''
    cases.append(('sdp_var',sdp,2.))
    sdpcon='''VER
3
OBJSENSE
MIN
VAR
1 1
F 1
PSDCON
1
3
OBJACOORD
1
0 1
HCOORD
3
0 0 0 0 1
0 0 1 1 1
0 0 2 2 1
DCOORD
1
0 0 2 1
'''
    cases.append(('sdp_constraint',sdpcon,1.))
    records=[]
    for name,text,expected in cases:
        path=self.convert(text,format='cbf',name=name)
        with Model(path) as model:
            before={i:model.raw(i) for i in model.sections}
            for target in ['scs','clarabel']:
                with self.subTest(case=name,solver=target):
                    result=solve(model,target,**({'eps_abs':1e-8,'eps_rel':1e-8} if target=='scs' else {}))
                    self.assertIn(result['status'].lower(),['solved','solved/inaccurate'])
                    self.assertAlmostEqual(result['objective'],expected,delta=2e-6)
                    records.append({'case':name,'solver':target,'objective':result['objective'],
                                    'expected':expected,'status':result['status']})
            self.assertEqual(before,{i:model.raw(i) for i in model.sections})
    qp=self.convert('NAME qp\nROWS\n N obj\nCOLUMNS\n X obj -2\nQUADOBJ\n X X 2\nENDATA\n',name='qp')
    with Model(qp) as model:
        for target in ['scs','clarabel']:
            result=solve(model,target)
            self.assertAlmostEqual(result['objective'],-1.,delta=1e-5)
            records.append({'case':'qp','solver':target,'objective':result['objective'],'expected':-1.,'status':result['status']})
    lp=self.convert(QP[:QP.index('QUADOBJ')]+'ENDATA\n',name='lp')
    with Model(lp) as model:
        for target in ['scs','clarabel']:
            result=solve(model,target)
            self.assertAlmostEqual(result['objective'],-1.,delta=2e-5)
            records.append({'case':'lp','solver':target,'objective':result['objective'],'expected':-1.,'status':result['status']})
    native_qc=self.convert(QP.replace('ENDATA','QCMATRIX qc\n X X 1\nENDATA'),name='qc',args=['--mps-dialect','gurobi'])
    with Model(native_qc) as model:
        with self.assertRaisesRegex(ValueError,'QCQP'): solve(model)
    nonconvex=self.convert('NAME qp\nROWS\n N obj\nCOLUMNS\n X obj -2\nQUADOBJ\n X X -2\nENDATA\n',name='nonconvex')
    with Model(nonconvex) as model:
        with self.assertRaisesRegex(ValueError,'nonconvex'): solve(model)
    maximal=self.convert('NAME qp\nOBJSENSE\n MAX\nROWS\n N obj\nCOLUMNS\n X obj 2\nQUADOBJ\n X X -2\nENDATA\n',name='maximal')
    with Model(maximal) as model:
        for target in ['scs','clarabel']:
            self.assertAlmostEqual(solve(model,target)['objective'],1.,delta=1e-5)
    impossible=self.root/'impossible.optb'
    sections=[(id,type,values if id!=7 else [math.inf,0.]) for id,type,values in base.MINIMAL]
    base.raw_file(impossible,sections)
    with Model(impossible) as model:
        with self.assertRaisesRegex(ValueError,'infeasible infinite bounds'): solve(model)
    report=Path(__file__).resolve().parents[1]/'benchmarks/solver-smoke.json'
    report.write_text(json.dumps({'scope':'Small analytical fixtures; not large benchmark performance',
        'versions':{'scs':scs.__version__,'clarabel':clarabel.__version__,'numpy':numpy.__version__,'scipy':scipy.__version__},
        'results':records},indent=2)+'\n')


SolverTests.test_solver_analytical_fixtures=test_solvers
if __name__=='__main__': unittest.main(verbosity=2)
