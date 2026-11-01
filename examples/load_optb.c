#include "optbin.h"
#include <stdio.h>
int main(int argc,char **argv) {
    if(argc!=2) { fprintf(stderr,"usage: load_optb MODEL.optb\n"); return 2; }
    optb_model model={0}; char error[512];
    if(optb_load(argv[1],&model,UINT64_C(8)*1024*1024*1024,error,sizeof error)) {
        fprintf(stderr,"%s\n",error); return 1;
    }
    const optb_section *values=optb_get(&model,OPTB_AX);
    printf("profile=%u rows=%llu columns=%llu entries=%llu\n",model.info.profile,
        (unsigned long long)model.info.rows,(unsigned long long)model.info.cols,
        (unsigned long long)(values?values->count:0));
    /* OPTB_AX decodes to double; OPTB_AP to uint64_t. Check OPTB_AI's type
       before interpreting row indices as uint32_t or uint64_t. */
    optb_free(&model); return 0;
}
