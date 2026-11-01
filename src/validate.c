#include "optbin.h"
#include <math.h>
#include <stdio.h>
#include <stdarg.h>
#include <limits.h>

static int bad(char *e,size_t n,const char *f,...) {
    if(e&&n) { va_list a; va_start(a,f); vsnprintf(e,n,f,a); va_end(a); } return -1;
}
static uint64_t count(const optb_model *m,unsigned id) {
    const optb_section *s=optb_get(m,id); return s?s->count:0;
}
static uint64_t u(const optb_model *m,unsigned id,uint64_t k) {
    const optb_section *s=optb_get(m,id);
    return s->type==OPTB_U32?((const uint32_t*)s->data)[k]:((const uint64_t*)s->data)[k];
}
static double d(const optb_model *m,unsigned id,uint64_t k) {
    return ((const double*)optb_get(m,id)->data)[k];
}
/* type=0 accepts either unsigned sparse-index width. */
static int shape(const optb_model *m,unsigned id,unsigned type,uint64_t n,int required,char *e,size_t ne) {
    const optb_section *s=optb_get(m,id);
    if(!s) return required?bad(e,ne,"missing %s",optb_section_name(id)):0;
    if((type?s->type!=type:s->type!=OPTB_U32&&s->type!=OPTB_U64)||s->count!=n)
        return bad(e,ne,"invalid type/count for %s",optb_section_name(id));
    return 0;
}
static int csc(const optb_model *m,unsigned p,unsigned i,unsigned x,int quadratic,char *e,size_t ne) {
    uint64_t nz=count(m,x),n=m->info.cols,rows=quadratic?n:m->info.rows;
    int required=!quadratic||optb_get(m,p)||optb_get(m,i)||optb_get(m,x);
    if(!required) return 0;
    /* A always has n+1 pointers. An empty Q is represented by no pointers. */
    if(shape(m,p,OPTB_U64,quadratic&&!nz?0:n+1,1,e,ne)||shape(m,i,0,nz,1,e,ne)||
       shape(m,x,OPTB_F64,nz,1,e,ne)) return -1;
    if(quadratic&&!nz) return 0;
    const uint64_t *ptr=optb_get(m,p)->data;
    const optb_section *indices=optb_get(m,i);
    const uint32_t *i32=indices->data; const uint64_t *i64=indices->data;
    if(ptr[0]!=0||ptr[n]!=nz) return bad(e,ne,"invalid CSC endpoints");
    for(uint64_t col=0;col<n;col++) {
        uint64_t b=ptr[col],end=ptr[col+1],prev=0;
        if(b>end||end>nz) return bad(e,ne,"invalid CSC pointer order");
        for(uint64_t k=b;k<end;k++) {
            uint64_t row=indices->type==OPTB_U32?i32[k]:i64[k];
            if(row>=rows||(k>b&&row<=prev)||(quadratic&&row>col))
                return bad(e,ne,"invalid CSC index/order/triangle");
            prev=row;
        }
    }
    return 0;
}
static int cones(const optb_model *m,unsigned base,uint64_t dimension,char *e,size_t ne) {
    uint64_t k=count(m,base),p=count(m,base+3);
    if(shape(m,base,OPTB_U32,k,1,e,ne)||shape(m,base+1,OPTB_U64,k,1,e,ne)||
       shape(m,base+2,OPTB_U64,k+1,1,e,ne)||shape(m,base+3,OPTB_F64,p,1,e,ne)) return -1;
    if(u(m,base+2,0)!=0||u(m,base+2,k)!=p) return bad(e,ne,"invalid cone parameter endpoints");
    uint64_t total=0;
    for(uint64_t j=0;j<k;j++) {
        uint64_t kind=u(m,base,j),dim=u(m,base+1,j),a=u(m,base+2,j),b=u(m,base+2,j+1);
        if(kind>OPTB_DUAL_POWER||!dim||total>dimension||dim>dimension-total||a>b||b>p)
            return bad(e,ne,"invalid cone descriptor/coverage");
        if((kind==OPTB_RSOC&&dim<2)||((kind==OPTB_EXP||kind==OPTB_DUAL_EXP)&&dim!=3))
            return bad(e,ne,"invalid cone dimension");
        if(kind==OPTB_POWER||kind==OPTB_DUAL_POWER) {
            if(a==b||b-a>dim) return bad(e,ne,"invalid power parameter count");
            for(uint64_t t=a;t<b;t++) if(!(d(m,base+3,t)>0)) return bad(e,ne,"power weights must be positive");
        } else if(a!=b) return bad(e,ne,"unexpected cone parameters");
        total+=dim;
    }
    return total==dimension?0:bad(e,ne,"cone dimensions do not cover domain");
}
/* Prefix keys, then (column,row). Requiring canonical order rejects duplicates
 * including mirrored entries without an auxiliary hash table. */
