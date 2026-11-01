# OptBin version 2: development byte specification

This specification is implemented but experimental. Integers use little-endian
encoding; FP32/FP64 use IEEE representations. The current implementation requires
a little-endian IEEE host. No native C struct is written to disk. Dimensions and
array counts are below INT64_MAX; section counts are at most 4096.

## Header (128 bytes)

| Offset | Bytes | Meaning |
|---:|---:|---|
| 0 | 8 | ASCII `OPTBIN2` followed by LF |
| 8 | 4 | version = 2 |
| 12 | 4 | header bytes = 128 |
| 16 | 8 | directory entry count |
| 24 | 8 | total block count |
| 32 | 8 | exact file size |
| 40 | 8 | directory offset = 128 |
| 48 | 8 | directory bytes = 64 times entry count |
| 56 | 4 | profile: 1 quadratic/bounded, 2 conic |
| 60 | 4 | signed sense: +1 min, -1 max |
| 64 | 8 | scalar variable count n |
| 72 | 8 | scalar affine row count m |
| 80 | 8 | finite FP64 objective constant |
| 88 | 8 | maximum decoded elements/block; 1..1,048,576 |
| 96 | 28 | reserved, all zero; unknown feature bits are rejected |
| 124 | 4 | CRC32 over bytes 0..123, initial CRC 0 |

## Directory (64 bytes per section)

Entries are strictly increasing by section ID. They describe disjoint contiguous
byte ranges covering everything after the directory, in directory order.

| Offset | Bytes | Meaning |
|---:|---:|---|
| 0 | 4 | section ID |
| 4 | 4 | decoded type: U32=1, U64=2, F64=3, U8=4 |
| 8 | 8 | decoded element count |
| 16 | 8 | first block's absolute file offset |
| 24 | 8 | total bytes of section block records, including headers/padding |
| 32 | 8 | block count |
| 40 | 8 | flags: 1 required, 0 optional |
| 48 | 12 | reserved, zero |
| 60 | 4 | CRC32 over bytes 0..59 |

Empty sections have zero records and bytes; their offset is the next byte after
the preceding section. Unknown required sections are rejected by model
validation. Optional unknown sections remain available to callers, with normal
framing and type validation. Known sections cannot be silently omitted when
their profile requires them.

## Blocks and codecs

Each section contains BQP-compatible `BLK1` 64-byte block records followed by
payload and zero padding to an 8-byte boundary. The array-ID field is the v2
section ID. Starts cover the decoded array in order, beginning at zero. Block
count, decoded coverage, section bytes and final file size must agree exactly.

