#ifndef OPTBIN_H
#define OPTBIN_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define OPTBIN_VERSION "0.2.0rc4"
enum { OPTB_QUADRATIC=1, OPTB_CONIC=2 };
enum { OPTB_U32=1, OPTB_U64=2, OPTB_F64=3, OPTB_U8=4 };
enum {
    OPTB_AP=1, OPTB_AI, OPTB_AX, OPTB_C, OPTB_RL, OPTB_RU,
    OPTB_XL, OPTB_XU, OPTB_QP, OPTB_QI, OPTB_QX,
    OPTB_QC_ROW, OPTB_QC_PTR, OPTB_QC_I, OPTB_QC_J, OPTB_QC_X, OPTB_QC_SCALE,
    OPTB_B,
    OPTB_VAR_KIND, OPTB_VAR_DIM, OPTB_VAR_PARAM_PTR, OPTB_VAR_PARAM,
    OPTB_CON_KIND, OPTB_CON_DIM, OPTB_CON_PARAM_PTR, OPTB_CON_PARAM,
    OPTB_PSD_VAR_DIM, OPTB_PSD_CON_DIM,
    OPTB_OF_BLOCK, OPTB_OF_I, OPTB_OF_J, OPTB_OF_X,
    OPTB_F_ROW, OPTB_F_BLOCK, OPTB_F_I, OPTB_F_J, OPTB_F_X,
    OPTB_H_BLOCK, OPTB_H_VAR, OPTB_H_I, OPTB_H_J, OPTB_H_X,
    OPTB_D_BLOCK, OPTB_D_I, OPTB_D_J, OPTB_D_X,
    OPTB_INTEGER_IDS, OPTB_SOURCE
};
/* EXP/POWER coordinates follow CBF v3, not a particular solver's ordering.
 * Matrix-valued sections store unscaled symmetric upper-triangle entries.
 * The trace inner product counts off-diagonal entries twice. */
enum { OPTB_FREE=0, OPTB_NONNEG=1, OPTB_NONPOS=2, OPTB_ZERO=3,
       OPTB_SOC=4, OPTB_RSOC=5, OPTB_EXP=6, OPTB_DUAL_EXP=7,
       OPTB_POWER=8, OPTB_DUAL_POWER=9 };
typedef struct {
    uint64_t sections, blocks, file_bytes, cols, rows, block_elements;
    uint32_t profile;
    int32_t sense; /* +1 min, -1 max */
    double objective_offset;
} optb_info;
typedef struct {
    uint32_t id, type;
    uint64_t count, offset, stored_bytes, blocks, flags;
    void *data;
} optb_section;
typedef struct {
    optb_info info;
    optb_section *section;
} optb_model;
typedef int (*optb_block_fn)(void *, const optb_info *, const optb_section *,
                           uint64_t start, uint64_t count, const void *,
                           char *error, size_t error_size);

unsigned optb_type_bytes(unsigned type);
const char *optb_section_name(unsigned id);
const optb_section *optb_get(const optb_model *, unsigned id);
int optb_read_info(const char *, optb_info *, char *, size_t);
/* Streaming visits verify checksums, framing, types, and array coverage.
 * Callback data is borrowed until return. Cross-array model invariants are
 * additionally checked by optb_load/optb_validate. No GPU decoding is implied. */
int optb_visit(const char *, optb_info *, optb_block_fn, void *, char *, size_t);
/* Reads v2 .optb and legacy v1 .bqp. limit bounds decoded arrays, not total RSS.
 * Output must not already own allocations; free an earlier model before reuse.
 * On failure the output is empty. Free successful output with optb_free. */
int optb_load(const char *, optb_model *, uint64_t limit, char *, size_t);
int optb_validate(const optb_model *, char *, size_t);
/* Reuse the exact v2 section codecs in another container (e.g. HDF5).
 * This checks one encoded section, not cross-array model semantics. Caller
 * supplies aligned output plus reusable scratch; then call optb_validate. */
#define OPTBIN_SCRATCH_BYTES (16u*1024u*1024u)
int optb_decode_section(const optb_section *, const void *encoded, size_t encoded_bytes,
                        void *decoded, size_t decoded_bytes, uint64_t block_limit,
                        void *scratch, size_t scratch_bytes, char *, size_t);
void optb_free(optb_model *);
#ifdef __cplusplus
}
#endif
#endif
