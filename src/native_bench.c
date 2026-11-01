#define _POSIX_C_SOURCE 200809L
#include "optbin_hdf5.h"
#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <zlib.h>

#ifdef OPTBIN_TEXT_BENCH
int optb_text_bench_load(const char *,const char *,optb_model *,char *,size_t);
void optb_text_bench_free(optb_model *);
#endif
static double gzip_seconds=0;
static int load(const char *,const char *,optb_model *,char *,size_t);
static int load_gzip(const char *kind,const char *path,optb_model *m,char *e,size_t ne){
    const char *dir=getenv("TMPDIR");if(!dir)dir="/tmp";
    size_t size=strlen(dir)+48;char *temp=malloc(size);if(!temp)return -1;
    snprintf(temp,size,"%s/optbin-gzip-bench-XXXXXX",dir);
    struct timespec a,b;clock_gettime(CLOCK_MONOTONIC,&a);
    int fd=mkstemp(temp),rc=-1;FILE *out=fd<0?NULL:fdopen(fd,"wb");gzFile in=gzopen(path,"rb");
    unsigned char *buffer=malloc(1024*1024);
    if(fd>=0&&!out)close(fd);
    if(!out||!in||!buffer){snprintf(e,ne,"gzip temporary/input allocation failed");goto done;}
    gzbuffer(in,1024*1024);
    int n;
    while((n=gzread(in,buffer,1024*1024))>0){
        if(fwrite(buffer,1,(size_t)n,out)!=(size_t)n){snprintf(e,ne,"gzip temporary write failed");goto done;}
    }
    int z=0;gzerror(in,&z);
    if(n<0||(z!=Z_OK&&z!=Z_STREAM_END)){snprintf(e,ne,"gzip decompression/checksum failed");goto done;}
    {int status=gzclose(in);in=NULL;if(status!=Z_OK)goto done;}
    {int status=fclose(out);out=NULL;if(status)goto done;}
    clock_gettime(CLOCK_MONOTONIC,&b);
    gzip_seconds=b.tv_sec-a.tv_sec+1e-9*(b.tv_nsec-a.tv_nsec);
    rc=load(kind,temp,m,e,ne);
done:
    if(in)gzclose(in);if(out)fclose(out);free(buffer);unlink(temp);free(temp);return rc;
}

