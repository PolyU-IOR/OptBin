"""Native LP/QP/QCQP evaluation and optional quadratic-solver adapters.

The storage model is not reformulated into cones. Gurobi uses its quadratic API;
SCIP adds an objective epigraph/hypograph because its objective API is linear.
"""
import math


def arrays(model):
    import numpy as np
    from scipy import sparse
    if model.info['profile']!=1:
        raise ValueError('quadratic solver adapter requires the bounded quadratic profile')
    a={id:model.numpy(id) for id in model.sections}
    if np.any(np.isposinf(a[7])) or np.any(np.isneginf(a[8])):
        raise ValueError('infeasible infinite variable bounds cannot be omitted')
    m,n=model.info['rows'],model.info['cols']
    a['A']=sparse.csc_matrix((a[3],a[2].astype('int64'),a[1].astype('int64')),shape=(m,n)).tocsr()
    return a


def objective(model,x):
    import numpy as np
    a=arrays(model);x=np.asarray(x,dtype=float)
    value=model.info['objective_offset']+float(a[4]@x)
    if len(a.get(11,[])):
        for col in range(model.info['cols']):
            for k in range(int(a[9][col]),int(a[9][col+1])):
                row=int(a[10][k]);value+=float(a[11][k])*x[row]*x[col]*(.5 if row==col else 1.)
    return float(value)


def activities(model,x):
    import numpy as np
    a=arrays(model);x=np.asarray(x,dtype=float);values=a['A']@x
    for j,row in enumerate(a.get(12,[])):
        lo,hi=int(a[13][j]),int(a[13][j+1]);i=a[14][lo:hi].astype('int64');col=a[15][lo:hi].astype('int64')
        values[int(row)]+=a[17][j]*np.sum(a[16][lo:hi]*x[i]*x[col]*np.where(i==col,1.,2.))
    return values


def residuals(model,x):
    import numpy as np
    a=arrays(model);x=np.asarray(x,dtype=float);g=activities(model,x)
    def violation(v,lo,hi):
        error=np.maximum(np.maximum(lo-v,v-hi),0.)
        scale=1.+np.abs(v)
        return float(np.max(error,initial=0.)),float(np.max(error/scale,initial=0.))
    row,relative=violation(g,a[5],a[6]);bounds,brelative=violation(x,a[7],a[8])
    return {'row_absolute':row,'row_relative':relative,'bound_absolute':bounds,'bound_relative':brelative}


def to_gurobi(model,env):
    import gurobipy as gp
    import numpy as np
    a=arrays(model);n=model.info['cols'];g=gp.Model(env=env)
    x=g.addVars(n,lb=a[7].tolist(),ub=a[8].tolist(),vtype=gp.GRB.CONTINUOUS,name='x')
    var=[x[i] for i in range(n)]
    obj=gp.QuadExpr(gp.LinExpr(a[4].tolist(),var));obj+=model.info['objective_offset']
    if len(a.get(11,[])):
        for col in range(n):
            begin,end=int(a[9][col]),int(a[9][col+1])
            if begin==end:continue
            rows=a[10][begin:end].astype('int64')
            coefficients=a[11][begin:end]*np.where(rows==col,.5,1.)
            if not np.all(np.isfinite(coefficients)):raise ValueError('quadratic coefficient overflow')
            obj.addTerms(coefficients.tolist(),[var[i] for i in rows],[var[col]]*len(rows))
    g.setObjective(obj,gp.GRB.MINIMIZE if model.info['sense']==1 else gp.GRB.MAXIMIZE)
    qc={int(row):k for k,row in enumerate(a.get(12,[]))};A=a['A']
    for row in range(model.info['rows']):
        begin,end=A.indptr[row:row+2]
        expression=gp.QuadExpr(gp.LinExpr(A.data[begin:end].tolist(),[var[i] for i in A.indices[begin:end]]))
        if row in qc:
            j=qc[row];lo,hi=int(a[13][j]),int(a[13][j+1]);i=a[14][lo:hi].astype('int64');col=a[15][lo:hi].astype('int64')
            coefficients=a[17][j]*a[16][lo:hi]*np.where(i==col,1.,2.)
            if not np.all(np.isfinite(coefficients)):raise ValueError('quadratic coefficient overflow')
            expression.addTerms(coefficients.tolist(),[var[k] for k in i],[var[k] for k in col])
        lower,upper=float(a[5][row]),float(a[6][row])
        if lower==math.inf or upper==-math.inf:raise ValueError('infeasible infinite row bound')
        if lower==upper:g.addConstr(expression==lower,name=f'r{row}_eq')
        else:
            if math.isfinite(lower):g.addConstr(expression>=lower,name=f'r{row}_lower')
            if math.isfinite(upper):g.addConstr(expression<=upper,name=f'r{row}_upper')
    g.update()
    return g,var


