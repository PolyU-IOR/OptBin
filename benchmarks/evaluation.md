# Development evaluation, 2026-10-26

This milestone adds independent model comparisons, native quadratic adapters,
source-vs-native solver checks, and real-model encoding/container experiments.
The package remains a development prototype; this is not a large-scale v2
performance or publication result.

## Complete model comparisons

[real-correctness.json](real-correctness.json): **22 continuous models passed**
complete comparisons with independent readers: eight LP/QP, five QCQP and nine
CBF models. Six integer CBF inputs were excluded and rejection verified. The
manifest records public sources, local variants and hashes; the report records
converted file hashes and reference normalizations.

HiGHS supplies LP/QP data. Gurobi supplies QCQP polynomials. The CBLIB reference
parser supplies original CBF cone and PSD data. Matching includes coefficients,
sparse indices, bounds, objective sense/offset, cone parameters and quadratic
row associations, as applicable. Sorting sparse coordinates and removing
HiGHS-inserted zero Hessian diagonals are the only reference normalizations.

**Correctness fix:** the live MOSEK 11.2.7 reader showed that QCMATRIX represents
`x'Qx`, matching Gurobi 13.0.3; QSECTION represents `0.5*x'Qx`. Dev0 assigned
the wrong multiplier to MOSEK QCMATRIX. Dev1 corrects this and tests both section
forms against the SDK. Regenerate affected dev0 conversions. Existing BQP v1
files and the v2 schema are unaffected.

## Solver checks

[real-solvers.json](real-solvers.json) records **55 runs on 22 models**, with
source hashes, OptBin hashes, dependency versions and time limits.

- All 13 MPS source/native Gurobi pairs returned optimal status. Maximum
  normalized objective difference was `6.62e-7`, using
  `abs(native-source)/max(1,abs(source))`. Native row violations were at most
  `1.05e-6` absolute (`5.23e-7` relative); bounds were satisfied.
- Seven ordinary CBFs were compared through an independent CBLIB-to-CVXPY
  modeling path and OptBin-to-Clarabel/SCS. Clarabel objective differences were
  below `2.1e-9` by the same normalization. The scheduling case had
  `optimal_inaccurate`/`AlmostSolved` status in the reference/native paths. SCS
  reached its iteration limit on nql30, and its largest normalized objective
  difference in this group was about `8.4e-6` on scheduling.
- Two native SCIP QCQP runs reached their 10-second limits with feasible
  incumbents close to the source objectives. These are not optimality passes.
- The two infeasibility SDP stress cases are kept as qualified outcomes.
  Clarabel agrees with the reference's infeasible status on the clean case;
  the messy case is inconclusive. SCS returns inaccurate candidates for both.
  Matching model data does not turn those candidates into valid solutions.

Native CBF primal solutions are evaluated against original coefficients and
domains. Returned infeasibility certificates have necessary linear diagnostics,
but complete original-coordinate dual-cone certificate validation remains open.

## Storage experiments

[real-storage.json](real-storage.json) contains six real models, eight
value/index/Zstd variants per model, and six array-loading paths. Every path and
variant reconstructs identical decoded bytes. Each measured path has five
repetitions after a warmup; six-format measurement rounds are shuffled.

Stored sizes below include container metadata. The raw+Zstd baseline includes a
JSON manifest and compresses all arrays together at level 1. Encoded HDF5 carries
the exact same BLK1 bytes as OptBin with no additional HDF5 compression.

| Model | OptBin bytes | HDF5, identical codecs | Raw arrays + Zstd |
|---|---:|---:|---:|
| AUG2D | 2,184 | 11,416 | 147,406 |
| QPLIB_2676 | 7,768 | 18,976 | 19,405 |
| nql30 | 22,056 | 38,512 | 48,358 |
| nb_L2 | 628,728 | 645,504 | 616,951 |
| LogExpCR-n20-m400 | 67,744 | 84,496 | 75,436 |
| infeas_clean_10_10_1 | 4,192 | 19,720 | 3,525 |

AUG2D is unusually regular, so its very small representation is not a general
compression expectation. Specialized encoding substantially helps some models;
whole-array Zstd is smaller on nb_L2 and the tiny SDP. Ablations also show the
default pre-Zstd codec selection is not always globally smallest: nb_L2's
values-only mode is 48 bytes smaller than all encodings enabled. The writer
does not search every codec-plus-compressor combination.

