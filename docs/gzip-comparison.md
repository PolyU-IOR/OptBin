# Practical compressed-file comparison

The technical note compares the representations a user may store or transfer:

| Representation | Contents | Loading path |
|---|---|---|
| MPS.gz / CBF.gz | Source text, gzip compressed | Public Gurobi MPS reader / MOSEK CBF reader into their solver models |
| raw.h5.gz | Ordinary HDF5 typed arrays, no value/index encoding or dataset compression; external gzip | Decompress to a seekable temporary, then load the arrays |
| OptBin | Default lossless block encodings and optional per-block Zstandard | Native C reader |
| OptBin.gz | External gzip around that same default OptBin file | Decompress to a seekable temporary, then call the native C reader |

`optb_load` does not itself accept an outer gzip wrapper. An `.optb.gz` file is a
transport choice; expand it before passing the `.optb` path to the API. Its
internal blocks may already be Zstandard compressed. The benchmark's temporary
wrapper is not a new production gzip-reader API.

Raw HDF5 retains model metadata, native array types and per-block CRCs. This
definition includes the same numerical information and checksum validation as
OptBin. It is not an HDF5 file containing OptBin-encoded blocks. No shuffle,
dataset deflate or other HDF5 variants appear in the current note's comparison.

## Scope and evidence

[The Gurobi workstation study](../benchmarks/gurobi-small-20261030.json) measures
all 13 real MPS LP/QP/QCQP models, with complete independent polynomial checks.
The [MOSEK study](../benchmarks/mosek-cbf-20261030.json) measures all nine original
CBLIB conic cases, with complete independent function-in-set checks. All three
binary representations reconstruct the same arrays. Both vendor studies have
fresh matched binary trials; binary timings are not mixed between experiments.

The large-server comparison uses public `gurobipy.read` on the two original
Oliver Hinder MPS.gz files, alongside the existing raw.h5.gz, OptBin and
OptBin.gz representations. Each format has a full numerical verification and
three shuffled fresh-process trials. The source OptBin files were previously
migrated from BQP; these are new source-reader measurements, not new MPS-to-binary
conversion measurements. [Gurobi methodology](gurobi-reference.md) explains
public API endpoints, complete LP verification, and large nonzero-count handling.

Gurobi and MOSEK return their solver models, including metadata; the binary
readers return validated numerical arrays. Environment startup and reference
extraction are outside the vendor read timers. [MOSEK methodology](mosek-reference.md)
describes exact conic normalization and the reference helper's explicit limits.
Independent original-CBLIB and JuMP/MOI correctness evidence remains separate.
Earlier timings of OptBin's own MPS/CBF parsers are archived in the original gzip
study and are not relabeled as vendor timings.

## All 22 workstation cases

Each numeric cell gives stored KiB followed by complete loading milliseconds.
Source names identify the exact manifest entries. CVXQP1_S, CVXQP2_S, CVXQP3_S,
DUAL1 and DUAL2 are local presolved variants, as recorded in
`benchmarks/real-cases.json`; the QPLIB QCQP MPS files are Gurobi exports of the
public LP-format sources. They are not asserted identical to another published
version with the same short name.

