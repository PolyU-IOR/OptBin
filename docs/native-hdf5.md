# Native C HDF5 comparison frontend

This optional frontend compares OptBin and HDF5 using native C paths. It is an
experimental evaluation interface, separate from the earlier JSON-metadata
Python frontend in `python/optbin/hdf5.py`. Files from the two HDF5 experiments
are distinct profiles; neither is an arbitrary HDF5 optimization-model reader.
The OptBin core and normal Make/CMake installation still require no HDF5.

## Build

Use an existing HDF5 C development installation, or build the pinned dependency
without administrator access:

```bash
.venv/bin/python scripts/build_hdf5.py --jobs 8
cmake -S . -B build-native -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON -DOPTBIN_WITH_HDF5=ON \
  -DHDF5_ROOT="$PWD/data/deps/hdf5-install"
cmake --build build-native -j4
```

The helper downloads official HDF5 1.14.6 source and verifies the release SHA256
`e4defbac30f50d64e1556374aa49e574417c9e72c6b1de7a4ff88c4b1bea6e9b`.
Source, logs, configuration and dependency provenance remain under ignored
`data/deps/`. The optional benchmark uses an OpenSSL `libcrypto` runtime for
SHA256 after timing; its development headers are not required.

## Export and load

```bash
build-native/optb_h5_export data/converted/nql30.optb /tmp/nql30-encoded.h5 encoded
build-native/optb_h5_export data/converted/nql30.optb /tmp/nql30-raw.h5 raw
build-native/optb_h5_export data/converted/nql30.optb /tmp/nql30-gzip.h5 gzip
build-native/optb_native_bench hdf5 /tmp/nql30-encoded.h5
build-native/optb_native_bench hdf5 /tmp/nql30-raw.h5 optbin data/converted/nql30.optb
```

Exports use a temporary file and exclusive publication. Encoded exports retain
identical BLK1 bytes. Typed exports carry the same arrays and original block
checksums. Export time includes stream framing/checksum validation and writing
the new representation; it is not MPS/CBF source parsing time.

`include/optbin_hdf5.h` declares `optb_h5_load`, `optb_h5_export` and a dependency
version query. The loader returns `optb_model` with the same ownership rules and
decoded-byte budget as `optb_load`; use `optb_free`. `info.file_bytes` retains the
original OptBin logical size. Use filesystem size for physical HDF5 bytes. This
optional frontend is built separately and is not included in the normal
installed CMake target export.

## Experimental HDF5 profile

The writer emits scalar `optbin_hdf5_version=1` and `layout` attributes, and a
`manifest` byte dataset containing the original 128-byte header followed by its
directory. The shared manifest parser verifies header/directory CRCs and coverage
using the original logical file size. Directory offsets are source OptBin
offsets, not HDF5 object offsets.

| Layout | `data/<section-ID>` | Additional checks |
|---|---|---|
| 0, `encoded` | One-dimensional bytes: identical BLK1 headers, payloads and padding | Original per-block checksums |
| 1, `raw` | Contiguous one-dimensional typed decoded arrays | `checks/<ID>` with `(start,count,CRC32)` U64 triples |
| 2, `gzip` | Same arrays, chunked with shuffle and deflate level 1 | Same U64 check triples |

Typed chunks have at most 65,536 elements. Empty sections have no blocks.
Encoded reading uses a 1 MiB cache and bounded codec buffers. Typed loading
reads into final arrays and checks original block partitions. Both paths run
full model validation. The output-array budget is not a cap on process RSS,
HDF5 metadata/cache allocations, or temporary checksum lists.

## Measurements and correctness

```bash
.venv/bin/python -m unittest discover -s tests -p test_native_hdf5.py -v
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/benchmark_native.py --repeats 7 --reference
```

Tests include QCQP/PSD round trips, independently written RAW blocks larger
than the cache, signed zero, infinite bounds, budgets, corrupted payload/metadata,
exclusive output publication and lossless legacy repacking. They pass under UBSan.

Each timed trial is a fresh C process. Timing and `getrusage` RSS stop after
complete loading/validation, before numerical SHA256 and optional explicit
`memcmp`. GNU time is a small launcher for the smaller-model harness to avoid
an inherited Python RSS high-water floor. Trial values and launch details are
retained. Files are warm; no controlled cold-cache, solver setup or GPU upload
is measured.

Both encoded paths share `optb_manifest_decode`, `optb_allocate`,
`optb_walk_encoded`, numeric codecs, decoded CRC checks and `optb_validate`.
OptBin loading now reads its manifest and blocks through one open file. The v2
schema and decoded outputs are unchanged. Different I/O libraries and buffering
policies still contribute to observed timings; these are comparisons of the
implemented paths, not intrinsic lower bounds for HDF5.

## Numerical fingerprints

The benchmark's SHA256 fingerprints the decoded numerical model, not the
compressed file. On the supported little-endian host it hashes, in order:

1. Profile U32, sense I32, rows U64, columns U64 and objective offset F64.
2. Each nonempty section with ID 1 through 47, in order: ID U32, type U32,
   count U64, then decoded array bytes.

Provenance section 48 and empty/absent optional sections are excluded. Full
checksums and model validation precede fingerprinting. `--reference` additionally
compares dimensions, sense, objective bits, types/counts and numerical array
bytes explicitly. Fingerprints and executed byte comparisons are distinct JSON
records; fingerprints alone are not described as an executed `memcmp`.

Process RSS excludes filesystem page cache. Fresh-process runs include first-use
initialization, so small-instance medians do not estimate batched loading after
runtime initialization. HDF5 1.14.6 is one pinned version/configuration, not an
evaluation of every HDF5 release or filter.

## Large-model migration

```bash
build/bqp2optb /path/model.bqp /path/model.optb
python3 scripts/benchmark_native.py --input model=/path/model.bqp \
  --repeats 3 --reference --output benchmarks/native-large.json
```

`bqp2optb` indexes block locations, regroups them by section and retains encoded
records unchanged. Decoded CRC/framing verification precedes exclusive publication.
It does not allocate the full model or reparse MPS; cross-array invariants are
checked when loading. Original files remain unchanged. Historical source-MPS/
HPR-LP comparisons use a different timing boundary and are reported separately.
