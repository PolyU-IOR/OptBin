#define _POSIX_C_SOURCE 200809L
#define _FILE_OFFSET_BITS 64
#include "optbin.h"
#include "internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <math.h>
#include <limits.h>
#include <sys/stat.h>
#include <zlib.h>

static int err(char *e,size_t n,const char *fmt,...) {
    if(e&&n) { va_list a; va_start(a,fmt); vsnprintf(e,n,fmt,a); va_end(a); }
    return -1;
}
unsigned optb_type_bytes(unsigned t) {
    return t==OPTB_U32?4:t==OPTB_U64||t==OPTB_F64?8:t==OPTB_U8?1:0;
}
const char *optb_section_name(unsigned i) {
    static const char *names[]={"unused","A_ptr","A_index","A_value","c",
    "row_lower","row_upper","x_lower","x_upper","Q_ptr","Q_index","Q_value",
    "qc_row","qc_ptr","qc_i","qc_j","qc_value","qc_scale","b",
    "var_kind","var_dim","var_param_ptr","var_param",
    "con_kind","con_dim","con_param_ptr","con_param","psd_var_dim","psd_con_dim",
    "obj_psd_block","obj_psd_i","obj_psd_j","obj_psd_value",
    "F_row","F_block","F_i","F_j","F_value",
    "H_block","H_var","H_i","H_j","H_value",
    "D_block","D_i","D_j","D_value","integer_ids","source"};
    return i<sizeof names/sizeof *names?names[i]:"unknown";
}
const optb_section *optb_get(const optb_model *m,unsigned id) {
    if(!m) return NULL;
    uint64_t lo=0,hi=m->info.sections;
    while(lo<hi) {
        uint64_t mid=lo+(hi-lo)/2;
        if(m->section[mid].id<id) lo=mid+1;
        else if(m->section[mid].id>id) hi=mid;
        else return m->section+mid;
    }
    return NULL;
}
void optb_free(optb_model *m) {
    if(!m) return;
    if(m->section) for(uint64_t k=0;k<m->info.sections;k++) free(m->section[k].data);
    free(m->section); memset(m,0,sizeof *m);
}
int optb_read_info(const char *path,optb_info *out,char *e,size_t ne) {
    FILE *f=fopen(path,"rb"); optb_model m={0};
    if(!f) return err(e,ne,"cannot open input");
    unsigned char magic[8]; int legacy=fread(magic,1,8,f)==8&&!memcmp(magic,"BQP1\r\n\032\n",8);
    rewind(f);
    if(legacy) {
        bqp_info b; fclose(f);
        if(bqp_read_info(path,&b,e,ne)) return -1;
        memset(out,0,sizeof *out); out->sections=11; out->blocks=b.blocks;
        out->file_bytes=b.file_bytes; out->cols=b.cols; out->rows=b.rows;
        out->block_elements=b.block_elements; out->profile=OPTB_QUADRATIC;
        out->sense=b.sense; out->objective_offset=b.objective_offset; return 0;
    }
    int rc=optb_manifest_read(f,&m,e,ne); fclose(f);
    if(!rc) *out=m.info;
    optb_free(&m); return rc;
}
static int stdio_bytes(void *ctx,const optb_section *s,uint64_t offset,void *out,size_t n,char *e,size_t ne) {
    (void)s;(void)offset;
    return fread(out,1,n,(FILE*)ctx)==n?0:err(e,ne,"truncated section/read error");
}
int optb_visit(const char *path,optb_info *info,optb_block_fn cb,void *ctx,char *e,size_t ne) {
    FILE *f=fopen(path,"rb");optb_model m={0};int rc=-1;
    if(!f)return err(e,ne,"cannot open input");
    if(optb_manifest_read(f,&m,e,ne)||optb_walk_encoded(&m,stdio_bytes,f,cb,ctx,e,ne))goto done;
    if(fgetc(f)!=EOF||ferror(f)){err(e,ne,"trailing data/read error");goto done;}
    if(info)*info=m.info;
    rc=0;
done:optb_free(&m);fclose(f);return rc;
}
static int copy(void *ctx,const optb_info *h,const optb_section *s,uint64_t start,
                uint64_t n,const void *p,char *e,size_t ne) {
    optb_model *m=ctx; const optb_section *d=optb_get(m,s->id);
    if(memcmp(h,&m->info,sizeof *h)||!d||d->type!=s->type||d->count!=s->count||
       d->offset!=s->offset||d->stored_bytes!=s->stored_bytes||d->blocks!=s->blocks||d->flags!=s->flags)
        return err(e,ne,"file changed during load");
    memcpy((unsigned char *)d->data+start*optb_type_bytes(d->type),p,(size_t)n*optb_type_bytes(d->type));
    return 0;
}
int optb_load(const char *path,optb_model *m,uint64_t limit,char *e,size_t ne) {
    if(!m) return err(e,ne,"null output model");
    memset(m,0,sizeof *m); FILE *f=fopen(path,"rb");
    if(!f) return err(e,ne,"cannot open input");
    unsigned char magic[8]; int legacy=fread(magic,1,8,f)==8&&!memcmp(magic,"BQP1\r\n\032\n",8);
    rewind(f);
    if(legacy) {
        bqp_model b; fclose(f);
        if(bqp_load(path,&b,limit,e,ne)) return -1;
        m->section=calloc(11,sizeof *m->section);
        if(!m->section) { bqp_free(&b); return err(e,ne,"directory allocation failed"); }
        m->info=(optb_info){11,b.info.blocks,b.info.file_bytes,b.info.cols,b.info.rows,b.info.block_elements,
                           OPTB_QUADRATIC,b.info.sense,b.info.objective_offset};
        for(unsigned a=1;a<=11;a++) {
            optb_section *s=m->section+a-1; s->id=a; s->count=bqp_array_count(&b.info,a); s->flags=1;
            s->type=a==1||a==9?OPTB_U64:a==2||a==10?(bqp_element_bytes(&b.info,a)==4?OPTB_U32:OPTB_U64):OPTB_F64;
            s->data=b.array[a]; b.array[a]=NULL;
        }
        bqp_free(&b);
    } else {
        if(optb_manifest_read(f,m,e,ne)||optb_allocate(m,limit,e,ne)||
           optb_walk_encoded(m,stdio_bytes,f,copy,m,e,ne)) {
            fclose(f);optb_free(m);return -1;
        }
        if(fgetc(f)!=EOF||ferror(f)) {
            fclose(f);optb_free(m);return err(e,ne,"trailing data/read error");
        }
        fclose(f);
    }
    if(optb_validate(m,e,ne)) { optb_free(m); return -1; }
    return 0;
}
