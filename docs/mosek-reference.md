# MOSEK as the CBF source reference

The primary CBF timing reference is **MOSEK 11.2.7, public
`Task.readdata`**. It reads each original CBLIB `.cbf.gz` directly. All nine
continuous cases pass a complete independent conic-model comparison against
OptBin; seven shuffled fresh-process trials per format are recorded in
[the CBF study](../benchmarks/mosek-cbf-20261030.json). The same stored files
and native binary reader hashes are retained from the earlier gzip study.
Every binary path has fresh matched trials and exact decoded-array checks.

Default OptBin loads 3.62–8.00 times as fast as MOSEK on these nine cases;
OptBin.gz loads 2.25–6.88 times as fast, including expansion and staging.
These are small, warm-file workloads, not large conic-model speedup claims.
See [the full tables](gzip-comparison.md) for absolute times and stored sizes.

## Timing contract

`scripts/mosek_read.py` creates the environment and Task and sets `num_threads=1`
before starting the read timer. Only `Task.readdata(source)` is timed. The
process peak RSS snapshot is taken immediately on return, before querying any
model attributes or loading an OptBin reference. Environment/Task startup,
public-API extraction, hashing and reference comparison are recorded separately.
No optimization, presolve or model-file output is performed. The installed SDK
can read these files without an optimization license.

MOSEK returns its own solver Task with metadata and affine conic structures;
the binary paths return numerical arrays that pass full native validation.
The measurements do not isolate tokenization or build identical solver objects
from both representations. Small-process RSS includes startup and may retain
the parent's pre-exec watermark, so it is not interpreted as minimum reader
memory. Binary outer-gzip staging is included in the corresponding read timer;
gzip creation remains separate preparation.

## Full numerical agreement

The reference check extracts the entire numerical Task through public APIs:
scalar objective coefficients and constant, objective sense, scalar variable
bounds, linear rows and bounds, affine expression coefficients/constants,
affine conic constraint domains and offsets, PSD dimensions, PSD objective
coefficients and sparse PSD operators. It never validates by writing CBF back
and rereading with OptBin's parser.

Comparison uses the complete function-in-set model, with scalar IDs followed
by unscaled PSD triangle coordinates. Explicit normalization accounts for:

- Equivalent grouping of scalar linear cones and bounds.
- EXP/dual EXP coordinate order.
- Twice the off-diagonal coefficient in PSD trace inner products.
- Sparse duplicate sums, explicit zeros, signed zeros and irrelevant row order.

The comparator uses exact binary64 equality without a numerical tolerance.
All four checks (variables, sense, objective and constraints) and canonical
SHA-256 fingerprints must agree. Timed MOSEK trials extract the full canonical
model after the timer and must retain the independently verified fingerprint.
The binary numerical-array SHA is a different representation and is checked
separately; it is not equated to the conic canonical fingerprint.

The nine real cases cover SOC, exponential cones and PSD variables. Contract
fixtures additionally cover linear cones, RSOC, dual EXP and three-dimensional
power/dual-power domains. The exact MOSEK reference helper explicitly rejects
integer/disjunctive/quadratic models, legacy cones, generalized power/geometric
mean domains and `svec` PSD affine domains. The last require a separate
rounding-aware comparison because of their square-root-of-two coordinate
scaling. These reference-helper limits do not narrow the production OptBin
CBF storage scope: original-CBLIB, analytical solver and JuMP checks remain
separate evidence for supported PSD affine operators and power models.

Four test groups check plain/gzip agreement across cone families, objective
and PSD off-diagonal factors, one-ULP coefficient changes, affine constants,
cone-type changes, unsupported features and missing inputs. They never solve.
The benchmark source is pinned at revision `8e734fa`; its report also records
every measured source, native reader, SDK library and input-file SHA-256 hash.

## Reproduce

Build the native tools and optional reference dependencies:

```bash
.venv/bin/python -m pip install '.[mosek]' 'Mosek==11.2.7'
make -j4
OPTBIN_BUILD="$PWD/build" OPENBLAS_NUM_THREADS=1 \
  .venv/bin/python -m unittest discover -s tests -p test_mosek_bench.py -v
```

For an individual source and an existing native model:

```bash
OPTBIN_LIBRARY="$PWD/build/liboptbin.so" \
  .venv/bin/python scripts/mosek_read.py /path/model.cbf.gz \
  --reference /path/model.optb
```

Prepare the four stored representations using the [gzip study](gzip-comparison.md),
then pass its local baseline report to the matched benchmark:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
  .venv/bin/python scripts/benchmark_mosek.py \
  --baseline-report benchmarks/gzip-readers-20261029.json \
  --binary-build build-gzip --repeats 7 \
  --work benchmarks/runs/mosek-reproduction \
  --output benchmarks/runs/mosek-reproduction.json
```

Archived baseline paths identify the original host. Reproduce the input files
and supply a local baseline report on another host; output and work paths must
be new. Input sizes and SHA-256 hashes are checked before every warmup.

MOSEK documents [CBF and gzip input support](https://docs.mosek.com/latest/pythonapi/supported-file-formats.html)
and the public [Task API](https://docs.mosek.com/latest/pythonapi/optimizer-task.html),
including `readdata`, affine expressions, domains and PSD triplet extraction.
