# Benchmark evidence

The current technical note uses the [gzip-inclusive practical comparison](../docs/gzip-comparison.md):
MPS.gz, CBLIB CBF.gz, raw.h5.gz, default OptBin and OptBin.gz. Complete loading
includes decompression; gzip creation is recorded separately. Current MPS rows use
[Gurobi on 13 smaller models](gurobi-small-20261030.json) and
[Gurobi on two large LPs](gurobi-large-20261030.json), with fresh matched binary trials.
The nine CBLIB rows use [MOSEK's public CBF reader](mosek-cbf-20261030.json),
also with fresh matched binary trials and complete independent conic comparisons.
[Gurobi methodology](../docs/gurobi-reference.md) and
[MOSEK methodology](../docs/mosek-reference.md) distinguish public solver-model
loading from native array loading and describe full numerical validation.
The earlier [native MPS/CBF study](gzip-readers-20261029.json) and
[large binary study](gzip-large-20261029.json), and the older container/codec experiments
below remain archived evidence and are not the note's comparison tables.

The current [Gurobi/CBLIB correctness check](real-correctness-gurobi-20261030.json)
matches complete numerical models on 22 continuous inputs. The archived
[development evaluation](evaluation.md) summarizes the earlier model
comparisons on 22 continuous real inputs, 55 source/native solver runs, and a
six-model/six-format storage experiment with codec ablations. Raw reports are
[real-correctness.json](real-correctness.json), [real-solvers.json](real-solvers.json)
and [real-storage.json](real-storage.json). The first retains the earlier HiGHS
LP/QP comparison. Inputs and attribution are in
[real-cases.json](real-cases.json); data caches are not committed.

The completed [native CPU report](native-cpu.json) uses shared C decoding and
validation across four representations, explicit byte comparisons, seven trial
rounds per smaller model, and resident-memory snapshots before hashing. See
[native methodology](../docs/native-hdf5.md). The Python and native reports have
different timing boundaries and must not be combined silently.

The [two-large-LP report](native-large.json) adds three shuffled trial rounds
per representation with exact comparisons. [Derived results](native-analysis.json)
show nearly equal same-codec HDF5 and OptBin loading times. A separate
[full boundary check](large-boundary.json) verifies exact loading of
`prod_200_500_02` with 4,443,349,500 entries and 32-bit row indices; it is not a
repeated performance benchmark. All eight source BQP payload hashes were
[verified against their existing manifest](large-inputs.json) after timing.

`solver-smoke.json` records optional tests on small analytical fixtures, including
library versions and known objectives. It provides no large-instance timing claim.

Saved machine paths and hostnames use synthetic `/benchmark/...` paths and
`benchmark-workstation` / `benchmark-server` labels. These are placeholders,
not live addresses or download locations. Numerical results, input-file hashes,
library versions and measured binary/source fingerprints are retained. References
between reports use the hashes of their saved copies. Supply your own data paths
when reproducing a study; the large models are not bundled.

Historical results: total MPS.gz 157.82 GB, BQP 58.42 GB. Two full native HPR-LP
loads reconstructed all arrays bitwise and had read-plus-model-construction ratios
of 9.00x (`prod_200_400_02`) and 9.48x (`mcf_5000_100_1000`). They were single
potentially cached runs. They do not benchmark this v2 implementation or HDF5.

`legacy-bqp-results.md` and `results/` contain the older BQP v1 loading study.
These records are separate from the OptBin v2 results in the technical note.

The original instances and generation details are attributed to
[Oliver Hinder's collection](https://www.oliverhinder.com/large-scale-lp-problems).
The current CBF evaluation uses source-attributed instances from
[CBLIB](https://cblib.zib.de/), with the original notices retained.

## Array baseline harness

`initial-baseline-smoke.json` is a functional run on 160,000 entries in an
artificially repetitive LP. All five loading paths reconstruct the same arrays.
The model is intentionally highly compressible and is not a representative
benchmark. Cached manifest metadata, Python binding overhead and compression
choices also differ; see the limitations in the report.

That historical smoke report predates the sixth path. The current harness also
loads HDF5 with identical OptBin BLK1 bytes through the shared C decoder. It
warms each path and shuffles repeated measurement rounds. Real results are
reported separately in `real-storage.json`; they show both compression wins and
counterexamples versus whole-file Zstd. The HDF5 Python frontend is not a matched
native C container-speed baseline.

To reproduce the harness:

```bash
mkdir -p benchmarks/runs/example
python3 scripts/generate_synthetic.py benchmarks/runs/example/source.mps
build/mps2optb benchmarks/runs/example/source.mps benchmarks/runs/example/model.optb
.venv/bin/python scripts/benchmark_storage.py benchmarks/runs/example/model.optb \
  --output benchmarks/runs/example/baselines --repeats 3
```

For the real sources and all eight encoding/Zstd variants:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/evaluate_storage.py --repeats 5
```

See [validation/reproduction](../docs/validation.md) for source fetching,
independent readers, optional solver requirements and selection of available
inputs through a manifest.
