#define _POSIX_C_SOURCE 200809L
#include "internal.h"
#include <stdlib.h>
#include <math.h>
#include <limits.h>
#include <sys/stat.h>
#include <zlib.h>

static int bad(char *e,size_t n,const char *s) { if(e&&n)snprintf(e,n,"%s",s);return -1; }

int optb_manifest_decode(const unsigned char *h,size_t hn,const unsigned char *dir,size_t dn,
                         uint64_t file_bytes,optb_model *m,char *e,size_t ne) {
    if(!m)return bad(e,ne,"null manifest output");
    memset(m,0,sizeof *m);
    if(!bqp_platform()||!h||hn!=128||memcmp(h,"OPTBIN2\n",8)||bqp_u32(h+8)!=2||bqp_u32(h+12)!=128)
        return bad(e,ne,"unsupported OptBin platform/magic/version/header");
    if((uint32_t)crc32(0,h,124)!=bqp_u32(h+124))return bad(e,ne,"header checksum mismatch");
    optb_info *p=&m->info;
    p->sections=bqp_u64(h+16);p->blocks=bqp_u64(h+24);p->file_bytes=bqp_u64(h+32);
    p->profile=bqp_u32(h+56);p->sense=(int32_t)bqp_u32(h+60);
    p->cols=bqp_u64(h+64);p->rows=bqp_u64(h+72);
    memcpy(&p->objective_offset,h+80,8);p->block_elements=bqp_u64(h+88);
    if(p->sections>4096||bqp_u64(h+40)!=128||bqp_u64(h+48)!=64*p->sections||dn!=64*p->sections||
       (dn&&!dir)||(p->profile!=OPTB_QUADRATIC&&p->profile!=OPTB_CONIC)||
       (p->sense!=1&&p->sense!=-1)||p->cols>=INT64_MAX||p->rows>=INT64_MAX||
       !isfinite(p->objective_offset)||!p->block_elements||p->block_elements>BQP_MAX_ELEMENTS)
        return bad(e,ne,"invalid OptBin metadata");
    for(unsigned i=96;i<124;i++)if(h[i])return bad(e,ne,"unknown required header feature");
    if(p->file_bytes!=file_bytes||file_bytes<128+dn)return bad(e,ne,"file size mismatch");
    m->section=calloc((size_t)p->sections,sizeof *m->section);
    if(p->sections&&!m->section)return bad(e,ne,"directory allocation failed");
    uint64_t offset=128+dn,blocks=0;
    for(uint64_t k=0;k<p->sections;k++) {
        const unsigned char *d=dir+64*k;optb_section *s=m->section+k;
        if((uint32_t)crc32(0,d,60)!=bqp_u32(d+60))return bad(e,ne,"directory checksum mismatch");
        s->id=bqp_u32(d);s->type=bqp_u32(d+4);s->count=bqp_u64(d+8);s->offset=bqp_u64(d+16);
        s->stored_bytes=bqp_u64(d+24);s->blocks=bqp_u64(d+32);s->flags=bqp_u64(d+40);
        unsigned w=optb_type_bytes(s->type);
        if(!s->id||(k&&s->id<=m->section[k-1].id)||!w||s->count>SIZE_MAX/w||s->count>INT64_MAX||
           s->offset!=offset||s->stored_bytes>file_bytes-offset||s->blocks>s->stored_bytes/64||
           s->flags>1||(!s->count&&(s->blocks||s->stored_bytes))||(s->count&&!s->blocks))
            return bad(e,ne,"invalid section directory");
        for(unsigned i=48;i<60;i++)if(d[i])return bad(e,ne,"nonzero directory reserved field");
        offset+=s->stored_bytes;
        if(s->blocks>UINT64_MAX-blocks)return bad(e,ne,"block count overflow");
        blocks+=s->blocks;
    }
    return offset==file_bytes&&blocks==p->blocks?0:bad(e,ne,"directory coverage mismatch");
}

int optb_manifest_read(FILE *f,optb_model *m,char *e,size_t ne) {
    unsigned char h[128];struct stat st;
    if(fread(h,1,128,f)!=128)return bad(e,ne,"truncated OptBin header");
    uint64_t count=bqp_u64(h+16);
    if(count>4096)return bad(e,ne,"invalid section count");
    size_t n=(size_t)count*64;unsigned char *d=malloc(n?n:1);
    if(!d)return bad(e,ne,"directory buffer allocation failed");
    int rc=-1;
    if(fread(d,1,n,f)!=n||fstat(fileno(f),&st)||st.st_size<128)bad(e,ne,"directory truncation/stat failure");
    else rc=optb_manifest_decode(h,128,d,n,(uint64_t)st.st_size,m,e,ne);
    free(d);return rc;
}

