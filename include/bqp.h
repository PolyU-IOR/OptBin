#ifndef BQP_H
#define BQP_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

/* BQP v1: continuous LP/QP, CSC A and upper-triangular CSC Q.
 * objective = c0 + c'x + 0.5*x'Q*x; sense +1=min, -1=max.
 * Q's missing lower triangle is its symmetric reflection. */
enum { BQP_AP=1, BQP_AI, BQP_AX, BQP_C, BQP_RL, BQP_RU,
       BQP_XL, BQP_XU, BQP_QP, BQP_QI, BQP_QX, BQP_ARRAYS=11 };
typedef struct {
    uint64_t rows, cols, nnz, qnnz, blocks, file_bytes, block_elements;
    int32_t sense;
    uint32_t a_index_bytes, q_index_bytes;
    double objective_offset;
} bqp_info;
typedef struct {
    bqp_info info;
    int device; /* -1 for CPU, CUDA device ordinal otherwise. */
    /* AP/QP: uint64_t; AI/QI: uint32_t or uint64_t; others: double.
     * Array zero is unused. The CPU reader owns these allocations. */
    void *array[BQP_ARRAYS+1];
} bqp_model;
typedef int (*bqp_block_fn)(void *context, const bqp_info *info,
                           unsigned array, uint64_t start, uint64_t count,
                           const void *data, char *error, size_t error_size);

uint64_t bqp_array_count(const bqp_info *, unsigned array);
unsigned bqp_element_bytes(const bqp_info *, unsigned array);
const char *bqp_array_name(unsigned array);
int bqp_memory_bytes(const bqp_info *, uint64_t *bytes);
int bqp_read_info(const char *path, bqp_info *, char *error, size_t error_size);
/* Decoded data belongs to the reader and is valid only during the callback.
 * Blocks are in order within each array, but arrays can be interleaved.
 * All header/payload checksums and structural bounds are checked. */
int bqp_visit(const char *path, bqp_info *, bqp_block_fn, void *context,
              char *error, size_t error_size);
/* limit=0 means no caller-imposed limit. Failure frees partial allocations. */
int bqp_load(const char *path, bqp_model *, uint64_t limit,
             char *error, size_t error_size);
void bqp_free(bqp_model *);

/* Optional CUDA implementation; link src/bqp_cuda.c and -ldl.
 * Uses runtime-loaded libcudart; CPU decoding and bounded host->device copies.
 * It does NOT claim GPUDirect I/O or compressed execution on the GPU.
 * The returned array pointers are device pointers. */
int bqp_load_cuda(const char *path, bqp_model *, int device, uint64_t limit,
                  char *error, size_t error_size);
int bqp_cuda_checksums(const bqp_model *, uint32_t crc[BQP_ARRAYS+1],
                       char *error, size_t error_size);
void bqp_free_cuda(bqp_model *);
#ifdef __cplusplus
}
#endif
#endif
