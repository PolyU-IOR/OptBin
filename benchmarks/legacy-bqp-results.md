# Full-instance BQP benchmark

Run on 2026-10-19 using the eight complete `.mps.gz` files in `/benchmark/datasets/Oliver_Hinder/larger` on `benchmark-server`.

**All eight complete files passed streaming and CPU checks; seven passed CUDA readback.** The largest model used the CPU path because its decoded arrays exceed the configured 65-GiB CUDA budget.

Total input: **157.82 GB**. Total BQP output: **58.42 GB**. Reduction: **62.98%** (2.70x smaller), measured against already-compressed MPS.

GB below means decimal 1,000,000,000 bytes. Times are seconds. GPU loading includes CPU block decoding and staged H2D transfers.

| Instance | MPS.gz GB | BQP GB | Smaller | Decoded GB | CPU load s | GPU load s |
|---|---:|---:|---:|---:|---:|---:|
| prod_200_400_02 | 7.832 | 0.474 | 16.53x | 30.054 | 54.1 | 42.1 |
| prod_200_500_02 | 14.663 | 0.776 | 18.89x | 56.953 | 100.8 | 79.4 |
| mcf_10000_100_1000 | 22.660 | 10.036 | 2.26x | 56.517 | 109.8 | 87.4 |
| mcf_10000_120_1000 | 27.263 | 11.681 | 2.33x | 67.788 | 130.7 | 103.6 |
| mcf_10000_150_1000 | 34.304 | 13.891 | 2.47x | 84.695 | 159.0 | CPU fallback |
| mcf_5000_100_1000 | 11.331 | 4.789 | 2.37x | 28.258 | 54.2 | 43.6 |
| mcf_5000_150_1000 | 17.061 | 7.188 | 2.37x | 42.347 | 80.5 | 65.3 |
| mcf_5000_200_1000 | 22.703 | 9.588 | 2.37x | 56.436 | 108.2 | 87.0 |

## Validation and measurement scope

- Every block has a decoded-data CRC32. All full-file stream/CPU checksums matched the converter's checksums over the original unencoded numerical arrays. All seven GPU checksums matched after device-to-host readback.
- An independent Python parser also compared the first 65,536 canonical matrix entries per original MPS file against the C reader: 524,288 source entries matched exactly. This reference check is a prefix sample; it is distinct from full-file binary round-trip validation.
- Seven unit-test groups cover numeric encodings, LP/QP semantics, randomized sparse models, 64-bit indices, malformed/truncated data, and output/memory limits. The undefined-behavior sanitizer suite passed. A GPU-first example was also checked for CPU fallback.
- The provided large instances are LPs. QP support was tested with fixtures including QUADOBJ and symmetric QMATRIX, not a separate large QP corpus.
- OS caches were not cleared. Loads followed conversion; CPU workers overlapped on a shared server. These are potentially warm-cache measurements, not isolated storage throughput measurements.
- `load_seconds` includes allocation, decoding, validation, and GPU upload where applicable. The extra full-array verification/readback and freeing time is recorded separately in JSON and excluded from this table.
- No optimization solver was run. Sparse layout conversion, solver presolve, solver copies, and solve workspace are outside these measurements.
- CPU loads validate canonical column ordering and Q triangle placement. Stream/CUDA loads validate block checksums, complete array coverage, pointer monotonicity/endpoints, and index ranges.

## Runtime memory and conversion

Peak host RSS during the seven GPU load/readback runs was at most **108.8 MiB**. GPU arrays were fully expanded to FP64 and integer CSC buffers; compact file size is not GPU solve-memory size.

The largest model contains 84,694,809,008 decoded bytes (78.88 GiB). Its GPU check stopped at the explicit 65-GiB allocation budget, then retained the successful CPU result. This is a budget decision, not a claim that its compressed file cannot be transferred to a GPU.

| Instance | A entries | Conversion s | Converter peak RSS GiB |
|---|---:|---:|---:|
| prod_200_400_02 | 2,310,559,600 | 534.3 | 1.80 |
| prod_200_500_02 | 4,443,349,500 | 1011.7 | 2.79 |
| mcf_10000_100_1000 | 2,015,000,100 | 923.8 | 23.62 |
| mcf_10000_120_1000 | 2,418,000,120 | 1118.2 | 33.22 |
| mcf_10000_150_1000 | 3,022,500,150 | 1390.5 | 35.33 |
| mcf_5000_100_1000 | 1,007,500,100 | 465.4 | 11.83 |
| mcf_5000_150_1000 | 1,511,250,150 | 700.8 | 17.69 |
| mcf_5000_200_1000 | 2,015,000,200 | 929.4 | 23.51 |

Recorded batch-runner wall time: 1977.9 seconds (33.0 minutes). Conversion time includes MPS parsing, encoding, compression, and writing; it must not be interpreted as a standalone MPS-read baseline.

## Environment and saved artifacts

- Intel Xeon Gold 6548Y+, 128 logical CPUs, about 1.48 TiB RAM.
- NVIDIA H100 PCIe, 81,559 MiB reported capacity; GPU ordinal 2 used. Driver 570.124.06, CUDA runtime 12.8.
- Eight conversion workers; GPU benchmarks serialized. Device-activity skips were retried after the utilization meter settled.
- Zstd level 1; 65,536-element blocks. The format uses CSC, 64-bit pointers, adaptive lossless value encoding, and delta-compressed indices.
- Source/build directory: `/benchmark/server/bqp-format-20261019`.
- Converted models and full reports: `/benchmark/server/bqp-data-20261019`.

Raw reports: [summary](results/summary.json), [environment](results/environment.json), [encoding breakdown](results/encoding_stats.json), and [independent source-prefix check](results/prefix_reference_check.json).

See [package README](../README.md) for usage, supported MPS scope, GPU behavior, and limits. See [BQP v1 specification](../docs/bqp-v1.md) for the binary specification.
