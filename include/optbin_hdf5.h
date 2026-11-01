#ifndef OPTBIN_HDF5_H
#define OPTBIN_HDF5_H
#include "optbin.h"
#ifdef __cplusplus
extern "C" {
#endif
/* Optional experimental native HDF5 comparison frontend. The core C reader
 * does not depend on HDF5. Layout 0: identical BLK1 bytes; 1: typed contiguous;
 * 2: typed chunks with shuffle/deflate level 1. All verify decoded block CRCs.
 * info.file_bytes retains the source OptBin logical byte count, not H5 size. */
int optb_h5_load(const char *,optb_model *,uint64_t,char *,size_t);
int optb_h5_export(const char *,const char *,unsigned,char *,size_t);
const char *optb_h5_version(void);
#ifdef __cplusplus
}
#endif
#endif