<!-- BEGIN RESULTS -->
| Model | Source reader | Text.gz KiB / ms | raw.h5.gz KiB / ms | OptBin KiB / ms | OptBin.gz KiB / ms |
|---|---|---:|---:|---:|---:|
| afiro | Gurobi MPS | 0.60 / 2.242 | 1.68 / 1.849 | 1.98 / 0.209 | 0.91 / 0.338 |
| AUG2D | Gurobi MPS | 401.58 / 15.887 | 130.64 / 5.047 | 2.13 / 2.202 | 0.87 / 2.325 |
| AUG3DCQP | Gurobi MPS | 62.93 / 5.073 | 29.67 / 2.966 | 2.13 / 0.583 | 0.91 / 0.692 |
| QPLIB_2482 | Gurobi MPS | 56.29 / 5.153 | 21.19 / 3.002 | 3.96 / 0.567 | 2.36 / 0.666 |
| CVXQP1_S | Gurobi MPS | 2.60 / 2.435 | 2.76 / 1.972 | 2.79 / 0.254 | 1.63 / 0.383 |
| CVXQP2_S | Gurobi MPS | 1.84 / 2.301 | 2.33 / 2.004 | 2.58 / 0.228 | 1.37 / 0.373 |
| CVXQP3_S | Gurobi MPS | 3.33 / 2.400 | 3.18 / 1.986 | 3.00 / 0.250 | 1.86 / 0.381 |
| DUAL1 | Gurobi MPS | 14.24 / 2.882 | 7.82 / 2.116 | 5.30 / 0.324 | 4.17 / 0.470 |
| DUAL2 | Gurobi MPS | 17.65 / 3.066 | 9.25 / 2.149 | 6.15 / 0.325 | 5.01 / 0.475 |
| qssp30 | MOSEK CBF | 161.84 / 11.343 | 60.20 / 3.742 | 35.21 / 1.419 | 32.29 / 1.672 |
| nql30 | MOSEK CBF | 71.11 / 4.888 | 38.32 / 3.060 | 21.54 / 0.837 | 18.83 / 1.051 |
| nb | MOSEK CBF | 840.73 / 25.369 | 240.51 / 6.665 | 243.23 / 3.494 | 233.52 / 4.740 |
| nb_L1 | MOSEK CBF | 853.73 / 24.902 | 245.79 / 8.817 | 238.73 / 3.712 | 235.60 / 3.621 |
| nb_L2 | MOSEK CBF | 2194.08 / 54.388 | 760.63 / 12.660 | 613.99 / 6.888 | 607.30 / 9.358 |
| LogExpCR-n20-m400 | MOSEK CBF | 109.96 / 4.530 | 72.67 / 2.330 | 66.16 / 0.601 | 63.78 / 1.060 |
| infeas_clean_10_10_1 | MOSEK CBF | 1.30 / 0.956 | 2.99 / 1.844 | 4.09 / 0.264 | 1.39 / 0.425 |
| infeas_messy_10_10_1 | MOSEK CBF | 2.37 / 1.186 | 3.92 / 1.910 | 4.59 / 0.314 | 1.92 / 0.432 |
| sched_50_50_orig | MOSEK CBF | 216.63 / 5.699 | 152.49 / 3.569 | 146.41 / 1.194 | 144.06 / 1.896 |
| QPLIB_2456 | Gurobi MPS | 280.22 / 13.374 | 61.70 / 5.105 | 15.38 / 1.652 | 13.58 / 1.927 |
| QPLIB_2519 | Gurobi MPS | 146.50 / 8.249 | 52.89 / 4.331 | 4.65 / 1.124 | 2.97 / 1.288 |
| QPLIB_2676 | Gurobi MPS | 71.59 / 5.852 | 17.86 / 3.020 | 7.59 / 0.614 | 5.94 / 0.807 |
| QPLIB_3185 | Gurobi MPS | 444.57 / 18.278 | 159.91 / 7.270 | 5.11 / 2.846 | 3.13 / 2.943 |

Default OptBin is larger than Text.gz on 5/22 models; OptBin.gz is larger on 2/22. The table retains these cases. Times are medians of seven trials, including decompression.
<!-- END RESULTS -->

## Two large Oliver Hinder LPs

<!-- BEGIN LARGE RESULTS -->
| Model | Rows | Variables | A nonzeros |
|---|---:|---:|---:|
| prod_200_400_02 | 16,361,200 | 64,560,800 | 2,310,559,600 |
| mcf_5000_100_1000 | 5,525,100 | 502,500,100 | 1,007,500,100 |

| Representation | prod GiB / seconds | mcf GiB / seconds |
|---|---:|---:|
| MPS.gz (Gurobi) | 7.294 / 342.27 | 10.553 / 386.89 |
| raw.h5 (uncompressed) | 27.992 / -- | 26.319 / -- |
| raw.h5.gz | 0.533 / 101.39 | 4.301 / 117.72 |
| OptBin | 0.441 / 53.13 | 4.460 / 53.59 |
| OptBin.gz | 0.421 / 55.41 | 4.314 / 73.37 |

Times are medians of three shuffled fresh-process trials, including gzip decompression. Every large source LP passes complete independent Gurobi-to-OptBin numerical comparison; binary paths pass decoded-array byte comparisons. Default OptBin loads 6.44× and 7.22× as fast as Gurobi on prod and mcf, respectively. Gurobi returns its solver model with metadata; the binary readers return validated arrays. Raw .h5 has storage measurements only.

Before gzip, raw HDF5 occupies 27.99 and 26.32 GiB (prod/mcf), 63.4 and 5.9 times default OptBin. The tested raw.h5.gz loader needs a temporary file of that expanded size; default OptBin reads its internally compressed file directly. These savings concern stored files and temporary disk space; decoded RAM is essentially unchanged. HDF5 also supports internal dataset compression, which this raw-array baseline does not use.

Gurobi read-phase peak RSS is 57.67 / 70.41 GiB (prod / mcf). OptBin is 28.00 / 26.32 GiB; the other binary paths are within 0.01 GiB. Extraction, reference loading and later solver allocations are outside these snapshots. The representations and metadata differ, so this is not an isolated container-memory comparison.
<!-- END LARGE RESULTS -->

## Timing and gzip policy