The identical-codec HDF5 results show the encoding benefit can be carried inside
HDF5. The custom container saves roughly 9–17 KB of metadata on this sample;
that fixed cost says little about multi-gigabyte workloads. OptBin is a separate
container, not an HDF5 enhancement or a new compression algorithm.

Warm read/decode/full-validation medians range from about 0.26 to 4.76 ms for
OptBin. Raw arrays are faster in these small cases. HDF5 paths use h5py and the
encoded path also makes Python calls per section; baseline metadata caching and
allocation differ. These timings cannot establish intrinsic container speed
superiority. No cold-disk, separate RSS, layout conversion, GPU upload or solver
setup is measured. Binary loading timings must not be compared with complete
source conversion times as if both were reader-only timings.

## Next evidence needed

Large v2 LP/QP/QCQP/conic workloads, a native C HDF5 baseline using the same
codecs, cold/warm I/O and separately measured RSS come next. GPU loading,
parallel decoding, bounded-memory CBF sorting and complete certificate checks
remain on [the roadmap](../docs/roadmap.md). These results support continued
engineering evaluation, not freezing or publishing an interchange standard yet.

Build, reproduction commands and optional dependencies are in
[validation.md](../docs/validation.md). Source data retain their original
attribution; local presolved variants are labeled and are not redistributed.

<!-- BEGIN NATIVE_EVALUATION -->
## Native C evaluation, 27 October 2026

The matched native experiment supersedes Python/frontend timing comparisons for the container question. Reports retain exact comparisons, all trials, RSS snapshots and scope limits. Both encoded paths share parsing, allocation, block decoding, decoded CRCs and full validation.

Large measurements use the Xeon server and three shuffled fresh-process trial rounds; smaller measurements use the i9 workstation and seven rounds. Files are warm. Hashing and explicit byte comparisons are outside the timer and RSS snapshot.

| Model | OptBin GiB | H5 same codecs GiB | H5 raw GiB | H5 shuffle/gzip GiB | Decoded GiB |
|---|---:|---:|---:|---:|---:|
| prod_200_400_02 | 0.441 | 0.441 | 27.992 | 0.783 | 27.99 |
| mcf_5000_100_1000 | 4.460 | 4.460 | 26.319 | 4.732 | 26.32 |

| Model | OptBin seconds | H5 same codecs seconds | H5 raw seconds | H5 shuffle/gzip seconds |
|---|---:|---:|---:|---:|
| prod_200_400_02 | 51.19 | 51.13 | 42.26 | 74.51 |
| mcf_5000_100_1000 | 52.16 | 52.32 | 39.00 | 101.21 |

Large-case details:

- `prod_200_400_02`: raw HDF5 takes 63.42x the file space. The same-codec HDF5/OptBin median-time ratio is 0.9989; OptBin peak RSS is 27.99 GiB. Observed timing ranges overlap: true.
- `mcf_5000_100_1000`: raw HDF5 takes 5.90x the file space. The same-codec HDF5/OptBin median-time ratio is 1.0031; OptBin peak RSS is 26.32 GiB. Observed timing ranges overlap: false.

Interpretation:

- Identical encodings can be reused inside HDF5. These measurements do not establish universal custom-container superiority.
- Warm raw-array loading can be faster despite much larger files. Compressed size and CPU loading cost are different objectives.
- Process memory is dominated by identical decoded arrays. RSS excludes filesystem page cache and later solver/adapter allocations.
- Historical MPS/HPR-LP timings include different model-construction work and are not divided by these native timings.
- The extra legacy-BQP compatibility timings include two validation layers and are supplementary, not a clean container comparison.
- The shuffle/gzip comparison changes the compressor and encodings together. Large-instance matched-compressor ablations remain pending.
- The v2 files were migrated from existing BQP blocks. This is not a new large-MPS converter timing.

Separately, `prod_200_500_02` passes a full native v2 load and byte comparison with its BQP source at 4,443,349,500 stored entries, using 32-bit row indices and 64-bit pointers. This is a correctness check, not a repeated performance measurement. All eight source BQP payload SHA-256 hashes match their existing manifest; the integrity pass ran after the timing experiments.

[Native methodology](../docs/native-hdf5.md), [raw small report](native-cpu.json), [raw large report](native-large.json), [derived analysis](native-analysis.json), [input provenance](large-inputs.json), [boundary correctness](large-boundary.json), [technical note](../docs/optbin-technical-report.pdf).
<!-- END NATIVE_EVALUATION -->
