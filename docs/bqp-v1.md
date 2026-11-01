# BQP version 1 byte specification

All integers are unsigned little-endian unless specified otherwise. Binary64 and
binary32 use IEEE encodings. The current implementation requires a little-endian
host with those representations. An independent reader can byte-swap on other
hosts. No C struct is written directly to disk.

## Model header: 128 bytes

| Offset | Bytes | Meaning |
|---:|---:|---|
| 0 | 8 | magic `42 51 50 31 0d 0a 1a 0a` |
| 8 | 4 | version = 1 |
| 12 | 4 | header length = 128 |
| 16 | 8 | number of constraint rows m |
| 24 | 8 | number of variables n |
| 32 | 8 | stored A entry count |
| 40 | 8 | stored upper-triangular Q entry count |
| 48 | 8 | number of block records |
| 56 | 8 | exact file size |
| 64 | 4 | signed sense: +1 minimize, -1 maximize |
| 68 | 4 | A row-index width: 4 or 8 |
| 72 | 4 | Q row-index width: 4 or 8 |
| 76 | 4 | layout flags = 1: CSC A, symmetric-upper CSC Q |
| 80 | 8 | binary64 objective offset |
| 88 | 8 | maximum elements per block, 1..1,048,576 |
| 96 | 28 | reserved; zero |
| 124 | 4 | zlib/IEEE CRC32 of bytes 0..123, initial CRC 0 |

All model dimensions and entry counts must be at most INT64_MAX. Counts are
64-bit regardless of index width; more than 2^32 nonzeros do not force 64-bit row
indices. A 32-bit row index is permitted when the relevant row dimension is at
most UINT32_MAX. Indices are zero-based.

## Array IDs and decoded types

| ID | Array | Length | Decoded type |
|---:|---|---:|---|
| 1 | A column pointers | n+1 | uint64 |
| 2 | A row indices | nnz(A) | uint32/uint64 |
| 3 | A values | nnz(A) | binary64 |
| 4 | c | n | binary64 |
| 5 | row lower bounds | m | binary64 |
| 6 | row upper bounds | m | binary64 |
| 7 | variable lower bounds | n | binary64 |
| 8 | variable upper bounds | n | binary64 |
| 9 | Q column pointers | n+1 if qnnz>0, else 0 | uint64 |
| 10 | Q row indices | qnnz | uint32/uint64 |
| 11 | Q values | qnnz | binary64 |

Blocks of different arrays may be interleaved. Within an array they must start at
zero and cover its entire length in order, without gaps/overlaps. Empty arrays
have no blocks. Pointers start at zero, never decrease, and end at the matrix
entry count. Within a column row indices are strictly increasing. Q rows are at
most the column index. Explicit zero entries are permitted. Bound contradictions
are preserved as infeasible model data.

## Block record

Each record has a 64-byte header, followed by its stored payload, followed by
0..7 zero bytes to align the next record to 8 bytes. No file trailer is used.

| Offset | Bytes | Meaning |
|---:|---:|---|
| 0 | 4 | ASCII `BLK1` |
| 4 | 4 | array ID |
| 8 | 8 | starting element offset within that array |
| 16 | 8 | decoded element count; positive and <= block limit |
| 24 | 4 | value encoding ID |
| 28 | 4 | compression: 0 none, 1 one standard Zstd frame |
| 32 | 8 | packed payload size before Zstd |
| 40 | 8 | stored payload size after optional Zstd |
| 48 | 4 | CRC32 of the decoded little-endian array bytes, initial CRC 0 |
| 52 | 4 | dictionary count; zero unless encoding 6 |
| 56 | 4 | bits per dictionary ID; zero unless encoding 6 |
| 60 | 4 | CRC32 of header bytes 0..59, initial CRC 0 |

Packed and stored sizes must each be in 1..16 MiB. Decompression must produce
exactly the declared packed size. Decoding must produce exactly count*width bytes
and match the decoded CRC32. A complete reader must verify total file size,
record count, array coverage, and metadata/index bounds.

## Value encodings

0. **RAW:** count elements in their decoded type.
1. **CONST:** one binary64 bit pattern, repeated count times.
2. **INT8:** count signed two's-complement int8 values, converted exactly to binary64.
3. **INT16:** count signed little-endian int16 values, converted to binary64.
4. **INT32:** count signed little-endian int32 values, converted to binary64.
5. **FP32:** count binary32 values, converted to binary64.
6. **DICT:** dictionary_count binary64 bit patterns, followed by packed indices.
   The dictionary count is 2..4096; bits is 1..12 and 2^bits >= dictionary_count.
   Index i starts at bit i*bits, least-significant bit first within both the code
   and each byte. The bitstream occupies ceil(count*bits/8) bytes. IDs must be
   less than dictionary_count; unused high bits in the writer are zero.
7. **DELTA:** integer arrays only. First value is an absolute uint64, then
   count-1 differences. For signed difference d, zigzag is 2*d for d>=0 and
   -2*d-1 for d<0. Encode zigzag using unsigned base-128 varints (low 7 bits
   first; bit 7 signals continuation). Decoded values must fit the target width,
   cannot underflow, and the packed payload must be consumed exactly.

Encodings 1..6 are only for binary64 arrays. Encodings 0 and 7 are available for
integer arrays. Writers must preserve exact binary64 bit patterns when selecting
numeric encodings; negative zero therefore cannot use an integer encoding.
NaNs are forbidden; infinities are permitted only in bound arrays.

The mathematical objective convention is always c0+c'x+0.5*x'Q*x, using the full
symmetric matrix represented by the stored upper triangle. For an off-diagonal
entry q_ij, the resulting polynomial coefficient is q_ij; for diagonal q_ii it
is q_ii/2. The sense field controls min versus max without negating coefficients.