static int load(const char *kind,const char *path,optb_model *m,char *e,size_t ne){
    if(!strcmp(kind,"hdf5-gzip"))return load_gzip("hdf5",path,m,e,ne);
    if(!strcmp(kind,"optbin-gzip"))return load_gzip("optbin",path,m,e,ne);
#ifdef OPTBIN_TEXT_BENCH
    if(!strcmp(kind,"cbf")||!strncmp(kind,"mps-",4))return optb_text_bench_load(kind,path,m,e,ne);
#endif
    if(!strcmp(kind,"hdf5"))return optb_h5_load(path,m,0,e,ne);
    if(!strcmp(kind,"optbin")||!strcmp(kind,"bqp"))return optb_load(path,m,0,e,ne);
    snprintf(e,ne,"unknown reader");return -1;
}
static void release(const char *kind,optb_model *m){
#ifdef OPTBIN_TEXT_BENCH
    if(!strcmp(kind,"cbf")||!strncmp(kind,"mps-",4)){optb_text_bench_free(m);return;}
#else
    (void)kind;
#endif
    optb_free(m);
}
static int equal(const optb_model *a,const optb_model *b){
    if(a->info.profile!=b->info.profile||a->info.rows!=b->info.rows||a->info.cols!=b->info.cols||
       a->info.sense!=b->info.sense||memcmp(&a->info.objective_offset,&b->info.objective_offset,8))return 0;
    for(unsigned id=1;id<=47;id++){
        const optb_section *s=optb_get(a,id),*t=optb_get(b,id);
        if(!s||!t){if((s&&s->count)||(t&&t->count))return 0;continue;}
        if(s->type!=t->type||s->count!=t->count||
           (s->count&&memcmp(s->data,t->data,(size_t)s->count*optb_type_bytes(s->type))))return 0;
    }
    return 1;
}
static int digest(const optb_model *m,char out[65]){
    /* Stable OpenSSL EVP ABI; optional benchmark dependency, outside the timed
     * reader. Runtime loading avoids an OpenSSL development-header dependency. */
    void *lib=dlopen("libcrypto.so.3",RTLD_NOW|RTLD_LOCAL);
    if(!lib)lib=dlopen("libcrypto.so",RTLD_NOW|RTLD_LOCAL);
    if(!lib){fprintf(stderr,"OpenSSL libcrypto runtime required for benchmark fingerprints\n");return -1;}
    void *(*newctx)(void)=NULL;void (*freectx)(void*)=NULL;const void *(*sha256)(void)=NULL;
    int (*init)(void*,const void*,void*)=NULL,(*update)(void*,const void*,size_t)=NULL,(*final)(void*,unsigned char*,unsigned*)=NULL;
    const char *names[]={"EVP_MD_CTX_new","EVP_MD_CTX_free","EVP_sha256","EVP_DigestInit_ex","EVP_DigestUpdate","EVP_DigestFinal_ex"};
    void *slots[]={&newctx,&freectx,&sha256,&init,&update,&final};
    for(unsigned i=0;i<6;i++){void *p=dlsym(lib,names[i]);if(!p){dlclose(lib);return -1;}memcpy(slots[i],&p,sizeof p);}
    void *ctx=newctx();unsigned char hash[64];unsigned n=0;int ok=ctx&&init(ctx,sha256(),NULL)==1;
    if(!ok){if(ctx)freectx(ctx);dlclose(lib);return -1;}
    ok&=update(ctx,&m->info.profile,sizeof m->info.profile);ok&=update(ctx,&m->info.sense,sizeof m->info.sense);
    ok&=update(ctx,&m->info.rows,8);ok&=update(ctx,&m->info.cols,8);ok&=update(ctx,&m->info.objective_offset,8);
    for(unsigned id=1;id<=47;id++){
        const optb_section *s=optb_get(m,id);if(!s||!s->count)continue;
        ok&=update(ctx,&s->id,4);ok&=update(ctx,&s->type,4);ok&=update(ctx,&s->count,8);
        ok&=update(ctx,s->data,(size_t)s->count*optb_type_bytes(s->type));
    }
    int rc=ok&&final(ctx,hash,&n)==1&&n==32?0:-1;freectx(ctx);dlclose(lib);
    for(unsigned i=0;!rc&&i<32;i++)sprintf(out+2*i,"%02x",hash[i]);
    return rc;
}
int main(int argc,char **argv){
    if(argc!=3&&argc!=5){fprintf(stderr,"usage: optb_native_bench optbin|bqp|hdf5 FILE [REFERENCE_KIND REFERENCE_FILE]\n");return 2;}
    optb_model m={0},reference={0};char e[1024]={0},sha[65]={0};struct timespec a,b;struct rusage usage;
    clock_gettime(CLOCK_MONOTONIC,&a);
    if(load(argv[1],argv[2],&m,e,sizeof e)){fprintf(stderr,"%s\n",e);return 1;}
    clock_gettime(CLOCK_MONOTONIC,&b);getrusage(RUSAGE_SELF,&usage);
    uint64_t bytes=0;for(uint64_t k=0;k<m.info.sections;k++)bytes+=m.section[k].count*optb_type_bytes(m.section[k].type);
    double measured_gzip_seconds=gzip_seconds;
    if(digest(&m,sha)){release(argv[1],&m);return 1;}
    if(argc==5){
        if(load(argv[3],argv[4],&reference,e,sizeof e)||!equal(&m,&reference)){
            fprintf(stderr,"reference load or bitwise comparison failed: %s\n",e);release(argv[3],&reference);release(argv[1],&m);return 1;
        }
        release(argv[3],&reference);
    }
    struct stat st;if(stat(argv[2],&st)){release(argv[1],&m);return 1;}
    printf("{\"seconds\":%.9g,\"gzip_to_temporary_seconds\":%.9g,\"max_rss_bytes\":%llu,\"user_seconds\":%.9g,\"system_seconds\":%.9g,"
           "\"input_blocks\":%ld,\"bytes\":%llu,\"decoded_bytes\":%llu,\"rows\":%llu,\"cols\":%llu,"
           "\"numerical_sha256\":\"%s\",\"bitwise_reference_equal\":%s,\"hdf5_version\":\"%s\"}\n",
           b.tv_sec-a.tv_sec+1e-9*(b.tv_nsec-a.tv_nsec),measured_gzip_seconds,(unsigned long long)usage.ru_maxrss*1024,
           usage.ru_utime.tv_sec+1e-6*usage.ru_utime.tv_usec,usage.ru_stime.tv_sec+1e-6*usage.ru_stime.tv_usec,
           usage.ru_inblock,(unsigned long long)st.st_size,(unsigned long long)bytes,
           (unsigned long long)m.info.rows,(unsigned long long)m.info.cols,sha,argc==5?"true":"null",optb_h5_version());
    release(argv[1],&m);
    return 0;
}