The record fields and value codecs are the same as in
[the BQP v1 block specification](bqp-v1.md#block-record). FP64 sections support
RAW, CONST, exact INT8/INT16/INT32, exact FP32, and packed FP64 dictionaries.
Unsigned integer sections support RAW or delta/zigzag varints; U8 supports RAW
only. Zstd is optional after encoding. Checksums cover decoded little-endian
bytes, not compressed bytes. They detect corruption; they are not signatures.

The current writer chooses the smallest representation before optional Zstd,
then retains Zstd only when it shrinks that representation. It does not search
all codec-plus-Zstd combinations for the global minimum.

## Common sections

IDs 1..11 retain the BQP array IDs:

| ID | Data | Type/count |
|---:|---|---|
| 1 | A column pointers | U64, n+1 |
| 2 | A row indices | U32/U64, nnz(A) |
| 3 | A coefficients | F64, nnz(A) |
| 4 | objective c | F64, n |
| 5,6 | row lower/upper bounds | F64, m; profile 1 only |
| 7,8 | variable lower/upper bounds | F64, n; profile 1 only |
| 9 | objective Q column pointers | U64, n+1 if Q is nonempty, otherwise zero |
| 10 | Q row indices | U32/U64, nnz(Q) |
| 11 | Q coefficients | F64, nnz(Q) |

A is CSC with strictly increasing row IDs in each column. Q is symmetric with
only its upper triangle stored. Empty Q can omit all three sections or declare
all three empty. The scalar-variable objective is `c0+c'x+0.5*x'Q*x`.
Bounds may contain infinities but not NaNs; all other F64 values are finite.
Contradictory bounds represent infeasible data and are preserved.

Section 48 is optional source/provenance text as UTF-8 bytes. It is descriptive;
the numerical metadata, rather than this text, determines model interpretation.
Input names and comments are not preserved in this prototype.

## Profile 1: LP/QP/QCQP

The affine A rows and variable bounds are mandatory. QC sections 12..17 form an
optional group; either the whole group is absent or all members are present.

| ID | Data | Type/count |
|---:|---|---|
| 12 | affine row associated with each quadratic constraint | U64, k |
| 13 | offsets into the quadratic coordinate arrays | U64, k+1 |
| 14,15 | variable row/column IDs | U32/U64, qcnnz each |
| 16 | matrix coefficients | F64, qcnnz |
| 17 | quadratic expression multipliers | F64, k; each 0.5 or 1 |

Constraint k augments row `qc_row[k]` with `qc_scale[k]*x'Q[k]*x`.
Rows 12 are strictly increasing. Pointers begin at zero and end at qcnnz.
Each Q[k] is upper triangular and sorted by (column,row), without duplicates.
Matrix diagonals contribute once; off-diagonals contribute twice inside x'Qx.
The reader does not assume convexity. See [MPS semantics](mps.md).

## Profile 2: native affine conic model

Section 18 is b, F64 length m, with affine-domain expression `g=A*x+b` when PSD
variables are absent. Sections 19..22 describe scalar variable domains, and
23..26 describe scalar affine expression domains. In each four-section group:

1. U32 cone kinds, length k.
2. U64 coordinate dimensions, length k, summing to n or m.
3. U64 parameter offsets, length k+1, covering the next section.
4. F64 parameters, unnormalized positive weights for power cones.

Kinds 0..9 are FREE, NONNEG, NONPOS, ZERO, SOC, RSOC, EXP, DUAL_EXP, POWER,
DUAL_POWER. Dimensions are positive; RSOC requires at least 2; EXP/DUAL_EXP
require exactly 3. A power vector has 1..dimension positive finite weights.
Non-power cones have no parameters. CBF v3 coordinate order and cone semantics
are retained. The storage reader does not normalize parameters or permute rows.

Section 27 lists U64 matrix orders for PSD variables; 28 lists PSD constraint
orders. Each order is 1..UINT32_MAX. Scalar variable count n excludes PSD
variables, and scalar affine row count m excludes PSD constraints.

The following sparse-coordinate groups hold native symmetric coefficients:

| IDs | Coordinate key, then F64 value |
|---|---|
| 29..32 | PSD variable block, matrix row, matrix column, objective coefficient |
| 33..37 | scalar affine row, PSD variable block, matrix row, matrix column, F coefficient |
| 38..42 | PSD constraint block, scalar variable, matrix row, matrix column, H coefficient |
| 43..46 | PSD constraint block, matrix row, matrix column, constant D coefficient |

All coordinate IDs are U64. Within a group all arrays have the same length.
Matrix row <= column. Entries are strictly sorted by prefix coordinates, then
matrix column, then matrix row. Missing groups mean zero operators. Coefficients
are unscaled symmetric-matrix entries. The objective and scalar affine rows use
trace inner products with PSD variables, so off-diagonal products count twice.
PSD constraints represent `sum_j x[j]*H[block,j] + D[block]` being PSD.

Section 47 (integer IDs) is reserved and rejected in this continuous release,
including when empty. CBF CHANGE sequences and v4 proposal cones are not encoded.

## Loading and compatibility

`optb_load` verifies checksums and full model invariants, preserving exact
decoded FP64 bits, including signed zero. Text conversion may first perform
defined canonicalization such as summing duplicate MPS entries. Losslessness
refers to the resulting binary64 numerical model, not original decimal tokens,
names, comment lines or byte ordering of text records.

The current native implementation decodes on the CPU. Solver adapters own
reformulations, objective-sense normalization and PSD scaling. The new loader
accepts v1 BQP through the retained v1 implementation. V1 readers reject v2;
no existing BQP file/header is modified.

The optional `python/optbin/hdf5.py` frontend carries the same BLK1 section bytes
inside HDF5 datasets. `optb_decode_section` exposes the native section decoder
for such containers; it must be followed by `optb_validate` for full model
invariants. This frontend does not change the `.optb` wire format.
