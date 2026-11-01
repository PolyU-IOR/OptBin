#define _POSIX_C_SOURCE 200809L
#include "bqp.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/resource.h>
#include <zlib.h>
static double now(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec*1e-9; }
static int checksum(void *ctx,const bqp_info *h,unsigned a,uint64_t start,uint64_t count,
                    const void *p,char *error,size_t n) {
    (void)start; (void)error; (void)n; uint32_t *crc=ctx;
    crc[a]=(uint32_t)crc32(crc[a],p,(uInt)(count*bqp_element_bytes(h,a))); return 0;
}
int main(int argc,char **argv) {
    if(argc<2) { fprintf(stderr,"usage: bqp_inspect FILE [--info|--stream|--load|--cuda DEVICE] [--limit-gib N]\n"); return 2; }
    const char *mode="stream"; int device=0; uint64_t limit=0;
    for(int i=2;i<argc;i++) {
        if(!strcmp(argv[i],"--info")) mode="info";
        else if(!strcmp(argv[i],"--stream")) mode="stream";
        else if(!strcmp(argv[i],"--load")) mode="cpu";
        else if(!strcmp(argv[i],"--cuda")&&i+1<argc) { mode="cuda"; device=atoi(argv[++i]); }
        else if(!strcmp(argv[i],"--limit-gib")&&i+1<argc) {
            char *end; double g=strtod(argv[++i],&end);
            if(*end||!(g>0)||g>1000000) { fprintf(stderr,"invalid memory limit\n"); return 2; }
            limit=(uint64_t)(g*1073741824.);
        } else { fprintf(stderr,"unknown/incomplete option: %s\n",argv[i]); return 2; }
    }
    char error[512]={0}; bqp_info h; bqp_model m; uint32_t crc[BQP_ARRAYS+1]={0};
    double t=now(),loaded=t; int rc;
    if(!strcmp(mode,"info")) rc=bqp_read_info(argv[1],&h,error,sizeof error);
    else if(!strcmp(mode,"stream")) rc=bqp_visit(argv[1],&h,checksum,crc,error,sizeof error);
    else {
        rc=!strcmp(mode,"cuda")?bqp_load_cuda(argv[1],&m,device,limit,error,sizeof error):bqp_load(argv[1],&m,limit,error,sizeof error);
        loaded=now();
        if(!rc) {
            h=m.info;
            if(!strcmp(mode,"cuda")) rc=bqp_cuda_checksums(&m,crc,error,sizeof error);
            else {
                for(unsigned a=1;a<=BQP_ARRAYS;a++) {
                    uint64_t bytes=bqp_array_count(&h,a)*bqp_element_bytes(&h,a);
                    for(uint64_t start=0;start<bytes;) {
                        uInt take=bytes-start>1048576?1048576:(uInt)(bytes-start);
                        crc[a]=(uint32_t)crc32(crc[a],(unsigned char*)m.array[a]+start,take); start+=take;
                    }
                }
            }
            if(!strcmp(mode,"cuda")) bqp_free_cuda(&m); else bqp_free(&m);
        }
    }
    double done=now();
    if(rc) { fprintf(stderr,"bqp_inspect: %s\n",error); return 1; }
    if(!strcmp(mode,"info")||!strcmp(mode,"stream")) loaded=done;
    uint64_t bytes=0; bqp_memory_bytes(&h,&bytes); struct rusage ru; getrusage(RUSAGE_SELF,&ru);
    printf("{\"mode\":\"%s\",\"rows\":%llu,\"cols\":%llu,\"nnz\":%llu,\"qnnz\":%llu,\"file_bytes\":%llu,\"decoded_bytes\":%llu,\"load_seconds\":%.6f,\"verify_and_free_seconds\":%.6f,\"seconds\":%.6f,\"max_rss_kib\":%ld,\"array_crc32\":[",
        mode,(unsigned long long)h.rows,(unsigned long long)h.cols,(unsigned long long)h.nnz,(unsigned long long)h.qnnz,
        (unsigned long long)h.file_bytes,(unsigned long long)bytes,loaded-t,done-loaded,done-t,ru.ru_maxrss);
    for(unsigned a=1;a<=BQP_ARRAYS;a++) printf("%s%u",a>1?",":"",crc[a]);
    puts("]}"); return 0;
}
