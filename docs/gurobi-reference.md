# Gurobi as the MPS source reference

The primary MPS reference is **Gurobi 13.0.3, using public `gurobipy.read`**.
`scripts/validate_real.py` now selects Gurobi for LP/QP/QCQP by default;
`--mps-reference highs` retains the earlier HiGHS LP/QP comparison. CBF uses
[MOSEK's public reader](mosek-reference.md) for timing and full conic agreement;
the original pinned CBLIB parser remains an additional independent validation.

The [complete independent check](../benchmarks/real-correctness-gurobi-20261030.json)
passes all 13 MPS LP/QP/QCQP models and all nine CBF models. Six integer CBF inputs
are rejected explicitly. The MPS comparisons include linear coefficients,
variable/row bounds, objective sense/constant, the complete quadratic objective
and every quadratic constraint polynomial. Solver objective agreement is not
used as a substitute for comparing the model.
Source checks reject discrete, multiobjective, multiscenario, general/SOS and
piecewise-objective features outside the declared continuous single-objective
LP/QP/QCQP profile. A negative multiobjective fixture verifies that matching only
the first objective cannot be accepted as complete model agreement.

## Timing and numerical verification

`scripts/gurobi_read.py` creates and starts an environment, disables output, sets
the `Threads` parameter to 1, and times only `gurobipy.read(source, env=env)`.
Compressed `.mps.gz` input is passed directly to Gurobi. No optimization,
presolve, conversion or serialized model output occurs. Environment startup is
recorded separately; package import and process startup are outside the timer.

The read endpoint is a Gurobi solver model, including its own metadata and
representation. The binary loaders return validated native numerical arrays.
These are practical public loader endpoints; the measurements do not isolate
text tokenization or create identical solver objects on both paths.

The process RSS snapshot is taken immediately after `gurobipy.read` returns,
before reference loading and attribute/matrix extraction. The verification
phase is outside the read timer and RSS snapshot:

- Smaller LP/QP/QCQP cases use the complete polynomial comparator in
  `scripts/validate_real.py`.
- Large LP cases use public `getA()`/`getAttr()` calls and compare the entire
  CSC matrix, objective coefficients, variable bounds, row bounds, sense and
  objective constant with an OptBin model loaded and fully validated by C.
  For matrices above the legacy `getA()` nonzero-count limit, public model
  copies retain contiguous row blocks before querying. The original model is
  unchanged; the verified blocks must cover every original row and nonzero.
- Gurobi infinity sentinels, signed zeros and explicit sparse zeros are
  normalized. Comparison uses exact numerical equality without a tolerance.
- Binary representations pass decoded-array byte comparisons and share the
  same native numerical SHA-256 fingerprint.

Full verification holds extra decoded arrays, CSR/CSC matrices, row-model copies
and Python attribute lists. Its memory requirement can greatly exceed the
read-phase RSS in the comparison table; that table is not a memory budget for
running the complete validation harness.

`scripts/benchmark_gurobi.py` verifies each model before timing, then shuffles
Gurobi, raw.h5.gz, OptBin and OptBin.gz across fresh-process rounds. Timed Gurobi
runs must retain the verified dimensions and Gurobi model fingerprint. Its
fingerprint is distinct from the binary-array SHA-256 and is not treated as
cross-format numerical proof. Full independent array/polynomial verification
occurs in the separate warmup phase.

Files are warm; no global cache flush is used. Binary outer-gzip expansion to a
seekable temporary remains inside the timed loading path. Gzip creation is
preparation and is excluded from repeated read times. RSS excludes filesystem
page cache, temporary disk, and later extraction/solver allocations.
Only large-case RSS is interpreted in the note. In this Linux subprocess setup,
`ru_maxrss` can retain a pre-exec watermark from the parent;
[a local probe](../benchmarks/reader-rss-watermark-20261030.json) raised
a tiny native read's reported peak from 13.3 to 140.8 MiB solely by retaining
128 MiB in its parent. Small-process RSS is therefore not a minimum reader-memory
measurement. The large decoded arrays greatly exceed this startup floor.

The two large case jobs run concurrently on the shared server. Within each job,
formats run sequentially in shuffled rounds. This is recorded in the combined
report; the benchmark does not claim an exclusive node or controlled cold I/O.

The workstation worker source is preserved at Git revision
`fa05a2f03c96856e05e5317e0f099413e671b7d2`; the large worker adds nonzero-balanced
public row queries and reference-only quadratic guards at revision
`96e9ece`. Reports retain exact source and measured development-binary hashes.
The large LP checks reject quadratic data on either side of the comparison.
A [separate post-timing profile audit](../benchmarks/gurobi-large-profiles-20261030.json)
uses the later worker at `8e734fa` to confirm `IsMIP=0`, one objective and no
integer, quadratic, general/SOS or piecewise-objective features on both large
sources. Fresh file hashes, dimensions and Gurobi fingerprints match the fully
verified timing models. These additional reads are excluded from timing medians.
The [recorded query boundary](../benchmarks/gurobi-api-boundary-20261030.json)
distinguishes the successful large read from the earlier `getA()` extraction
failure in Gurobi 13.0.3; failed/stopped verification attempts are not trials.

## Reproduce

Install the optional reference environment and build the native tools:

```bash
.venv/bin/python -m pip install '.[gurobi]' 'gurobipy==13.0.3'
cmake -S . -B build-gurobi -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_SHARED_LIBS=ON -DOPTBIN_WITH_GUROBI_BENCH=ON \
  -DPython3_EXECUTABLE="$PWD/.venv/bin/python"
cmake --build build-gurobi -j4
ctest --test-dir build-gurobi --output-on-failure
```

The optional C client `optb_gurobi_bench` uses public `GRBreadmodel` and
`GRBXgetvars` (64-bit nonzero counts). It requires a license that supports the
C interface. The server's pip license rejects a standalone C client, so the
reported comparison uses the public Python interface on both hosts. No license
checks are bypassed and no Gurobi SDK is included in this package.

Run the complete small-model independent check:

```bash
OPTBIN_LIBRARY="$PWD/build-gurobi/liboptbin.so" \
  .venv/bin/python scripts/validate_real.py \
  --manifest benchmarks/real-cases.json --mps-reference gurobi \
  --output benchmarks/runs/gurobi-correctness-reproduction.json
```

After reproducing the stored files from [the gzip study](gzip-comparison.md),
the benchmark can reuse those exact representations without recompressing them:

```bash
.venv/bin/python scripts/benchmark_gurobi.py \
  --baseline-report benchmarks/gzip-readers-20261029.json \
  --python-validation --repeats 7 \
  --work benchmarks/runs/gurobi-reproduction \
  --output benchmarks/runs/gurobi-reproduction.json
```

An archived baseline report records paths on its original host. Supply a report
with the corresponding current file paths to reproduce elsewhere. Inputs are
hashed again, and must match the recorded sizes and SHA-256 hashes. Work and
output paths must be new. For large LPs, omit `--python-validation` to select
the public-API full-LP verification and use three repetitions.
For a fresh experiment on another machine, first run `benchmark_gzip.py` to
create that experiment's files and local baseline report, then pass that report
to `benchmark_gurobi.py`. The published development-file hashes are not
expected to match newly converted files with different provenance or codecs.

The two original large case reports can be assembled with:

```bash
.venv/bin/python scripts/summarize_gurobi.py \
  --combine benchmarks/gurobi-prod-20261030.json benchmarks/gurobi-mcf-20261030.json \
  --case-concurrency 2 --output benchmarks/runs/gurobi-large-combined.json
```

Assembly rejects unfinished cases, different hosts/readers/contracts, duplicate
models, incomplete verification coverage and incorrect medians. The combined
report retains the source reports and their hashes.

The five contract groups test plain/gzip reads, complete LP arrays, explicit-zero
normalization, one-ULP/metadata/bound/coefficient mismatches, quadratic coverage
boundaries, public row partitions and error handling. They pass locally and on the server; local native
CLI and public-reference contracts also pass UBSan. Hosted CI is configured
separately and has not yet run.

The relevant public API contracts are documented in
[Gurobi's Python read reference](https://docs.gurobi.com/projects/optimizer/en/current/reference/python/func_global.html#read)
and [model queries](https://docs.gurobi.com/projects/optimizer/en/current/reference/python/model.html).
