#define _POSIX_C_SOURCE 200809L
#include "optbin_hdf5.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/stat.h>
int main(int argc,char **argv){
    if(argc!=4){fprintf(stderr,"usage: optb_h5_export INPUT.optb OUTPUT.h5 encoded|raw|gzip\n");return 2;}
    unsigned layout=!strcmp(argv[3],"encoded")?0:!strcmp(argv[3],"raw")?1:!strcmp(argv[3],"gzip")?2:99;
    char e[512]={0};struct timespec a,b;clock_gettime(CLOCK_MONOTONIC,&a);
    if(optb_h5_export(argv[1],argv[2],layout,e,sizeof e)){fprintf(stderr,"%s\n",e);return 1;}
    clock_gettime(CLOCK_MONOTONIC,&b);struct stat s;if(stat(argv[2],&s))return 1;
    printf("{\"bytes\":%llu,\"seconds\":%.9g,\"layout\":%u}\n",(unsigned long long)s.st_size,
           b.tv_sec-a.tv_sec+1e-9*(b.tv_nsec-a.tv_nsec),layout);return 0;
}