Each timed loader starts before opening its stored input. Gurobi timing ends
when public `gurobipy.read` or MOSEK `Task.readdata` returns its solver model.
Binary loading ends with numeric arrays that pass the common `optb_validate`.
Vendor timing includes decompression, parsing and internal model construction. Binary
timing includes decompression, allocation, decoded CRCs and full validation. Numeric hashing, reference loading and byte
comparison happen after the timer and RSS snapshot. No solving, GPU upload or
serialized model output is timed.

For externally gzipped binaries, the timer also includes writing and closing
the expanded seekable temporary, opening/loading it and deleting it. Scratch
is beside each benchmark case, on the same filesystem. Both binary gzip paths
use the same implementation. This is a practical temporary-file path, not a
claim about the fastest possible in-memory HDF5 implementation. The source
readers stream their gzip inputs and need no expanded text temporary.

Generated gzip files use level 6 and omit source filenames/timestamps (`-n`).
The workstation and prod studies use GNU gzip; the mcf study uses
[pigz 2.8](https://zlib.net/pigz/) with 16 compression threads. That parallelism
is only for preparation. Native decoding is serial; Gurobi `Threads` and MOSEK
`num_threads` parameters are set to 1. Existing source `.gz` files remain
verbatim; their producer compression settings are unknown. Gzip creation times
include writing/closing the output without an `fsync` and are recorded under
`preparation`, separately from repeated loading. Source conversion and raw HDF5
export are also preparation. Default OptBin combines specialized encodings with
Zstandard, so this comparison does not isolate the effect of the compressor.

There is one untimed verification/warmup per measured format. Subsequent
trials use fresh processes in shuffled rounds, seven per workstation case and
three per server format. No global cache flush is used. Results describe
warm files on these hosts, not controlled cold-disk or network transfers.
The two large case jobs run concurrently on the shared server, while formats
within each case run sequentially. The combined report retains both source
reports and their hashes.
Peak RSS excludes filesystem page cache, expanded temporary-file space and
later solver allocations. Temporary disk needs equal the uncompressed binary
file size; OptBin's decoded arrays remain expanded in memory.
The note interprets only large-case RSS. Small subprocess peaks can retain the
parent's pre-exec memory watermark and are not minimum reader-memory estimates.

## Reproduce

Build the optional native HDF5 benchmark (the production reader has no HDF5
dependency). The recorded study uses HDF5 1.14.6:

The benchmark also requires GNU gzip, GNU time and an OpenSSL `libcrypto`
runtime for untimed numerical SHA-256 fingerprints.

```bash
python3 scripts/build_hdf5.py
cmake -S . -B build-gzip -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON -DOPTBIN_WITH_HDF5=ON \
  -DHDF5_ROOT="$PWD/data/deps/hdf5-install"
cmake --build build-gzip -j4
ctest --test-dir build-gzip --output-on-failure
```

Fetch sources or provide local paths as described in [validation.md](validation.md).
The original native text baseline can be reproduced without Python numerical packages:

```bash
python3 scripts/benchmark_gzip.py --repeats 7 \
  --cases afiro AUG2D AUG3DCQP QPLIB_2676 nql30 nb_L2 \
    LogExpCR-n20-m400 infeas_clean_10_10_1 \
  --work benchmarks/runs/gzip-reproduction \
  --output benchmarks/gzip-reproduction.json
```

Supply all continuous IDs from `benchmarks/real-cases.json` through `--cases`
to repeat all 22. Reports and per-case work directories must be new: the harness
does not overwrite an earlier result. For the server cases, provide a manifest
with `id`, `format`, `dialect`, original `path`, existing `optbin` path and
existing `raw_hdf5` path, to reproduce the archived binary-only study. The current large MPS comparison
uses `scripts/benchmark_gurobi.py --repeats 3`, as described in
[the Gurobi reference instructions](gurobi-reference.md). Every report
records input/output SHA-256 hashes, executable/library/source hashes, CPU,
gzip version, run order, numerical hashes, validation results and raw timings.
For the CBLIB comparison, use `scripts/benchmark_mosek.py --repeats 7`, as
described in [the MOSEK reference instructions](mosek-reference.md).

For parallel preparation, add `--gzip-program /path/to/pigz --gzip-threads 16`.
The pinned pigz source archive has SHA-256
`eb872b4f0e1f0ebe59c9f7bd8c506c4204893ba6a8492de31df416f0d5170fd0`.
`scripts/combine_gzip_studies.py` combines completed per-case studies only when
the host and reader executable/libraries match. It retains each input report's
hash and compression settings. The technical-report builder checks that the
combined numerical/timing records equal the original reports. The incomplete
single-threaded mcf compression attempt is excluded; no measurements from it
are reported.

`tests/test_format_bench.py` checks LP/QP/QCQP/conic array agreement, inclusion
of gzip expansion time, CRC/truncation rejection, failed text comparisons and
temporary cleanup. The original native text parsers were extracted into templated
headers to share their logic with an in-memory benchmark sink; the production
converters retain their existing parsing and encoding behavior.
