#define _POSIX_C_SOURCE 200809L
#include "optbin.h"
#include <dlfcn.h>
#include <limits.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <time.h>

/* Optional reference tool. Public C API declarations only; no SDK header or
 * Gurobi implementation is bundled. The core reader never loads this library. */
typedef struct GRBenv GRBenv;
typedef struct GRBmodel GRBmodel;
typedef struct {
    int (*GRBemptyenv)(GRBenv **);
    int (*GRBsetintparam)(GRBenv *,const char *,int);
    int (*GRBstartenv)(GRBenv *);
    int (*GRBreadmodel)(GRBenv *,const char *,GRBmodel **);
    int (*GRBgetintattr)(GRBmodel *,const char *,int *);
    int (*GRBgetdblattr)(GRBmodel *,const char *,double *);
    int (*GRBgetdblattrarray)(GRBmodel *,const char *,int,int,double *);
    int (*GRBgetcharattrarray)(GRBmodel *,const char *,int,int,char *);
    int (*GRBXgetvars)(GRBmodel *,size_t *,size_t *,int *,double *,int,int);
    const char *(*GRBgeterrormsg)(GRBenv *);
    void (*GRBversion)(int *,int *,int *);
    void (*GRBfreemodel)(GRBmodel *);
    void (*GRBfreeenv)(GRBenv *);
} api;

static double now(void) {
    struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t);
    return t.tv_sec+1e-9*t.tv_nsec;
}
static double cpu_time(struct timeval t) { return t.tv_sec+1e-6*t.tv_usec; }
static int equal_real(double a,double b) {
    /* Exact numerical equality, without a tolerance. Gurobi can canonicalize
     * the sign of zero; file-to-file lossless checks remain bitwise elsewhere. */
    return a==b;
}
static double bound(double value) {
    /* Gurobi's public GRB_INFINITY sentinel, normalized only in bound fields. */
    return value==1e100?INFINITY:value==-1e100?-INFINITY:value;
}
static uint64_t index_at(const optb_section *s,uint64_t k) {
    return s->type==OPTB_U32?((const uint32_t *)s->data)[k]:((const uint64_t *)s->data)[k];
}
typedef struct { int row; double value; } entry;
static int by_row(const void *a,const void *b) {
    int x=((const entry *)a)->row,y=((const entry *)b)->row;
    return (x>y)-(x<y);
}

/* Compare all LP coefficients/bounds and metadata through public query APIs.
 * Batches avoid Python objects or a second complete CSC allocation. GRBX uses
 * size_t nonzero counts, including for >2^31-entry source models. */
