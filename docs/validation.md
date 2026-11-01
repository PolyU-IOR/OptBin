# Validation scope

The [CI workflow](../.github/workflows/ci.yml) covers builds and installation on
CPython 3.10, 3.12 and 3.14, optional frontends, Gurobi and MOSEK reader
contracts, pinned JuMP fixtures, undefined-behavior checks and technical-note
compilation. See
[GitHub Actions](https://github.com/PolyU-IOR/OptBin/actions) for the results on
the exact source revision being used.
Validation is limited to the documented 64-bit little-endian Linux platform.

## Release candidate installation checks

The 0.2.0rc4 candidate includes Python argument validation and Gurobi reference checks:
22 core/legacy groups, four JuMP-comparison harness groups, 14 optional groups
and four format-benchmark groups, plus five public Gurobi-reference groups and
four public MOSEK conic-reference groups.
Core, Gurobi-reference and MOSEK-reference checks also pass under UBSan. `scripts/check_install.py` verifies the actual source
archive and wheel, including a CMake build/CTest run, a relocated install, an
external `find_package(OptBin)` consumer, and fresh Python environments for
wheel and source installation. Known arrays, bundled-reader selection, budgets,
CLI versions/errors and `pip check` are tested outside the source checkout.
See [release commands](release.md#build-and-verify-release-artifacts).
The hosted matrix repeats these source, wheel and native-install checks in
clean runner environments.

## Native reader and converters

Run the core/legacy groups with `make test` (22 native/legacy groups
and four additional JuMP-comparison harness groups). They require Python's standard
library, compilers and the native build dependencies. Coverage includes:

- Seven legacy BQP groups: randomized sparse models, value codecs, malformed
  varints, corruption, memory limits and parsing semantics.
- LP/QP reconstruction compared byte-for-byte with the v1 converter/reader.
- Independent RAW-only v2 files constructed without the package writer, loaded
  through the public C ABI and a standalone static-library C consumer.
- Quadratic polynomials, row association, QSECTION, dialect selection and
  explicit unsupported-feature failures.
- Native CBF cones, power weights, unscaled PSD coordinates and coefficients.
- Header/directory/payload corruption, truncation, ordering, shapes, memory
  limits, seeded bit corruption and every encoding/Zstd ablation.

Lossless checks use decoded byte equality. Text conversion can canonicalize
duplicate MPS entries; losslessness refers to the resulting binary64 model.

## Optional analytical and adapter checks

```bash
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p test_solvers.py -v
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p test_quadratic_solvers.py -v
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p test_hdf5.py -v
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p test_native_hdf5.py -v
```

SCS/Clarabel checks cover LP, QP, SOC, rotated SOC, exponential, power, PSD
variables and PSD constraints against analytical optimal values. The 3x3 PSD
fixtures distinguish triangle orderings. Tests also check maximization, rejected
nonconvex objectives/QCQP constraints, no mutation of stored arrays, and that
infeasible/unbounded results return no primal objective. The 16 analytical solves
and versions are in `benchmarks/solver-smoke.json`.

Gurobi/SCIP checks cover convex QCQP, bounded bilinear nonconvex constraints,
quadratic equality, and quadratic objective plus quadratic constraints. An
optional MOSEK SDK reader regression verifies both QCMATRIX and QSECTION
polynomial conventions; it caught the dev0 QCMATRIX factor-of-two bug. See
[the correction](mps.md). Gurobi optimization requires a valid license; MOSEK
source reading in this regression does not require an optimization license.

HDF5 checks reconstruct identical arrays through the same C section decoder,
enforce a decoded-byte budget, reject corrupted payload/metadata, and apply full
cross-array model validation after decoding.

The native C HDF5 tests also cover cache-spanning independent RAW blocks,
signed zero, exact QCQP/PSD reconstruction, corruption, budgets and migration.
[Native instructions](native-hdf5.md) explain the build, shared validation,
process RSS and timing boundaries. After the shared-loader refactor, all 22
independent model comparisons passed again; the dated report is
`benchmarks/real-correctness-native-20261027.json`.

## Real source models

The additional [JuMP/MOI comparison](jump-validation.md) uses Julia 1.12.6,
JuMP 1.31.2 and MOI 1.53.0. All 22 real models agree after explicit numerical
function-in-set normalization. The expanded fixture suite retains five reviewed
MPS reader differences and one unsupported generalized power cone separately.
The MOSEK SDK confirms the four quadratic discrepancy cases; Gurobi confirms
the negative-upper-bound case. These are not counted as JuMP agreement passes.
The CI workflow includes a fixture-only JuMP job. Real-model results are in
`benchmarks/jump-correctness.json`.

`benchmarks/real-cases.json` identifies inputs, hashes, public URLs and derived
variants. `scripts/validate_real.py` compares complete model data with independent
source readers. **22 continuous models passed; six integer CBF cases were
excluded and verified to fail explicitly.**

- All 13 LP/QP/QCQP MPS models: public Gurobi 13.0.3 source reader, including
  the complete linear model, objective Hessian and each quadratic constraint
  polynomial. Four MPS files were exported by Gurobi 13.0.3 from
  public QPLIB LP files; original LPs/hashes are retained separately.
- Nine CBFs: the original CBLIB CBFdata parser, pinned by commit and SHA256,
  including cone metadata and raw PSD coefficients. Only a Python 2 `xrange`
  compatibility binding is added at import; its source is unmodified.
- The same nine CBFs also pass complete independent public MOSEK 11.2.7 Task
  extraction, comparing conic functions, domains, scalar bounds and PSD trace
  coefficients exactly. The [MOSEK reference](mosek-reference.md) documents
  normalization, helper limits and matched read timings. Its four contract
  groups test plain/gzip input, each supported cone family, one-ULP and PSD
  factor changes, altered constants/domains and explicit unsupported features.

The current report is
[`real-correctness-gurobi-20261030.json`](../benchmarks/real-correctness-gurobi-20261030.json).
Gurobi is the default MPS reference. The earlier HiGHS LP/QP comparison remains
available through `--mps-reference highs`; those earlier results are retained.
Reference normalization sorts sparse coordinates and compares the documented
quadratic polynomial conventions. The HiGHS option additionally removes zero
Hessian diagonals inserted by that reader. No nonzero reference coefficients are changed.
Several QP inputs are local presolved variants, explicitly labeled in the
manifest; they are not asserted to be canonical benchmark originals.

`benchmarks/real-solvers.json` records source-vs-native solver runs. MPS sources
use Gurobi directly; the four LP-origin QPLIB cases are solved from the original
LP. CBF references use CBLIB data to build CVXPY models independently of OptBin
arrays and PSD vectorization. Native CBF primal solutions are evaluated against
original CBF coefficients and domains. See [qualified results](../benchmarks/evaluation.md).
Small objective differences alone are not used to establish reader correctness.

Full original-coordinate infeasibility certificate validation is **pending**.
The adapter returns certificates and necessary linear diagnostics, but does not
yet establish all dual-cone membership conditions. Two difficult SDP cases have
inaccurate/inconclusive solver outcomes, despite matching source model data.

## Reproduce and inspect

```bash
.venv/bin/python -m pip install -e '.[solvers,quadratic,gurobi,validation,benchmark]'
.venv/bin/python scripts/fetch_real.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/validate_real.py \
  --manifest benchmarks/real-cases.json --output benchmarks/real-correctness.json --reconvert
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/validate_solver_agreement.py
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 .venv/bin/python scripts/evaluate_storage.py --repeats 5
```

The fetcher verifies public source hashes and retains original notices. It does
not redistribute local MPS variants. Supply those files or edit a copy of the
manifest to select available cases. `--cbf-only` fetches without Gurobi exports.
Run validation before solver agreement to create `data/converted/`. Data caches
and bulk baseline artifacts are ignored by Git; reports and scripts are kept.

To repeat undefined-behavior checks in a separate directory:

```bash
make BUILD=build-ubsan -j4 \
  CFLAGS='-O1 -g -std=c11 -fsanitize=undefined -fno-sanitize-recover=all' \
  CXXFLAGS='-O1 -g -std=c++17 -fsanitize=undefined -fno-sanitize-recover=all' \
  LDFLAGS=-fsanitize=undefined
make test BUILD=build-ubsan PYTHON=.venv/bin/python LDFLAGS=-fsanitize=undefined
OPTBIN_BUILD="$PWD/build-ubsan" OPTBIN_LIBRARY="$PWD/build-ubsan/liboptbin.so" \
  .venv/bin/python -m unittest discover -s tests -p test_hdf5.py -v
```

The current [gzip study](gzip-comparison.md) uses public `gurobipy.read` as its
MPS timing reference, alongside full independent model verification. The
[reference contracts](gurobi-reference.md) cover one-ULP mismatches, all LP
fields, explicit zeros and public row-block extraction. A separate
>2^32-entry full loading check remains correctness evidence. Cold
storage, more large QP/QCQP/PSD models and GPU transfers remain in
[the roadmap](roadmap.md). Historical v1 timings remain separate evidence.