static int psd_coords(const optb_model *m,unsigned first,unsigned value,unsigned dim,
                      unsigned index,char *e,size_t ne) {
    unsigned fields=value-first; uint64_t n=count(m,value);
    int present=0;
    for(unsigned id=first;id<=value;id++) present|=optb_get(m,id)!=NULL;
    if(!present) return 0;
    for(unsigned id=first;id<value;id++) if(shape(m,id,OPTB_U64,n,1,e,ne)) return -1;
    if(shape(m,value,OPTB_F64,n,1,e,ne)) return -1;
    for(uint64_t k=0;k<n;k++) {
        uint64_t block=u(m,first+index,k);
        if(block>=count(m,dim)) return bad(e,ne,"PSD block out of bounds");
        uint64_t row=u(m,value-2,k),col=u(m,value-1,k),size=u(m,dim,block);
        if(row>col||col>=size) return bad(e,ne,"PSD index/triangle out of bounds");
        if(first==OPTB_F_ROW&&u(m,OPTB_F_ROW,k)>=m->info.rows) return bad(e,ne,"F constraint out of bounds");
        if(first==OPTB_H_BLOCK&&u(m,OPTB_H_VAR,k)>=m->info.cols) return bad(e,ne,"H variable out of bounds");
        if(k) {
            int cmp=0;
            for(unsigned t=0;t<fields;t++) {
                unsigned id=t==fields-2?value-1:t==fields-1?value-2:first+t;
                uint64_t a=u(m,id,k-1),b=u(m,id,k);
                if(a!=b) { cmp=a<b?-1:1; break; }
            }
            if(cmp>=0) return bad(e,ne,"PSD coordinates unsorted/duplicated");
        }
    }
    return 0;
}
int optb_validate(const optb_model *m,char *e,size_t ne) {
    if(!m||(!m->section&&m->info.sections)||m->info.sections>4096||
       m->info.rows>=INT64_MAX||m->info.cols>=INT64_MAX||
       (m->info.profile!=OPTB_QUADRATIC&&m->info.profile!=OPTB_CONIC)||
       (m->info.sense!=1&&m->info.sense!=-1)||!isfinite(m->info.objective_offset))
        return bad(e,ne,"invalid model metadata");
    for(uint64_t j=0;j<m->info.sections;j++) {
        const optb_section *s=m->section+j; unsigned w=optb_type_bytes(s->type);
        if(!s->id||!w||s->count>SIZE_MAX/w||(s->count&&!s->data)||s->flags>1||
           (j&&s->id<=m->section[j-1].id)) return bad(e,ne,"invalid section metadata/data");
        if(s->id==OPTB_INTEGER_IDS) return bad(e,ne,"integer models are not supported by this release");
        if(s->id>OPTB_SOURCE&&s->flags) return bad(e,ne,"unknown required section");
        if(s->id==OPTB_SOURCE&&s->type!=OPTB_U8) return bad(e,ne,"source must be UTF-8 bytes");
        if((m->info.profile==OPTB_QUADRATIC&&s->id>=OPTB_B&&s->id<OPTB_SOURCE)||
           (m->info.profile==OPTB_CONIC&&((s->id>=OPTB_RL&&s->id<=OPTB_XU)||
                                         (s->id>=OPTB_QC_ROW&&s->id<=OPTB_QC_SCALE))))
            return bad(e,ne,"section incompatible with profile");
        if(s->type==OPTB_F64) for(uint64_t k=0;k<s->count;k++) {
            double x=((const double*)s->data)[k];
            int bound=s->id>=OPTB_RL&&s->id<=OPTB_XU;
            if(isnan(x)||(!bound&&!isfinite(x))) return bad(e,ne,"nonfinite value in %s",optb_section_name(s->id));
        }
    }
    if(shape(m,OPTB_C,OPTB_F64,m->info.cols,1,e,ne)||
       csc(m,OPTB_AP,OPTB_AI,OPTB_AX,0,e,ne)||csc(m,OPTB_QP,OPTB_QI,OPTB_QX,1,e,ne)) return -1;
    if(m->info.profile==OPTB_QUADRATIC) {
        for(unsigned id=OPTB_RL;id<=OPTB_XU;id++)
            if(shape(m,id,OPTB_F64,id<=OPTB_RU?m->info.rows:m->info.cols,1,e,ne)) return -1;
        uint64_t q=count(m,OPTB_QC_ROW),nz=count(m,OPTB_QC_X); int present=0;
        for(unsigned id=OPTB_QC_ROW;id<=OPTB_QC_SCALE;id++) present|=optb_get(m,id)!=NULL;
        if(present) {
            if(shape(m,OPTB_QC_ROW,OPTB_U64,q,1,e,ne)||shape(m,OPTB_QC_PTR,OPTB_U64,q+1,1,e,ne)||
               shape(m,OPTB_QC_I,0,nz,1,e,ne)||shape(m,OPTB_QC_J,0,nz,1,e,ne)||
               shape(m,OPTB_QC_X,OPTB_F64,nz,1,e,ne)||shape(m,OPTB_QC_SCALE,OPTB_F64,q,1,e,ne)) return -1;
            if(u(m,OPTB_QC_PTR,0)||u(m,OPTB_QC_PTR,q)!=nz) return bad(e,ne,"invalid QC endpoints");
            for(uint64_t j=0;j<q;j++) {
                uint64_t row=u(m,OPTB_QC_ROW,j),a=u(m,OPTB_QC_PTR,j),b=u(m,OPTB_QC_PTR,j+1);
                if(row>=m->info.rows||(j&&row<=u(m,OPTB_QC_ROW,j-1))||a>b||b>nz||
                   (d(m,OPTB_QC_SCALE,j)!=.5&&d(m,OPTB_QC_SCALE,j)!=1.)) return bad(e,ne,"invalid QC descriptor");
                for(uint64_t k=a;k<b;k++) {
                    uint64_t i=u(m,OPTB_QC_I,k),col=u(m,OPTB_QC_J,k);
                    if(i>col||col>=m->info.cols||(k>a&&(col<u(m,OPTB_QC_J,k-1)||
                       (col==u(m,OPTB_QC_J,k-1)&&i<=u(m,OPTB_QC_I,k-1)))))
                        return bad(e,ne,"invalid QC index/order");
                }
            }
        }
    } else {
        if(shape(m,OPTB_B,OPTB_F64,m->info.rows,1,e,ne)||
           cones(m,OPTB_VAR_KIND,m->info.cols,e,ne)||cones(m,OPTB_CON_KIND,m->info.rows,e,ne)) return -1;
        for(unsigned id=OPTB_PSD_VAR_DIM;id<=OPTB_PSD_CON_DIM;id++) {
            if(shape(m,id,OPTB_U64,count(m,id),0,e,ne)) return -1;
            for(uint64_t k=0;k<count(m,id);k++) {
                uint64_t size=u(m,id,k);
                if(!size||size>UINT32_MAX) return bad(e,ne,"invalid PSD dimension");
            }
        }
        if(psd_coords(m,OPTB_OF_BLOCK,OPTB_OF_X,OPTB_PSD_VAR_DIM,0,e,ne)||
           psd_coords(m,OPTB_F_ROW,OPTB_F_X,OPTB_PSD_VAR_DIM,1,e,ne)||
           psd_coords(m,OPTB_H_BLOCK,OPTB_H_X,OPTB_PSD_CON_DIM,0,e,ne)||
           psd_coords(m,OPTB_D_BLOCK,OPTB_D_X,OPTB_PSD_CON_DIM,0,e,ne)) return -1;
    }
    return 0;
}