static int compare_lp(api *g,GRBenv *env,GRBmodel *source,const optb_model *m,
                      int cols,int rows,uint64_t *compared,uint64_t *zeros) {
    enum { BATCH=65536 };
    double *values=NULL,*coeff=NULL; char *senses=NULL;
    size_t *pointers=NULL;int *indices=NULL;entry *sorted=NULL;
    size_t capacity=0,sort_capacity=0;int rc=-1,sense=0;
    double offset=0,nnz=0;
    const char *unsupported[]={"IsMIP","NumIntVars","NumQNZs","NumQConstrs","NumGenConstrs","NumSOS","NumPWLObjVars"};
#define QUERY(call) do { if((call)) { fprintf(stderr,"Gurobi query failed: %s\n",g->GRBgeterrormsg(env)); goto done; } } while(0)
#define REQUIRE(ok,field,pos) do { if(!(ok)) { fprintf(stderr,"reference mismatch: %s at %llu\n",field,(unsigned long long)(pos)); goto done; } } while(0)
    REQUIRE(m->info.profile==OPTB_QUADRATIC,"profile",0);
    for(size_t i=0;i<sizeof unsupported/sizeof *unsupported;i++) {
        int count=0;QUERY(g->GRBgetintattr(source,unsupported[i],&count));
        if(count) { fprintf(stderr,"C reference verification is LP-only; unsupported %s\n",unsupported[i]);goto done; }
    }
    REQUIRE(m->info.cols==(uint64_t)cols&&m->info.rows==(uint64_t)rows,"dimensions",0);
    if((optb_get(m,OPTB_QX)&&optb_get(m,OPTB_QX)->count)||
       (optb_get(m,OPTB_QC_ROW)&&optb_get(m,OPTB_QC_ROW)->count)) {
        fprintf(stderr,"C reference verification is LP-only; reference contains quadratic data\n");goto done;
    }
    QUERY(g->GRBgetintattr(source,"ModelSense",&sense));QUERY(g->GRBgetdblattr(source,"ObjCon",&offset));
    REQUIRE(sense==m->info.sense,"sense",0);
    REQUIRE(equal_real(offset,m->info.objective_offset),"objective offset",0);
    QUERY(g->GRBgetdblattr(source,"DNumNZs",&nnz));
    REQUIRE(nnz>=0&&nnz<9007199254740992.&&floor(nnz)==nnz,"nonzero count",0);
    values=malloc(BATCH*sizeof *values);senses=malloc(BATCH);
    pointers=malloc((BATCH+1)*sizeof *pointers);
    if(!values||!senses||!pointers) { fprintf(stderr,"reference scratch allocation failed\n");goto done; }
    const char *attributes[]={"Obj","LB","UB"};const unsigned ids[]={OPTB_C,OPTB_XL,OPTB_XU};
    for(unsigned attribute=0;attribute<3;attribute++) {
        const double *expected=optb_get(m,ids[attribute])->data;
        for(int start=0;start<cols;) {
            int count=cols-start<BATCH?cols-start:BATCH;
            QUERY(g->GRBgetdblattrarray(source,attributes[attribute],start,count,values));
            for(int k=0;k<count;k++) {
                double actual=attribute?bound(values[k]):values[k];
                REQUIRE(equal_real(actual,expected[(uint64_t)start+k]),attributes[attribute],(uint64_t)start+k);
            }
            *compared+=(uint64_t)count;start+=count;
        }
    }
    const double *lower=optb_get(m,OPTB_RL)->data,*upper=optb_get(m,OPTB_RU)->data;
    for(int start=0;start<rows;) {
        int count=rows-start<BATCH?rows-start:BATCH;
        QUERY(g->GRBgetdblattrarray(source,"RHS",start,count,values));
        QUERY(g->GRBgetcharattrarray(source,"Sense",start,count,senses));
        for(int k=0;k<count;k++) {
            double lo=-INFINITY,hi=INFINITY;
            REQUIRE(senses[k]=='<'||senses[k]=='>'||senses[k]=='=',"row sense",(uint64_t)start+k);
            if(senses[k]=='>'||senses[k]=='=')lo=values[k];
            if(senses[k]=='<'||senses[k]=='=')hi=values[k];
            REQUIRE(equal_real(lo,lower[(uint64_t)start+k]),"row lower",(uint64_t)start+k);
            REQUIRE(equal_real(hi,upper[(uint64_t)start+k]),"row upper",(uint64_t)start+k);
        }
        *compared+=2*(uint64_t)count;start+=count;
    }
    const uint64_t *ap=optb_get(m,OPTB_AP)->data;
    const optb_section *ai=optb_get(m,OPTB_AI);
    const double *ax=optb_get(m,OPTB_AX)->data;
    uint64_t returned=0;
    for(int start=0;start<cols;) {
        int count=cols-start<BATCH?cols-start:BATCH;size_t count_nz=0;
        QUERY(g->GRBXgetvars(source,&count_nz,NULL,NULL,NULL,start,count));
        if(count_nz>capacity) {
            REQUIRE(count_nz<=SIZE_MAX/sizeof(double)&&count_nz<=SIZE_MAX/sizeof(int),"scratch size",start);
            free(indices);free(coeff);indices=malloc(count_nz*sizeof *indices);coeff=malloc(count_nz*sizeof *coeff);
            if(!indices||!coeff) { fprintf(stderr,"matrix scratch allocation failed\n");goto done; }
            capacity=count_nz;
        }
        size_t actual_nz=count_nz;
        QUERY(g->GRBXgetvars(source,&actual_nz,pointers,indices,coeff,start,count));
        REQUIRE(actual_nz==count_nz,"query nonzero count",start);pointers[count]=count_nz;
        REQUIRE(!pointers[0],"CSC first pointer",start);
        for(int col=0;col<count;col++) {
            size_t a=pointers[col],b=pointers[col+1];
            REQUIRE(a<=b&&b<=count_nz,"CSC pointers",(uint64_t)start+col);
            int ordered=1;
            for(size_t k=a+1;k<b;k++)if(indices[k]<indices[k-1])ordered=0;
            if(!ordered) {
                size_t length=b-a;
                if(length>sort_capacity) {
                    REQUIRE(length<=SIZE_MAX/sizeof *sorted,"sort scratch size",start+col);
                    free(sorted);sorted=malloc(length*sizeof *sorted);sort_capacity=length;
                    if(!sorted) { fprintf(stderr,"sort scratch allocation failed\n");goto done; }
                }
                for(size_t k=a;k<b;k++)sorted[k-a]=(entry){indices[k],coeff[k]};
                qsort(sorted,b-a,sizeof *sorted,by_row);
                for(size_t k=a;k<b;k++){indices[k]=sorted[k-a].row;coeff[k]=sorted[k-a].value;}
            }
            uint64_t k=ap[(uint64_t)start+col],end=ap[(uint64_t)start+col+1];
            for(size_t j=a;j<b;j++) {
                if(coeff[j]==0)continue;
                while(k<end&&ax[k]==0){++k;++*zeros;}
                REQUIRE(k<end,"A column length",(uint64_t)start+col);
                REQUIRE(indices[j]>=0&&index_at(ai,k)==(uint64_t)indices[j],"A row index",k);
                REQUIRE(equal_real(ax[k],coeff[j]),"A coefficient",k);
                ++k;*compared+=2;
            }
            while(k<end&&ax[k]==0){++k;++*zeros;}
            REQUIRE(k==end,"A column length",(uint64_t)start+col);++*compared;
        }
        returned+=count_nz;start+=count;
    }
    REQUIRE(returned==(uint64_t)nnz,"complete A coverage",returned);
    rc=0;
done:
    free(values);free(senses);free(pointers);free(indices);free(coeff);free(sorted);return rc;
#undef QUERY
#undef REQUIRE
}

