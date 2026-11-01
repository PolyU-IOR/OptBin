"""Independent CBLIB-parser -> CVXPY modeling path and original-data evaluation.

No OptBin arrays, cone adapter or matrix-vectorization routine builds this model.
CVXPY owns its own canonicalization and PSD vectorization.
"""
import math


def build(ref):
    import cvxpy as cp
    import numpy as np
    from scipy import sparse
    n,m=ref.varnum,ref.mapnum
    x=cp.Variable(n) if n else None
    X=[cp.Variable((d,d),PSD=True) for d in ref.psdvardim]
    b=np.zeros(m)
    for i,v in zip(ref.bsubi,ref.bval):b[i]=v
    if n and m:
        A=sparse.coo_matrix((ref.aval,(ref.asubi,ref.asubj)),shape=(m,n)).tocsc()
        affine=A@x+b
    else:affine=cp.Constant(b) if m else None
    if ref.fnnz:
        terms=[[] for _ in range(m)]
        for row,j,i,k,v in zip(ref.fsubi,ref.fsubj,ref.fsubk,ref.fsubl,ref.fval):
            terms[row].append(v*(1 if i==k else 2)*X[j][i,k])
        affine=affine+cp.hstack([sum(t) for t in terms])
    obj=ref.objbval
    for i,v in zip(ref.objasubj,ref.objaval):obj+=v*x[i]
    for j,i,k,v in zip(ref.objfsubj,ref.objfsubk,ref.objfsubl,ref.objfval):obj+=v*(1 if i==k else 2)*X[j][i,k]
    constraints=[]
    def domains(expr,kinds,dims):
        start=0
        for kind,dim in zip(kinds,dims):
            e=expr[start:start+dim];start+=dim
            if kind=='F':continue
            if kind=='L+':constraints.append(e>=0)
            elif kind=='L-':constraints.append(e<=0)
            elif kind=='L=':constraints.append(e==0)
            elif kind=='Q':constraints.append(cp.SOC(e[0],e[1:]) if dim>1 else e[0]>=0)
            elif kind=='QR':
                tail=[e[0]-e[1]]
                if dim>2:tail.append(math.sqrt(2)*e[2:])
                constraints.append(cp.SOC(e[0]+e[1],cp.hstack(tail)))
            elif kind=='EXP':constraints.append(cp.ExpCone(e[2],e[1],e[0]))
            elif kind=='EXP*':constraints.append(cp.ExpCone(-e[1],-e[2],math.e*e[0]))
            else:raise ValueError('independent reference does not implement this cone: '+kind)
    if n:domains(x,ref.varstackdomain,ref.varstackdim)
    if m:domains(affine,ref.mapstackdomain,ref.mapstackdim)
    for block,size in enumerate(ref.psdmapdim):
        D=np.zeros((size,size));H={}
        for j,i,k,v in zip(ref.dsubi,ref.dsubk,ref.dsubl,ref.dval):
            if j==block:D[i,k]=D[k,i]=v
        for j,var,i,k,v in zip(ref.hsubi,ref.hsubj,ref.hsubk,ref.hsubl,ref.hval):
            if j!=block:continue
            if var not in H:H[var]=np.zeros((size,size))
            H[var][i,k]=H[var][k,i]=v
        G=cp.Constant(D)
        for var,value in H.items():G=G+x[var]*value
        constraints.append(G>>0)
    return cp.Problem(cp.Minimize(obj) if ref.obj=='MIN' else cp.Maximize(obj),constraints),x,X


def unpack(ref,z):
    import numpy as np
    z=np.asarray(z);x=z[:ref.varnum];position=ref.varnum;matrices=[]
    for size in ref.psdvardim:
        X=np.zeros((size,size))
        for j in range(size):
            for i in range(j+1):X[i,j]=X[j,i]=z[position];position+=1
        matrices.append(X)
    if position!=len(z):raise ValueError('solution dimension does not match original CBF domains')
    return x,matrices


def evaluate(ref,z):
    import numpy as np
    x,X=unpack(ref,z);g=np.zeros(ref.mapnum)
    for i,v in zip(ref.bsubi,ref.bval):g[i]=v
    for i,j,v in zip(ref.asubi,ref.asubj,ref.aval):g[i]+=v*x[j]
    for row,j,i,k,v in zip(ref.fsubi,ref.fsubj,ref.fsubk,ref.fsubl,ref.fval):g[row]+=v*(1 if i==k else 2)*X[j][i,k]
    objective=ref.objbval
    for i,v in zip(ref.objasubj,ref.objaval):objective+=v*x[i]
    for j,i,k,v in zip(ref.objfsubj,ref.objfsubk,ref.objfsubl,ref.objfval):objective+=v*(1 if i==k else 2)*X[j][i,k]
    errors=[]
    def record(value,scale):errors.append((max(float(value),0.),1.+float(scale)))
    def exp_violation(t,s,r):
        if s>0:
            try:return max(s*math.exp(r/s)-t,0.)
            except OverflowError:return math.inf
        return max(-s,-t,r,0.)
    def domains(expr,kinds,dims):
        start=0
        for kind,dim in zip(kinds,dims):
            e=expr[start:start+dim];start+=dim;scale=np.max(np.abs(e),initial=0.)
            if kind=='F':error=0.
            elif kind=='L+':error=np.max(-e,initial=0.)
            elif kind=='L-':error=np.max(e,initial=0.)
            elif kind=='L=':error=np.max(np.abs(e),initial=0.)
            elif kind=='Q':error=np.linalg.norm(e[1:])-e[0]
            elif kind=='QR':error=np.linalg.norm(np.r_[e[0]-e[1],math.sqrt(2)*e[2:]])-e[0]-e[1]
            elif kind=='EXP':error=exp_violation(*e)
            elif kind=='EXP*':error=exp_violation(math.e*e[0],-e[2],-e[1])
            else:raise ValueError('unsupported original cone in evaluator')
            record(error,scale)
    domains(x,ref.varstackdomain,ref.varstackdim);domains(g,ref.mapstackdomain,ref.mapstackdim)
    for matrix in X:record(-np.linalg.eigvalsh(matrix).min(),np.linalg.norm(matrix,2))
    for block,size in enumerate(ref.psdmapdim):
        matrix=np.zeros((size,size))
        for j,i,k,v in zip(ref.dsubi,ref.dsubk,ref.dsubl,ref.dval):
            if j==block:matrix[i,k]=matrix[k,i]=v
        for j,var,i,k,v in zip(ref.hsubi,ref.hsubj,ref.hsubk,ref.hsubl,ref.hval):
            if j==block:
                matrix[i,k]+=v*x[var]
                if i!=k:matrix[k,i]+=v*x[var]
        record(-np.linalg.eigvalsh(matrix).min(),np.linalg.norm(matrix,2))
    return {'objective':float(objective),'primal_absolute':max((e for e,s in errors),default=0.),
            'primal_relative':max((e/s for e,s in errors),default=0.)}


def reference_solve(ref,time_limit=20.):
    import numpy as np
    problem,x,X=build(ref)
    value=problem.solve(solver='CLARABEL',time_limit=time_limit)
    result={'status':problem.status,'objective':float(value) if problem.status in ('optimal','optimal_inaccurate') else None}
    if x is None or x.value is not None:
        if all(M.value is not None for M in X):
            z=list(np.asarray(x.value).ravel()) if x is not None else []
            for M in X:
                for j in range(M.shape[0]):
                    for i in range(j+1):z.append(M.value[i,j])
            result['original_model']=evaluate(ref,z)
    return result
