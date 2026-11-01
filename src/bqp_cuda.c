#include "bqp.h"
#include <dlfcn.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <zlib.h>
/* Only the stable CUDA runtime C ABI is needed. No CUDA compiler is required. */
static void *lib;
static pthread_once_t once=PTHREAD_ONCE_INIT;
static int (*set_device)(int);
static int (*mem_info)(size_t*,size_t*);
static int (*device_alloc)(void**,size_t);
static int (*device_free)(void*);
static int (*device_copy)(void*,const void*,size_t,int);
static const char *(*error_string)(int);
static void initialize(void) {
    const char *override=getenv("BQP_CUDART");
    if(override&&*override) lib=dlopen(override,RTLD_NOW|RTLD_LOCAL);
    const char *names[]={"libcudart.so.12","libcudart.so.13","libcudart.so.11.0","libcudart.so",
        "/usr/local/cuda/lib64/libcudart.so",NULL};
    for(unsigned i=0;names[i]&&!lib;i++) lib=dlopen(names[i],RTLD_NOW|RTLD_LOCAL);
    if(!lib) return;
    *(void**)(&set_device)=dlsym(lib,"cudaSetDevice");
    *(void**)(&mem_info)=dlsym(lib,"cudaMemGetInfo");
    *(void**)(&device_alloc)=dlsym(lib,"cudaMalloc");
    *(void**)(&device_free)=dlsym(lib,"cudaFree");
    *(void**)(&device_copy)=dlsym(lib,"cudaMemcpy");
    *(void**)(&error_string)=dlsym(lib,"cudaGetErrorString");
    if(!set_device||!mem_info||!device_alloc||!device_free||!device_copy||!error_string) {
        dlclose(lib); lib=NULL;
    }
}
static int cuda_error(int code,char *e,size_t n,const char *op) {
    if(code) { if(e&&n) snprintf(e,n,"%s: %s",op,error_string(code)); return -1; }
    return 0;
}
static int copy_block(void *ctx,const bqp_info *h,unsigned a,uint64_t start,uint64_t count,
                      const void *p,char *e,size_t n) {
    bqp_model *m=ctx;
    if(memcmp(&m->info,h,sizeof *h)) { snprintf(e,n,"file changed during CUDA load"); return -1; }
    unsigned w=bqp_element_bytes(h,a);
    return cuda_error(device_copy((unsigned char*)m->array[a]+start*w,p,(size_t)count*w,1),e,n,"cudaMemcpy H2D");
}
void bqp_free_cuda(bqp_model *m) {
    if(!m) return;
    pthread_once(&once,initialize);
    if(lib) {
        set_device(m->device);
        for(unsigned a=1;a<=BQP_ARRAYS;a++) if(m->array[a]) device_free(m->array[a]);
    }
    memset(m,0,sizeof *m);
}
int bqp_load_cuda(const char *path,bqp_model *m,int device,uint64_t limit,char *e,size_t n) {
    memset(m,0,sizeof *m); m->device=device;
    pthread_once(&once,initialize);
    if(!lib) { snprintf(e,n,"CUDA runtime not found"); return -1; }
    if(cuda_error(set_device(device),e,n,"cudaSetDevice")) return -1;
    if(bqp_read_info(path,&m->info,e,n)) return -1;
    uint64_t bytes; size_t available,total;
    if(bqp_memory_bytes(&m->info,&bytes)||cuda_error(mem_info(&available,&total),e,n,"cudaMemGetInfo")) return -1;
    /* Leave capacity for existing work and runtime allocations. This reader
     * does not allocate solver workspace; callers must budget that separately. */
    uint64_t safe=(uint64_t)available-available/10;
    if((limit&&bytes>limit)||bytes>safe) {
        snprintf(e,n,"decoded model (%llu bytes) exceeds CUDA budget (%llu bytes)",
            (unsigned long long)bytes,(unsigned long long)(limit&&limit<safe?limit:safe)); return -1;
    }
    for(unsigned a=1;a<=BQP_ARRAYS;a++) {
        size_t size=(size_t)(bqp_array_count(&m->info,a)*bqp_element_bytes(&m->info,a));
        if(size&&cuda_error(device_alloc(&m->array[a],size),e,n,"cudaMalloc")) { bqp_free_cuda(m); return -1; }
    }
    bqp_info actual;
    if(bqp_visit(path,&actual,copy_block,m,e,n)) { bqp_free_cuda(m); return -1; }
    return 0;
}
int bqp_cuda_checksums(const bqp_model *m,uint32_t crc[BQP_ARRAYS+1],char *e,size_t n) {
    pthread_once(&once,initialize);
    if(!lib) { snprintf(e,n,"CUDA runtime not found"); return -1; }
    if(cuda_error(set_device(m->device),e,n,"cudaSetDevice")) return -1;
    const size_t chunk=4*1024*1024; unsigned char *buf=malloc(chunk);
    if(!buf) { snprintf(e,n,"checksum buffer allocation failed"); return -1; }
    memset(crc,0,(BQP_ARRAYS+1)*sizeof *crc);
    for(unsigned a=1;a<=BQP_ARRAYS;a++) {
        uint64_t size=bqp_array_count(&m->info,a)*bqp_element_bytes(&m->info,a);
        for(uint64_t start=0;start<size;) {
            size_t take=size-start<chunk?(size_t)(size-start):chunk;
            if(cuda_error(device_copy(buf,(unsigned char*)m->array[a]+start,take,2),e,n,"cudaMemcpy D2H")) { free(buf); return -1; }
            crc[a]=(uint32_t)crc32(crc[a],buf,(uInt)take); start+=take;
        }
    }
    free(buf); return 0;
}