int main(int argc,char **argv) {
    if(argc!=3&&argc!=4) {
        fprintf(stderr,"usage: optb_gurobi_bench LIBGUROBI INPUT.mps[.gz] [REFERENCE.optb]\n");return 2;
    }
    void *library=dlopen(argv[1],RTLD_NOW|RTLD_LOCAL);api g={0};GRBenv *env=NULL;GRBmodel *model=NULL;
    optb_model reference={0};int rc=1;char error[1024];
    if(!library){fprintf(stderr,"Cannot load Gurobi library: %s\n",dlerror());return 1;}
#define SYMBOL(name) do { void *p=dlsym(library,#name); if(!p) { fprintf(stderr,"Missing Gurobi symbol: %s\n",#name);goto done; } memcpy(&g.name,&p,sizeof p); } while(0)
    SYMBOL(GRBemptyenv);SYMBOL(GRBsetintparam);SYMBOL(GRBstartenv);SYMBOL(GRBreadmodel);
    SYMBOL(GRBgetintattr);SYMBOL(GRBgetdblattr);SYMBOL(GRBgetdblattrarray);SYMBOL(GRBgetcharattrarray);
    SYMBOL(GRBXgetvars);SYMBOL(GRBgeterrormsg);SYMBOL(GRBversion);SYMBOL(GRBfreemodel);SYMBOL(GRBfreeenv);
#undef SYMBOL
#define CALL(call) do { int code=(call); if(code) { fprintf(stderr,"Gurobi error %d: %s\n",code,env?g.GRBgeterrormsg(env):"environment creation failed"); goto done; } } while(0)
    double started=now();
    CALL(g.GRBemptyenv(&env));CALL(g.GRBsetintparam(env,"OutputFlag",0));
    CALL(g.GRBsetintparam(env,"Threads",1));CALL(g.GRBstartenv(env));
    double environment_seconds=now()-started;
    struct rusage before,after;getrusage(RUSAGE_SELF,&before);
    started=now();CALL(g.GRBreadmodel(env,argv[2],&model));double seconds=now()-started;
    getrusage(RUSAGE_SELF,&after);
    int major=0,minor=0,technical=0,cols=0,rows=0,q_nnz=0,q_constraints=0;double nz=0;
    g.GRBversion(&major,&minor,&technical);
    CALL(g.GRBgetintattr(model,"NumVars",&cols));CALL(g.GRBgetintattr(model,"NumConstrs",&rows));
    CALL(g.GRBgetdblattr(model,"DNumNZs",&nz));
    CALL(g.GRBgetintattr(model,"NumQNZs",&q_nnz));CALL(g.GRBgetintattr(model,"NumQConstrs",&q_constraints));
    uint64_t compared=0,zeros=0;double verify_seconds=0;
    if(argc==4) {
        started=now();
        if(optb_load(argv[3],&reference,0,error,sizeof error)) { fprintf(stderr,"OptBin reference load: %s\n",error);goto done; }
        if(compare_lp(&g,env,model,&reference,cols,rows,&compared,&zeros))goto done;
        verify_seconds=now()-started;
    }
    printf("{\"reader\":\"Gurobi GRBreadmodel\",\"gurobi_version\":\"%d.%d.%d\","
        "\"seconds\":%.9g,\"environment_seconds\":%.9g,\"max_rss_bytes\":%llu,"
        "\"user_seconds\":%.9g,\"system_seconds\":%.9g,\"rows\":%d,\"cols\":%d,\"nnz\":%.0f,"
        "\"q_nnz\":%d,\"q_constraints\":%d,"
        "\"gurobi_threads_parameter\":1,\"exact_reference_equal\":%s,\"reference_compare_seconds\":%.9g,"
        "\"compared_elements\":%llu,\"ignored_explicit_sparse_zeros\":%llu,"
        "\"normalization\":\"Gurobi infinity sentinels, zero signs and explicit sparse zeros; no tolerance\"}\n",
        major,minor,technical,seconds,environment_seconds,(unsigned long long)after.ru_maxrss*1024,
        cpu_time(after.ru_utime)-cpu_time(before.ru_utime),cpu_time(after.ru_stime)-cpu_time(before.ru_stime),
        rows,cols,nz,q_nnz,q_constraints,argc==4?"true":"null",verify_seconds,(unsigned long long)compared,(unsigned long long)zeros);
    rc=0;
done:
    optb_free(&reference);if(model&&g.GRBfreemodel)g.GRBfreemodel(model);
    if(env&&g.GRBfreeenv)g.GRBfreeenv(env);
    dlclose(library);return rc;
#undef CALL
}
