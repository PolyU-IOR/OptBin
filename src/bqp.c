#define _POSIX_C_SOURCE 200809L
#define _FILE_OFFSET_BITS 64
#include "internal.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <limits.h>
#include <math.h>
#include <sys/stat.h>
#include <zlib.h>

static int fail(char *e,size_t n,const char *f,...) {
    if(e&&n) { va_list a; va_start(a,f); vsnprintf(e,n,f,a); va_end(a); }
    return -1;
}
const char *bqp_array_name(unsigned a) {
    static const char *names[]={"unused","A_ptr","A_index","A_value","c","row_lower",
        "row_upper","x_lower","x_upper","Q_ptr","Q_index","Q_value"};
    return a<=BQP_ARRAYS?names[a]:"invalid";
}
uint64_t bqp_array_count(const bqp_info *h,unsigned a) {
    switch(a) {
    case BQP_AP: return h->cols+1;
    case BQP_AI: case BQP_AX: return h->nnz;
    case BQP_C: case BQP_XL: case BQP_XU: return h->cols;
    case BQP_RL: case BQP_RU: return h->rows;
    case BQP_QP: return h->qnnz?h->cols+1:0;
    case BQP_QI: case BQP_QX: return h->qnnz;
    default: return 0;
    }
}
unsigned bqp_element_bytes(const bqp_info *h,unsigned a) {
    return a==BQP_AI?h->a_index_bytes:a==BQP_QI?h->q_index_bytes:8;
}
int bqp_memory_bytes(const bqp_info *h,uint64_t *bytes) {
    uint64_t total=0;
    for(unsigned a=1;a<=BQP_ARRAYS;a++) {
        uint64_t n=bqp_array_count(h,a), w=bqp_element_bytes(h,a);
        if(n>(UINT64_MAX-total)/w) return -1;
        total+=n*w;
    }
    *bytes=total; return 0;
}
static int header(FILE *f,bqp_info *h,char *e,size_t ne) {
    unsigned char b[BQP_HEADER]; struct stat st;
    if(!bqp_platform()) return fail(e,ne,"requires little-endian IEEE binary64/binary32 host");
    if(fread(b,1,sizeof b,f)!=sizeof b) return fail(e,ne,"truncated BQP header");
    if(memcmp(b,"BQP1\r\n\032\n",8)||bqp_u32(b+8)!=1||bqp_u32(b+12)!=128)
        return fail(e,ne,"unsupported BQP magic/version/header");
    if((uint32_t)crc32(0,b,124)!=bqp_u32(b+124)) return fail(e,ne,"header checksum mismatch");
    memset(h,0,sizeof *h);
    h->rows=bqp_u64(b+16); h->cols=bqp_u64(b+24);
    h->nnz=bqp_u64(b+32); h->qnnz=bqp_u64(b+40);
    h->blocks=bqp_u64(b+48); h->file_bytes=bqp_u64(b+56);
    h->sense=(int32_t)bqp_u32(b+64); h->a_index_bytes=bqp_u32(b+68);
    h->q_index_bytes=bqp_u32(b+72); memcpy(&h->objective_offset,b+80,8);
    h->block_elements=bqp_u64(b+88);
    if(h->cols==UINT64_MAX || h->rows>INT64_MAX || h->cols>INT64_MAX ||
       h->nnz>INT64_MAX || h->qnnz>INT64_MAX || (h->sense!=1&&h->sense!=-1)||
       (h->a_index_bytes!=4&&h->a_index_bytes!=8)||
       (h->q_index_bytes!=4&&h->q_index_bytes!=8)||bqp_u32(b+76)!=1||
       !isfinite(h->objective_offset)||!h->block_elements||h->block_elements>BQP_MAX_ELEMENTS)
        return fail(e,ne,"invalid model metadata");
    if((h->a_index_bytes==4&&h->rows>UINT32_MAX)||
       (h->q_index_bytes==4&&h->cols>UINT32_MAX)) return fail(e,ne,"index type cannot address dimensions");
    for(unsigned i=96;i<124;i++) if(b[i]) return fail(e,ne,"nonzero reserved header bytes");
    if(fstat(fileno(f),&st)||st.st_size<128||(uint64_t)st.st_size!=h->file_bytes||
       h->blocks>(h->file_bytes-128)/64) return fail(e,ne,"file size/block count mismatch");
    uint64_t bytes;
    if(bqp_memory_bytes(h,&bytes)||bytes>SIZE_MAX) return fail(e,ne,"model too large for address space");
    return 0;
}
int bqp_read_info(const char *path,bqp_info *h,char *e,size_t ne) {
    FILE *f=fopen(path,"rb"); if(!f) return fail(e,ne,"cannot open %s",path);
    int rc=header(f,h,e,ne); fclose(f); return rc;
}
int bqp_decode_block(unsigned char *out,const unsigned char *p,size_t len,uint64_t count,
                  unsigned width,unsigned array,unsigned enc,unsigned dict,unsigned bits,
                  char *e,size_t ne) {
    int real=array!=BQP_AP&&array!=BQP_AI&&array!=BQP_QP&&array!=BQP_QI;
    if(enc!=BQP_DICT&&(dict||bits)) return fail(e,ne,"unexpected dictionary metadata");
    if(enc==BQP_RAW) {
        if(len!=count*width) return fail(e,ne,"raw length mismatch");
        memcpy(out,p,len); return 0;
    }
    if(enc==BQP_DELTA) {
        if(real||len<8) return fail(e,ne,"bad delta block");
        uint64_t v=bqp_u64(p); size_t pos=8;
        for(uint64_t i=0;i<count;i++) {
            if(i) {
                uint64_t z=0; unsigned shift=0; unsigned char b;
                do {
                    if(pos==len||shift>=64) return fail(e,ne,"invalid delta varint");
                    b=p[pos++]; if(shift==63&&(b&127)>1) return fail(e,ne,"varint overflow");
                    z|=(uint64_t)(b&127)<<shift; shift+=7;
                } while(b&128);
                uint64_t mag=(z>>1)+(z&1);
                if(z&1) { if(mag>v) return fail(e,ne,"negative decoded index"); v-=mag; }
                else { if(v>UINT64_MAX-mag) return fail(e,ne,"delta overflow"); v+=mag; }
            }
            if(width==4) { if(v>UINT32_MAX) return fail(e,ne,"index overflow"); bqp_p32(out+i*4,(uint32_t)v); }
            else bqp_p64(out+i*8,v);
        }
        if(pos!=len) return fail(e,ne,"extra delta bytes");
        return 0;
    }
    if(!real) return fail(e,ne,"floating encoding for index array");
    double *d=(double*)out;
    if(enc==BQP_CONST) {
        if(len!=8) return fail(e,ne,"bad constant size");
        for(uint64_t i=0;i<count;i++) memcpy(d+i,p,8);
    } else if(enc==BQP_DICT) {
        if(dict<2||dict>4096||bits<1||bits>12||dict>(1u<<bits)||
           len!=(size_t)dict*8+(count*bits+7)/8) return fail(e,ne,"bad dictionary metadata");
        const unsigned char *codes=p+dict*8;
        for(uint64_t i=0;i<count;i++) {
            uint64_t bit=i*bits; unsigned code=0,shift=(unsigned)(bit%8);
            for(unsigned k=0;k<(bits+shift+7)/8;k++) code|=(unsigned)codes[bit/8+k]<<(8*k);
            code=(code>>shift)&((1u<<bits)-1);
            if(code>=dict) return fail(e,ne,"dictionary index out of range");
            memcpy(d+i,p+code*8,8);
        }
    } else {
        unsigned w=enc==BQP_I8?1:enc==BQP_I16?2:(enc==BQP_I32||enc==BQP_F32)?4:0;
        if(!w||len!=count*w) return fail(e,ne,"bad numeric encoding/length");
        for(uint64_t i=0;i<count;i++) {
            if(enc==BQP_I8) d[i]=(int8_t)p[i];
            else if(enc==BQP_I16) { int16_t x; memcpy(&x,p+2*i,2); d[i]=x; }
            else if(enc==BQP_I32) { int32_t x; memcpy(&x,p+4*i,4); d[i]=x; }
            else { float x; memcpy(&x,p+4*i,4); d[i]=x; }
        }
    }
    return 0;
}
int bqp_visit(const char *path,bqp_info *h,bqp_block_fn cb,void *ctx,char *e,size_t ne) {
    FILE *f=fopen(path,"rb"); if(!f) return fail(e,ne,"cannot open %s",path);
    unsigned char *stored=0,*packed=0,*out=0; int rc=-1;
    uint64_t seen[BQP_ARRAYS+1]={0},lastptr[BQP_ARRAYS+1]={0};
    if(header(f,h,e,ne)) goto done;
    stored=malloc(BQP_MAX_PACKED); packed=malloc(BQP_MAX_PACKED);
    out=malloc((size_t)h->block_elements*8);
    if(!stored||!packed||!out) { fail(e,ne,"block buffer allocation failed"); goto done; }
    for(uint64_t b=0;b<h->blocks;b++) {
        unsigned char bh[64];
        if(fread(bh,1,64,f)!=64) { fail(e,ne,"truncated block header"); goto done; }
        if(bqp_u32(bh)!=0x314b4c42u||(uint32_t)crc32(0,bh,60)!=bqp_u32(bh+60)) {
            fail(e,ne,"block header checksum/magic mismatch"); goto done;
        }
        unsigned a=bqp_u32(bh+4),enc=bqp_u32(bh+24),comp=bqp_u32(bh+28);
        uint64_t start=bqp_u64(bh+8),count=bqp_u64(bh+16),plen=bqp_u64(bh+32),slen=bqp_u64(bh+40);
        if(a<1||a>BQP_ARRAYS||!count||count>h->block_elements||start!=seen[a]||
           start>bqp_array_count(h,a)||count>bqp_array_count(h,a)-start||
           plen>BQP_MAX_PACKED||slen>BQP_MAX_PACKED||!plen||!slen||comp>1) {
            fail(e,ne,"invalid block dimensions/offsets/codec"); goto done;
        }
        if(fread(stored,1,(size_t)slen,f)!=slen) { fail(e,ne,"truncated block payload"); goto done; }
        unsigned pad=(unsigned)((8-slen%8)%8);
        for(unsigned k=0;k<pad;k++) if(fgetc(f)!=0) { fail(e,ne,"invalid block padding"); goto done; }
        const unsigned char *p=stored;
        if(comp) {
            if(!bqp_zstd_available()) { fail(e,ne,"libzstd.so.1 required for compressed file"); goto done; }
            size_t n=bqp_zstd_decompress(packed,(size_t)plen,stored,(size_t)slen);
            if(bqp_zstd_error(n)||n!=plen) { fail(e,ne,"zstd decompression failed/size mismatch"); goto done; }
            p=packed;
        } else if(slen!=plen) { fail(e,ne,"uncompressed payload size mismatch"); goto done; }
        unsigned width=bqp_element_bytes(h,a);
        if(bqp_decode_block(out,p,(size_t)plen,count,width,a,enc,bqp_u32(bh+52),bqp_u32(bh+56),e,ne)) goto done;
        if((uint32_t)crc32(0,out,(uInt)(count*width))!=bqp_u32(bh+48)) {
            fail(e,ne,"decoded checksum mismatch in %s block %llu",bqp_array_name(a),(unsigned long long)b); goto done;
        }
        for(uint64_t i=0;i<count;i++) {
            if(a==BQP_AP||a==BQP_QP) {
                uint64_t v=bqp_u64(out+i*8),nz=a==BQP_AP?h->nnz:h->qnnz;
                if((start+i==0&&v!=0)||v<lastptr[a]||v>nz||
                   (start+i==h->cols&&v!=nz)) { fail(e,ne,"invalid CSC pointers"); goto done; }
                lastptr[a]=v;
            } else if(a==BQP_AI||a==BQP_QI) {
                uint64_t v=width==4?bqp_u32(out+i*4):bqp_u64(out+i*8);
                if(v>=(a==BQP_AI?h->rows:h->cols)) { fail(e,ne,"sparse index out of bounds"); goto done; }
            } else {
                double v; memcpy(&v,out+i*8,8);
                if(isnan(v)||((a==BQP_AX||a==BQP_QX||a==BQP_C)&&!isfinite(v))) {
                    fail(e,ne,"nonfinite coefficient or NaN bound"); goto done;
                }
            }
        }
        if(cb&&cb(ctx,h,a,start,count,out,e,ne)) goto done;
        seen[a]+=count;
    }
    for(unsigned a=1;a<=BQP_ARRAYS;a++) if(seen[a]!=bqp_array_count(h,a)) {
        fail(e,ne,"missing elements in %s",bqp_array_name(a)); goto done;
    }
    if(fgetc(f)!=EOF||ferror(f)) { fail(e,ne,"trailing data/read error"); goto done; }
    rc=0;
done:
    free(out); free(packed); free(stored); fclose(f); return rc;
}
static int copy_block(void *ctx,const bqp_info *h,unsigned a,uint64_t start,uint64_t n,
                      const void *p,char *e,size_t ne) {
    bqp_model *m=ctx; unsigned w=bqp_element_bytes(h,a);
    if(memcmp(&m->info,h,sizeof *h)) return fail(e,ne,"file changed during load");
    memcpy((unsigned char*)m->array[a]+start*w,p,(size_t)n*w); return 0;
}
void bqp_free(bqp_model *m) {
    if(!m) return;
    for(unsigned a=1;a<=BQP_ARRAYS;a++) free(m->array[a]);
    memset(m,0,sizeof *m);
}
int bqp_load(const char *path,bqp_model *m,uint64_t limit,char *e,size_t ne) {
    uint64_t bytes; memset(m,0,sizeof *m); m->device=-1;
    if(bqp_read_info(path,&m->info,e,ne)) return -1;
    if(bqp_memory_bytes(&m->info,&bytes)||(limit&&bytes>limit)) return fail(e,ne,"decoded model exceeds memory limit");
    for(unsigned a=1;a<=BQP_ARRAYS;a++) {
        uint64_t n=bqp_array_count(&m->info,a)*bqp_element_bytes(&m->info,a);
        if(n && !(m->array[a]=malloc((size_t)n))) { fail(e,ne,"array allocation failed"); bqp_free(m); return -1; }
    }
    bqp_info original=m->info, actual;
    if(bqp_visit(path,&actual,copy_block,m,e,ne)) { bqp_free(m); return -1; }
    /* Visit reads the header again. Detect replacement before trusting layout. */
    if(memcmp(&original,&actual,sizeof actual)) { fail(e,ne,"file changed during load"); bqp_free(m); return -1; }
    for(unsigned matrix=0;matrix<2;matrix++) {
        unsigned pa=matrix?BQP_QP:BQP_AP,ia=matrix?BQP_QI:BQP_AI;
        if(matrix&&!m->info.qnnz) continue;
        const uint64_t *p=m->array[pa]; unsigned w=bqp_element_bytes(&m->info,ia);
        for(uint64_t j=0;j<m->info.cols;j++) {
            uint64_t prev=0;
            for(uint64_t k=p[j];k<p[j+1];k++) {
                uint64_t r=w==4?((uint32_t*)m->array[ia])[k]:((uint64_t*)m->array[ia])[k];
                if((k>p[j]&&r<=prev)||(matrix&&r>j)) { fail(e,ne,"noncanonical CSC ordering/triangle"); bqp_free(m); return -1; }
                prev=r;
            }
        }
    }
    return 0;
}
