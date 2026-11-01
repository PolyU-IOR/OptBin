#include "bqp.h"
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>

/* GPU-first application example with an explicit CPU fallback. */
int main(int argc, char **argv) {
    if (argc < 2 || argc > 3) {
        fprintf(stderr, "usage: load_auto MODEL.bqp [GPU_DEVICE]\n");
        return 2;
    }
    int device = argc == 3 ? atoi(argv[2]) : 0;
    bqp_model model;
    char error[512];
    int gpu = bqp_load_cuda(argv[1], &model, device, UINT64_C(65) << 30,
                            error, sizeof error) == 0;
    if (!gpu) {
        fprintf(stderr, "GPU load unavailable: %s\nLoading on CPU.\n", error);
        if (bqp_load(argv[1], &model, UINT64_C(128) << 30, error, sizeof error)) {
            fprintf(stderr, "CPU load failed: %s\n", error);
            return 1;
        }
    }
    printf("Loaded %llu rows, %llu variables, %llu entries on %s.\n",
           (unsigned long long)model.info.rows,
           (unsigned long long)model.info.cols,
           (unsigned long long)model.info.nnz,
           gpu ? "GPU" : "CPU");
    /* model.array[] contains device pointers on the GPU path and host pointers
       on the CPU path. Pass them to an appropriate solver adapter here. */
    if (gpu) bqp_free_cuda(&model); else bqp_free(&model);
    return 0;
}
