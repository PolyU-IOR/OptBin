#include "internal.h"
#include <dlfcn.h>
#include <pthread.h>
static pthread_once_t once=PTHREAD_ONCE_INIT;
static void *lib;
static size_t (*bound_fn)(size_t);
static size_t (*compress_fn)(void*,size_t,const void*,size_t,int);
static size_t (*decompress_fn)(void*,size_t,const void*,size_t);
static unsigned (*error_fn)(size_t);
static const char *(*name_fn)(size_t);
static void initialize(void) {
    lib=dlopen("libzstd.so.1",RTLD_NOW|RTLD_LOCAL);
    if(!lib) return;
    *(void**)(&bound_fn)=dlsym(lib,"ZSTD_compressBound");
    *(void**)(&compress_fn)=dlsym(lib,"ZSTD_compress");
    *(void**)(&decompress_fn)=dlsym(lib,"ZSTD_decompress");
    *(void**)(&error_fn)=dlsym(lib,"ZSTD_isError");
    *(void**)(&name_fn)=dlsym(lib,"ZSTD_getErrorName");
    if(!bound_fn||!compress_fn||!decompress_fn||!error_fn||!name_fn) {
        dlclose(lib); lib=0;
    }
}
int bqp_zstd_available(void) { pthread_once(&once,initialize); return lib!=0; }
size_t bqp_zstd_bound(size_t n) { return bound_fn(n); }
size_t bqp_zstd_compress(void *d,size_t c,const void *s,size_t n,int l) { return compress_fn(d,c,s,n,l); }
size_t bqp_zstd_decompress(void *d,size_t c,const void *s,size_t n) { return decompress_fn(d,c,s,n); }
int bqp_zstd_error(size_t n) { return error_fn(n)!=0; }
const char *bqp_zstd_error_name(size_t n) { return name_fn(n); }