def solve_gurobi(model,**parameters):
    import gurobipy as gp
    with gp.Env(empty=True) as env:
        env.setParam('OutputFlag',0);env.start()
        g,var=to_gurobi(model,env)
        try:
            g.setParam('Threads',1)
            for name,value in parameters.items():g.setParam(name,value)
            g.optimize()
            result={'status':int(g.Status),'runtime':g.Runtime,'objective':None,'x':None}
            if g.SolCount:
                result['x']=[v.X for v in var];result['objective']=objective(model,result['x'])
                result['residuals']=residuals(model,result['x'])
                result['solver_objective']=g.ObjVal
            return result
        finally:g.dispose()


def solve_scip(model,time_limit=30.,**parameters):
    import numpy as np
    from pyscipopt import Model as ScipModel,quicksum
    a=arrays(model);n=model.info['cols'];g=ScipModel();g.hideOutput()
    g.setParam('limits/time',float(time_limit));g.setParam('parallel/maxnthreads',1)
    for name,value in parameters.items():g.setParam(name,value)
    infinity=g.infinity()
    x=[g.addVar(name=f'x{i}',vtype='C',lb=float(a[7][i]) if math.isfinite(a[7][i]) else -infinity,
                ub=float(a[8][i]) if math.isfinite(a[8][i]) else infinity) for i in range(n)]
    obj=quicksum(float(c)*x[i] for i,c in enumerate(a[4]))+model.info['objective_offset']
    if len(a.get(11,[])):
        for col in range(n):
            for k in range(int(a[9][col]),int(a[9][col+1])):
                row=int(a[10][k]);obj+=float(a[11][k])*(.5 if row==col else 1.)*x[row]*x[col]
    if len(a.get(11,[])):
        aux=g.addVar(name='objective_aux',lb=-infinity,ub=infinity)
        g.addCons(aux>=obj if model.info['sense']==1 else aux<=obj)
        g.setObjective(aux,'minimize' if model.info['sense']==1 else 'maximize')
    else:g.setObjective(obj,'minimize' if model.info['sense']==1 else 'maximize')
    qc={int(row):k for k,row in enumerate(a.get(12,[]))};A=a['A']
    for row in range(model.info['rows']):
        begin,end=A.indptr[row:row+2];expression=quicksum(float(v)*x[int(i)] for i,v in zip(A.indices[begin:end],A.data[begin:end]))
        if row in qc:
            j=qc[row]
            for k in range(int(a[13][j]),int(a[13][j+1])):
                i,col=int(a[14][k]),int(a[15][k]);coefficient=float(a[17][j]*a[16][k])*(1. if i==col else 2.)
                if not math.isfinite(coefficient):raise ValueError('quadratic coefficient overflow')
                expression+=coefficient*x[i]*x[col]
        lower,upper=float(a[5][row]),float(a[6][row])
        if lower==math.inf or upper==-math.inf:raise ValueError('infeasible infinite row bound')
        if lower==upper:g.addCons(expression==lower)
        else:
            if math.isfinite(lower):g.addCons(expression>=lower)
            if math.isfinite(upper):g.addCons(expression<=upper)
    g.optimize();sol=g.getBestSol()
    result={'status':str(g.getStatus()),'runtime':g.getSolvingTime(),'objective':None,'x':None}
    if sol is not None:
        result['x']=[g.getSolVal(sol,v) for v in x];result['objective']=objective(model,result['x'])
        result['residuals']=residuals(model,result['x']);result['solver_objective']=g.getSolObjVal(sol)
    g.freeProb()
    return result
