"""CPU SCS/Clarabel adapters for LP/QP and continuous CBF profiles.

All reformulation/scaling happens here, outside the lossless storage reader.
Native quadratic constraints are intentionally retained by storage and currently
require a QCQP solver adapter; these affine-conic adapters reject them.
"""
import math


def _upper_index(i, j):
    return j * (j + 1) // 2 + i


def _svec_index(i, j, size, target):
    return (_upper_index(i, j) if target == 'clarabel'
            else i * size - i * (i - 1) // 2 + j - i)


def _psd_check(P):
    import numpy as np
    from scipy.sparse.linalg import eigsh
    if not P.nnz:
        return
    tolerance = 1e-10 * max(1., float(abs(P).sum(axis=1).max()))
    if P.shape[0] <= 256:
        minimum = np.linalg.eigvalsh(P.toarray())[0]
    elif np.all(P.diagonal() >= np.asarray(abs(P).sum(axis=1)).ravel() - abs(P.diagonal())):
        return  # Symmetric diagonal dominance with nonnegative diagonal.
    else:
        minimum = eigsh(P, k=1, which='SA', return_eigenvectors=False)[0]
    if minimum < -tolerance:
        raise ValueError('quadratic objective is nonconvex for the requested sense')


def problem(model, target='scs'):
    """Build solver arrays. Returns P, c, A, b, cone metadata.

    Cone parameters and original coefficients remain unchanged in Model.
    Expanded PSD variable coordinates use the unscaled upper triangle internally.
    """
    import numpy as np
    from scipy import sparse as sp
    if target not in ('scs','clarabel'):
        raise ValueError('unknown solver')
    cache={}
    def get(id):
        if id not in cache: cache[id]=model.numpy(id)
        return cache[id]
    n, m = model.info['cols'], model.info['rows']
    if len(get(12)):
        raise ValueError('native QCQP constraints require a QCQP adapter; no automatic conic reformulation')
    psd_sizes = get(27).astype('int64')
    starts, total = [], n
    for size in psd_sizes:
        starts.append(total); total += int(size) * (int(size)+1)//2
    scalar = sp.csc_matrix((get(3),get(2).astype('int64'),get(1).astype('int64')),shape=(m,n))
    G = sp.hstack([scalar,sp.csc_matrix((m,total-n))],format='csr')
    c = np.zeros(total); c[:n] = get(4)
    if len(get(11)):
        upper = sp.csc_matrix((get(11),get(10).astype('int64'),get(9).astype('int64')),shape=(n,n))
        symmetric = upper + sp.triu(upper,k=1,format='csc').T
        P = sp.block_diag((symmetric,sp.csc_matrix((total-n,total-n))),format='csc')
    else:
        P = sp.csc_matrix((total,total))
    blocks=[]

    def add(kind, dim, param, matrix, offset):
        if kind == 0:
            return  # Free domain imposes no constraint.
        dim = int(dim); matrix=matrix.tocsc(); offset=np.asarray(offset,dtype=float)
        if kind == 2:
            matrix,offset,kind=-matrix,-offset,1
        if kind == 4 and dim == 1:
            kind=1
        if kind == 5:
            # (u+v,u-v,sqrt(2)*w) lies in an SOC iff 2uv >= ||w||^2, u,v>=0.
            transform=sp.diags(np.r_[1.,1.,np.full(dim-2,math.sqrt(2.))],format='lil')
            transform[0,1]=1.; transform[1,0]=1.; transform[1,1]=-1.
            matrix=transform.tocsc()@matrix; offset=transform@offset; kind=4
        if kind in (6,7):
            if kind==7 and target=='clarabel':
                raise ValueError('Clarabel adapter currently supports primal exponential cones only')
            matrix=matrix[[2,1,0],:]; offset=offset[[2,1,0]]
        if kind in (8,9):
            param=np.asarray(param,dtype=float)
            weight_sum=float(param.sum())
            if not math.isfinite(weight_sum) or weight_sum<=0:
                raise ValueError('power weight normalization overflow')
            param=param/weight_sum
            if np.any(param<=0):
                raise ValueError('power weights underflow after normalization')
            if target=='scs' and (len(param)!=2 or dim!=3):
                raise ValueError('SCS adapter needs a three-dimensional two-weight power cone')
            if target=='clarabel' and kind==9:
                raise ValueError('Clarabel adapter currently supports primal power cones only')
        blocks.append((kind,dim,param,matrix,offset))

    if model.info['profile']==1:
        def intervals(matrix, lower, upper):
            if np.any(np.isposinf(lower)) or np.any(np.isneginf(upper)):
                raise ValueError('infeasible infinite bounds cannot be omitted by a solver adapter')
            eq=np.isfinite(lower)&(lower==upper)
            if eq.any(): add(3,int(eq.sum()),[],matrix[eq],-lower[eq])
            lo=np.isfinite(lower)&~eq
            if lo.any(): add(1,int(lo.sum()),[],matrix[lo],-lower[lo])
            hi=np.isfinite(upper)&~eq
            if hi.any(): add(1,int(hi.sum()),[],-matrix[hi],upper[hi])
        intervals(G,get(5),get(6))
        intervals(sp.eye(n,format='csr'),get(7),get(8))
    else:
        # Trace products involving native PSD variables: off-diagonal entries count twice.
        rr,cc,vv=[],[],[]
        for row,block,i,j,value in zip(get(33),get(34),get(35),get(36),get(37)):
            rr.append(int(row)); cc.append(starts[int(block)]+_upper_index(int(i),int(j)))
            vv.append(float(value)*(1 if i==j else 2))
        if rr:
            G=G+sp.coo_matrix((vv,(rr,cc)),shape=(m,total)).tocsr()
        for block,i,j,value in zip(get(29),get(30),get(31),get(32)):
            c[starts[int(block)]+_upper_index(int(i),int(j))]=float(value)*(1 if i==j else 2)
        def domains(base,matrix,offset):
            start=0; params=get(base+3); ptr=get(base+2).astype('int64')
            for k,(kind,dim) in enumerate(zip(get(base),get(base+1))):
                dim=int(dim)
                add(int(kind),dim,params[ptr[k]:ptr[k+1]],matrix[start:start+dim],offset[start:start+dim])
                start+=dim
        domains(23,G,get(18))
        domains(19,sp.hstack([sp.eye(n),sp.csc_matrix((n,total-n))],format='csr'),np.zeros(n))
        for block,size in enumerate(psd_sizes):
            size=int(size); dimension=size*(size+1)//2; rr=[];cc=[];vv=[]
            for j in range(size):
                for i in range(j+1):
                    rr.append(_svec_index(i,j,size,target));cc.append(starts[block]+_upper_index(i,j))
                    vv.append(1. if i==j else math.sqrt(2.))
            add(10,size,[],sp.coo_matrix((vv,(rr,cc)),shape=(dimension,total)),np.zeros(dimension))
        for block,size in enumerate(get(28)):
            size=int(size); dimension=size*(size+1)//2; rr=[];cc=[];vv=[];offset=np.zeros(dimension)
            for b,var,i,j,value in zip(get(38),get(39),get(40),get(41),get(42)):
                if int(b)!=block: continue
                i,j=int(i),int(j);rr.append(_svec_index(i,j,size,target));cc.append(int(var))
                vv.append(float(value)*(1 if i==j else math.sqrt(2.)))
            for b,i,j,value in zip(get(43),get(44),get(45),get(46)):
                if int(b)!=block: continue
                i,j=int(i),int(j);offset[_svec_index(i,j,size,target)]=float(value)*(1 if i==j else math.sqrt(2.))
            add(10,size,[],sp.coo_matrix((vv,(rr,cc)),shape=(dimension,total)),offset)
    if target=='scs':
        order={3:0,1:1,4:2,10:3,6:4,7:5,8:6,9:6}
        blocks.sort(key=lambda b:order[b[0]])
    if blocks:
        A=-sp.vstack([b[3] for b in blocks],format='csc')
        rhs=np.concatenate([b[4] for b in blocks])
    else:
        # Some solvers require a nonempty row dimension: one redundant equality.
        A=sp.csc_matrix((1,total));rhs=np.zeros(1);blocks=[(3,1,[],A,rhs)]
    sense=model.info['sense']; P=sense*P; c=sense*c
    if not np.all(np.isfinite(A.data)) or not np.all(np.isfinite(rhs)) or not np.all(np.isfinite(c)) or not np.all(np.isfinite(P.data)):
        raise ValueError('solver transformation overflow')
    _psd_check(P)
    metadata=[(b[0],b[1],b[2]) for b in blocks]
    return P,c,A,rhs,metadata


def solve(model, target='scs', **settings):
    if target=='gurobi':
        from .quadratic import solve_gurobi
        return solve_gurobi(model,**settings)
    if target=='scip':
        from .quadratic import solve_scip
        return solve_scip(model,**settings)
    import numpy as np
    from scipy import sparse as sp
    P,c,A,b,blocks=problem(model,target)
    if target=='scs':
        import scs
        cone={'z':0,'l':0,'q':[],'s':[],'ep':0,'ed':0,'p':[]}
        for kind,dim,param in blocks:
            if kind==3: cone['z']+=dim
            elif kind==1: cone['l']+=dim
            elif kind==4: cone['q'].append(dim)
            elif kind==10: cone['s'].append(dim)
            elif kind==6: cone['ep']+=1
            elif kind==7: cone['ed']+=1
            elif kind in (8,9): cone['p'].append(float(param[0])*(1 if kind==8 else -1))
        data={'A':A,'b':b,'c':c}
        if P.nnz: data['P']=sp.triu(P,format='csc')
        result=scs.solve(data,cone,verbose=False,**settings)
        x=result['x']; status=result['info']['status']; details=result['info']
        dual=result['y'];slack=result['s']
    else:
        import clarabel
        cones=[]
        for kind,dim,param in blocks:
            if kind==3: cone=clarabel.ZeroConeT(dim)
            elif kind==1: cone=clarabel.NonnegativeConeT(dim)
            elif kind==4: cone=clarabel.SecondOrderConeT(dim)
            elif kind==10: cone=clarabel.PSDTriangleConeT(dim)
            elif kind==6: cone=clarabel.ExponentialConeT()
            elif kind==8:
                cone=(clarabel.PowerConeT(float(param[0])) if len(param)==2 and dim==3
                      else clarabel.GenPowerConeT(param.tolist(),dim-len(param)))
            else: raise ValueError('unsupported Clarabel cone')
            cones.append(cone)
        options=clarabel.DefaultSettings(); options.verbose=False
        for name,value in settings.items(): setattr(options,name,value)
        result=clarabel.DefaultSolver(sp.triu(P,format='csc'),c,A,b,cones,options).solve()
        x=np.asarray(result.x); status=str(result.status)
        dual=np.asarray(result.z);slack=np.asarray(result.s)
        details={'primal_residual':result.r_prim,'dual_residual':result.r_dual,'iterations':result.iterations}
    has_primal=status.lower() in ('solved','almostsolved') or status.lower().startswith('solved (inaccurate')
    objective=(float(model.info['objective_offset']+model.info['sense']*(.5*x@(P@x)+c@x)) if has_primal else None)
    certificate=None
    if 'infeasible' in status.lower() or 'unbounded' in status.lower():
        certificate={'x':x,'y':dual,'s':slack}
        # Necessary linear conditions only; cone membership is not a proof here.
        if 'primalinfeasible' in status.lower() or status.lower().startswith('infeasible'):
            details=dict(details);details['certificate_b_dot_y']=float(b@dual)
            details['certificate_ATy_inf']=float(np.max(np.abs(A.T@dual),initial=0.))
    return {'status':status,'objective':objective,'x':x if has_primal else None,
            'candidate_x':None if has_primal else x,'details':details,'certificate':certificate}
