#include "optbin.h"
#include "internal.h"
#include <stdio.h>
#include <limits.h>
#include <zlib.h>

static int bad(char *e,size_t n,const char *s) { if(e&&n) snprintf(e,n,"%s",s);return -1; }
int optb_decode_section(const optb_section *s,const void *encoded,size_t bytes,
                        void *decoded,size_t output,uint64_t limit,
                        void *scratch,size_t scratch_bytes,char *e,size_t ne) {
    if(!bqp_platform()||!s||!s->id||!limit||limit>BQP_MAX_ELEMENTS)
        return bad(e,ne,"invalid section/platform/block limit");
    unsigned width=optb_type_bytes(s->type);
    if(!width||s->count>SIZE_MAX/width||s->count>INT64_MAX||s->count*width!=output||
       bytes!=s->stored_bytes||s->blocks>bytes/64||(bytes&&!encoded)||(output&&!decoded))
        return bad(e,ne,"invalid section buffers/dimensions");
    const unsigned char *src=encoded;unsigned char *dst=decoded;
    uint64_t seen=0;size_t pos=0;
    for(uint64_t k=0;k<s->blocks;k++) {
        if(bytes-pos<64) return bad(e,ne,"truncated section block header");
        const unsigned char *h=src+pos;
        if(memcmp(h,"BLK1",4)||(uint32_t)crc32(0,h,60)!=bqp_u32(h+60))
            return bad(e,ne,"section block header checksum mismatch");
        unsigned enc=bqp_u32(h+24),comp=bqp_u32(h+28);
        uint64_t start=bqp_u64(h+8),n=bqp_u64(h+16),pn=bqp_u64(h+32),sn=bqp_u64(h+40);
        unsigned pad=(unsigned)(-sn%8);
        if(bqp_u32(h+4)!=s->id||start!=seen||!n||n>limit||n>s->count-seen||
           !pn||pn>BQP_MAX_PACKED||!sn||sn>BQP_MAX_PACKED||comp>1||
           (s->type==OPTB_U8&&enc!=BQP_RAW)||sn+pad>bytes-pos-64)
            return bad(e,ne,"invalid section block framing");
        const unsigned char *payload=h+64;
        for(unsigned j=0;j<pad;j++) if(payload[sn+j]) return bad(e,ne,"section padding mismatch");
        if(comp) {
            if(!scratch||scratch_bytes<pn||!bqp_zstd_available()) return bad(e,ne,"section scratch/Zstd unavailable");
            size_t result=bqp_zstd_decompress(scratch,(size_t)pn,payload,(size_t)sn);
            if(bqp_zstd_error(result)||result!=pn) return bad(e,ne,"section decompression failed");
            payload=scratch;
        } else if(pn!=sn) return bad(e,ne,"section raw length mismatch");
        unsigned char *out=dst+seen*width;
        if(bqp_decode_block(out,payload,(size_t)pn,n,width,s->type==OPTB_F64?BQP_AX:BQP_AP,
                            enc,bqp_u32(h+52),bqp_u32(h+56),e,ne)) return -1;
        if((uint32_t)crc32(0,out,(uInt)(n*width))!=bqp_u32(h+48))
            return bad(e,ne,"section decoded checksum mismatch");
        seen+=n;pos+=64+(size_t)sn+pad;
    }
    if(pos!=bytes||seen!=s->count) return bad(e,ne,"incomplete section coverage");
    return 0;
}
