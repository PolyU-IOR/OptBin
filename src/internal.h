#ifndef BQP_INTERNAL_H
#define BQP_INTERNAL_H
#include "bqp.h"
#include "optbin.h"
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
enum { BQP_RAW=0, BQP_CONST=1, BQP_I8=2, BQP_I16=3, BQP_I32=4,
       BQP_F32=5, BQP_DICT=6, BQP_DELTA=7 };
#define BQP_HEADER 128
#define BQP_BLOCK_HEADER 64
#define BQP_MAX_ELEMENTS 1048576u
#define BQP_MAX_PACKED (16u*1024u*1024u)
static inline uint32_t bqp_u32(const unsigned char *p) {
    return (uint32_t)p[0]|((uint32_t)p[1]<<8)|((uint32_t)p[2]<<16)|((uint32_t)p[3]<<24);
}
static inline uint64_t bqp_u64(const unsigned char *p) {
    return bqp_u32(p)|((uint64_t)bqp_u32(p+4)<<32);
}
static inline void bqp_p32(unsigned char *p, uint32_t x) {
    for(unsigned i=0;i<4;i++) p[i]=(unsigned char)(x>>(8*i));
}
static inline void bqp_p64(unsigned char *p, uint64_t x) {
    for(unsigned i=0;i<8;i++) p[i]=(unsigned char)(x>>(8*i));
}
static inline int bqp_platform(void) {
    uint64_t x=1; double d=1.; uint64_t b=0;
    if(sizeof(double)!=8 || sizeof(float)!=4) return 0;
    memcpy(&b,&d,8);
    return *(unsigned char*)&x==1 && b==UINT64_C(0x3ff0000000000000);
}
/* Stable libzstd ABI, runtime loaded so development headers aren't required. */
#ifdef __cplusplus
extern "C" {
#endif
int bqp_zstd_available(void);
int bqp_decode_block(unsigned char *,const unsigned char *,size_t,uint64_t,
                     unsigned,unsigned,unsigned,unsigned,unsigned,char *,size_t);
size_t bqp_zstd_bound(size_t);
size_t bqp_zstd_compress(void *,size_t,const void *,size_t,int);
size_t bqp_zstd_decompress(void *,size_t,const void *,size_t);
int bqp_zstd_error(size_t);
const char *bqp_zstd_error_name(size_t);
/* Shared manifest, bounded byte-source traversal, and allocation for native
 * OptBin and optional HDF5 frontends. Each source must return exactly n bytes. */
typedef int (*optb_bytes_fn)(void *,const optb_section *,uint64_t,void *,size_t,char *,size_t);
int optb_manifest_decode(const unsigned char *,size_t,const unsigned char *,size_t,
                         uint64_t,optb_model *,char *,size_t);
int optb_manifest_read(FILE *,optb_model *,char *,size_t);
int optb_allocate(optb_model *,uint64_t,char *,size_t);
int optb_walk_encoded(const optb_model *,optb_bytes_fn,void *,optb_block_fn,void *,char *,size_t);
#ifdef __cplusplus
}
#endif
#endif
