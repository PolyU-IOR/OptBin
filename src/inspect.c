#define _POSIX_C_SOURCE 200809L
#include "optbin.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <errno.h>
int main(int argc,char **argv) {
    if(argc<2) { fprintf(stderr,"usage: optb_inspect FILE [--load|--stream] [--limit BYTES]\n"); return 2; }
    int stream=0; uint64_t limit=0;
    for(int j=2;j<argc;j++) {
        if(!strcmp(argv[j],"--stream")) stream=1;
        else if(!strcmp(argv[j],"--load")) stream=0;
        else if(!strcmp(argv[j],"--limit")&&j+1<argc) {
            const char *value=argv[++j]; char *end; errno=0;
            unsigned long long parsed=strtoull(value,&end,10);
            if(value[0]<'0'||value[0]>'9'||*end||errno==ERANGE||parsed>UINT64_MAX) {
                fprintf(stderr,"optb_inspect: --limit must be between 0 and 2^64-1\n"); return 2;
            }
            limit=(uint64_t)parsed;
        }
        else return 2;
    }
    char error[512]; optb_info info; optb_model m={0}; struct timespec a,b;
    clock_gettime(CLOCK_MONOTONIC,&a);
    int rc=stream?optb_visit(argv[1],&info,NULL,NULL,error,sizeof error):optb_load(argv[1],&m,limit,error,sizeof error);
    if(rc) { fprintf(stderr,"optb_inspect: %s\n",error); return 1; }
    if(!stream) info=m.info;
    clock_gettime(CLOCK_MONOTONIC,&b);
    printf("{\"profile\":%u,\"rows\":%llu,\"cols\":%llu,\"sections\":%llu,\"file_bytes\":%llu,\"seconds\":%.9f,\"validation\":\"%s\"}\n",
        info.profile,(unsigned long long)info.rows,(unsigned long long)info.cols,
        (unsigned long long)info.sections,(unsigned long long)info.file_bytes,
        b.tv_sec-a.tv_sec+(b.tv_nsec-a.tv_nsec)*1e-9,stream?"block":"full-model");
    optb_free(&m); return 0;
}