int optb_allocate(optb_model *m,uint64_t limit,char *e,size_t ne) {
    uint64_t bytes=0;
    for(uint64_t k=0;k<m->info.sections;k++) {
        const optb_section *s=m->section+k;uint64_t n=s->count*optb_type_bytes(s->type);
        if(n>UINT64_MAX-bytes||(limit&&(bytes>limit||n>limit-bytes)))return bad(e,ne,"decoded arrays exceed limit");
        bytes+=n;
    }
    for(uint64_t k=0;k<m->info.sections;k++) {
        optb_section *s=m->section+k;size_t n=(size_t)s->count*optb_type_bytes(s->type);
        if(n&&!(s->data=malloc(n)))return bad(e,ne,"array allocation failed");
    }
    return 0;
}

int optb_walk_encoded(const optb_model *m,optb_bytes_fn read,void *source,
                     optb_block_fn cb,void *ctx,char *e,size_t ne) {
    unsigned char *stored=malloc(BQP_MAX_PACKED),*packed=malloc(BQP_MAX_PACKED);
    unsigned char *out=malloc((size_t)m->info.block_elements*8);int rc=-1;
    if(!stored||!packed||!out){bad(e,ne,"block allocation failed");goto done;}
    for(uint64_t k=0;k<m->info.sections;k++) {
        const optb_section *s=m->section+k;uint64_t seen=0,used=0;unsigned width=optb_type_bytes(s->type);
        for(uint64_t b=0;b<s->blocks;b++) {
            unsigned char h[64],padding[8];
            if(s->stored_bytes-used<64||read(source,s,used,h,64,e,ne))goto done;
            if(memcmp(h,"BLK1",4)||(uint32_t)crc32(0,h,60)!=bqp_u32(h+60)){
                bad(e,ne,"block header checksum mismatch");goto done;
            }
            uint64_t start=bqp_u64(h+8),n=bqp_u64(h+16),pn=bqp_u64(h+32),sn=bqp_u64(h+40);
            unsigned enc=bqp_u32(h+24),comp=bqp_u32(h+28),pad=(unsigned)(-sn%8);
            if(bqp_u32(h+4)!=s->id||start!=seen||!n||n>m->info.block_elements||n>s->count-seen||
               !pn||pn>BQP_MAX_PACKED||!sn||sn>BQP_MAX_PACKED||comp>1||
               (s->type==OPTB_U8&&enc!=BQP_RAW)||sn+pad>s->stored_bytes-used-64){
                bad(e,ne,"invalid block shape/encoding");goto done;
            }
            if(read(source,s,used+64,stored,(size_t)sn,e,ne)||
               (pad&&read(source,s,used+64+sn,padding,pad,e,ne)))goto done;
            for(unsigned j=0;j<pad;j++)if(padding[j]){bad(e,ne,"invalid padding");goto done;}
            const unsigned char *p=stored;
            if(comp) {
                if(!bqp_zstd_available()){bad(e,ne,"libzstd.so.1 required");goto done;}
                size_t z=bqp_zstd_decompress(packed,(size_t)pn,stored,(size_t)sn);
                if(bqp_zstd_error(z)||z!=pn){bad(e,ne,"decompression failed");goto done;}p=packed;
            }else if(pn!=sn){bad(e,ne,"raw length mismatch");goto done;}
            if(bqp_decode_block(out,p,(size_t)pn,n,width,s->type==OPTB_F64?BQP_AX:BQP_AP,
                                enc,bqp_u32(h+52),bqp_u32(h+56),e,ne))goto done;
            if((uint32_t)crc32(0,out,(uInt)(n*width))!=bqp_u32(h+48)){
                bad(e,ne,"decoded checksum mismatch");goto done;
            }
            if(cb&&cb(ctx,&m->info,s,start,n,out,e,ne))goto done;
            seen+=n;used+=64+sn+pad;
        }
        if(seen!=s->count||used!=s->stored_bytes){bad(e,ne,"section coverage mismatch");goto done;}
    }
    rc=0;
done:free(stored);free(packed);free(out);return rc;
}
