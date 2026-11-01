# OptBin

[![Build and install](https://github.com/PolyU-IOR/OptBin/actions/workflows/ci.yml/badge.svg)](https://github.com/PolyU-IOR/OptBin/actions/workflows/ci.yml)

OptBin is an experimental, lossless binary storage package for continuous LP,
QP, QCQP and conic optimization models. It includes MPS and CBF converters, a
standalone C11 reader, Python bindings, CPU SCS/Clarabel conic adapters, and
optional native Gurobi/SCIP quadratic adapters.

The new `.optb` container is version 2. The reader also loads the earlier `.bqp`
version 1 files, including the eight previously converted Oliver Hinder models.
Existing BQP files do not need to be regenerated.

**Status: experimental release candidate 0.2.0rc4.** The wire format and C ABI
are not frozen. [Installation and release scope](docs/release.md) covers native
tools, the bundled Python reader, source/wheel checks and platform limits.
Complete model data match independent
readers on 22 continuous real models, including five QCQPs and nine CBF models.
Six integer CBF cases are explicitly rejected. Source-vs-native solver results
are recorded in [the evaluation](benchmarks/evaluation.md).
MPS validation now uses [Gurobi's public reader](docs/gurobi-reference.md) by
default; all 13 real MPS LP/QP/QCQP models match its complete numerical model.
[MOSEK's public CBF reader](docs/mosek-reference.md) matches all nine CBLIB
models and provides their gzip-inclusive source-reading timing reference.
An additional [JuMP/MOI check](docs/jump-validation.md) matches all 22 real
models, with targeted MPS reader differences and unsupported reference cases
recorded separately.
The [practical storage/loading comparison](docs/gzip-comparison.md) covers
MPS.gz, CBLIB CBF.gz, externally gzipped raw HDF5 arrays, default OptBin and
OptBin.gz. Loading times include decompression. A separate LP with 4.44 billion
entries passes a full exact loading check. See the
[short technical report](docs/optbin-technical-report.pdf) and its
[LaTeX source](docs/technical-report.tex). GPU loading and broader large-model
coverage remain future work. The PDF is included in this repository; maintainer
release bundles also include a report copy. Benchmark methodology and
reproduction details are in [the benchmark guide](benchmarks/README.md).

## Build

64-bit little-endian Linux, a C11 compiler, a C++17 compiler, and zlib development
files are required. The current validation platform is Linux x86-64.
Zstd compression uses runtime-loaded `libzstd.so.1`; development headers are not
required. Use `--zstd 0` when that runtime is unavailable. No CUDA, HDF5, Python,
or solver library is required to build the native tools and C reader.

```bash
make -j4
make test
```

Alternatively, build and install with CMake:

```bash
cmake -S . -B build-cmake -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON
cmake --build build-cmake -j4
ctest --test-dir build-cmake --output-on-failure
cmake --install build-cmake --prefix "$PWD/install-test"
```

CMake consumers can use `find_package(OptBin CONFIG REQUIRED)` and link
`OptBin::optbin`. Make also builds the legacy `mps2bqp`, `bqp_inspect`, and
`libbqp` tools for reproducibility.

## Convert and read

```bash
build/mps2optb examples/qp.mps /tmp/qp.optb
build/mps2optb examples/qcqp.mps /tmp/qcqp.optb --mps-dialect gurobi
build/cbf2optb examples/socp.cbf /tmp/socp.optb
build/cbf2optb examples/sdp.cbf /tmp/sdp.optb
build/bqp2optb /path/existing.bqp /tmp/migrated.optb
build/optb_inspect /tmp/sdp.optb --load
build/optb_inspect /tmp/sdp.optb --stream
```

Both converters read plain or gzip-compressed input. Output is never silently
overwritten. Each accepts `--block 65536`, `--zstd 1` and
`--encoding all|raw|values|indices` (default `all`). `raw` disables the specialized
value/index encodings; Zstd remains controlled separately. The converter returns
JSON with dimensions, file size and conversion time. Conversion time includes
parsing, encoding and writing; it is not a standalone source-reader benchmark.

`--load` validates the complete model, including sparse ordering and cone/PSD/QC
relationships. `--stream` verifies framing, checksums and array coverage with
bounded scratch space, but does not perform cross-array semantic validation.
Legacy `.bqp` currently uses `--load`; streaming inspection is v2-only.

`bqp2optb` migrates legacy LP/QP models with bounded scratch space, retaining
encoded payloads and checking decoded block CRCs. Full model validation occurs
at loading. It is a representation migration, not a new MPS conversion.

### C API

```c
#include "optbin.h"

optb_model model = {0};
char error[512];
if (optb_load("model.optb", &model, 8ULL << 30, error, sizeof error)) {
    /* report error */
} else {
    const optb_section *values = optb_get(&model, OPTB_AX);
    /* values->data is double*. Check section types for integer indices. */
    optb_free(&model);
}
```

The limit is decoded array bytes, not a process RSS cap. The loader also needs
bounded codec buffers, directory metadata and allocator overhead. See the complete
[C example](examples/load_optb.c) and [public header](include/optbin.h).

```bash
cc -Iinclude examples/load_optb.c build/liboptbin.a \
   -lz -ldl -lpthread -lm -o /tmp/load_optb
/tmp/load_optb /tmp/socp.optb
```

### Optional Python and solver checks

CPython 3.10+ and Python development headers are required for a source Python
build. Pip builds a bundled native reader; NumPy and solver packages are optional.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/optbin-info /tmp/qp.optb --limit 8589934592
```

The Python distribution provides the reader and adapters. Install native
converters with CMake above. To enable the optional adapters and tests:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[solvers,benchmark,quadratic]'
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p test_solvers.py -v
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p test_quadratic_solvers.py -v
PYTHONPATH=python .venv/bin/python -m unittest discover -s tests -p test_hdf5.py -v
```

Installed packages load their bundled reader. `OPTBIN_LIBRARY` explicitly
overrides it; a source checkout without a bundled reader falls back to
`build/liboptbin.so`. No source-checkout path is needed after installation.

```python
from optbin import Model
from optbin.solvers import solve

with Model('/tmp/socp.optb', decoded_limit=8 << 30) as model:
    print(model.info)
    print(solve(model, 'scs')['objective'])
    print(solve(model, 'clarabel')['objective'])
```

NumPy results are copied by default. Explicit `model.numpy(id, copy=False)` views
are valid only until the owning model is closed. Solver adapters create their
own arrays and may expand PSD variables and cone-domain restrictions.

For native bounded LP/QP/QCQP models:

```python
with Model('/tmp/qcqp.optb') as model:
    result = solve(model, 'scip', time_limit=30.)
    print(result['status'], result['objective'], result.get('residuals'))
```

Install the `gurobi` extra and provide a valid Gurobi license to use
`solve(model, 'gurobi', TimeLimit=30., NonConvex=2)`. Gurobi uses its native
quadratic API; SCIP keeps quadratic constraints and uses an objective auxiliary
variable when needed. Check status and residuals: an incumbent at a time limit
is not an optimality result. SCS/Clarabel accept affine conic constraints and
convex objectives; native quadratic constraints require Gurobi or SCIP.

## Supported model scope

| Input | Implemented |
|---|---|
| MPS LP | Linear rows, ranges, bounds, objective sense and constant |
| MPS QP | `QUADOBJ`, symmetric `QMATRIX`, MOSEK objective `QSECTION` |
| MPS QCQP | Per-row `QCMATRIX`, MOSEK constraint `QSECTION` |
| CBF 1–3, continuous | Free/zero/positive/negative domains, SOC, rotated SOC, primal/dual exponential, generalized primal/dual power, PSD variables and constraints |
| Legacy BQP v1 | Complete native C loading with v1 validation |

MPS quadratic constraints require an explicit `--mps-dialect gurobi` or
`--mps-dialect mosek`. Tests against Gurobi 13.0.3 and MOSEK 11.2.7 readers give
`x'Qx` for `QCMATRIX`; MOSEK `QSECTION` gives `0.5*x'Qx`. The file preserves
matrix coefficients and records the expression multiplier. `QSECTION` requires
`mosek`. CPLEX QCQP scaling has not been
validated and is not a selectable dialect in this release. See
[MPS semantics](docs/mps.md).

**Converter correction:** regenerate MOSEK `QCMATRIX` files produced by
0.2.0-dev0; that converter incorrectly used multiplier 0.5. The dev1 regression
compares directly with MOSEK's source reader. The schema and existing BQP v1
files are unaffected.

MPS accepts free-format records with or without leading indentation, and
whitespace-compatible fixed MPS. Embedded spaces in fixed-width names are not
supported. Multiple named RHS/range/bound sets, integer variables, SOS,
multiobjective and unknown sections fail explicitly. Repeated arbitrary column
names after a column has been closed are rejected. Duplicate linear or quadratic
entries are summed as part of MPS parsing, with finite-result checks.

CBF integrality (`INT`), change sequences (`CHANGE`), and version 4 proposal
features are rejected. The conic reader preserves CBF coordinate conventions and
unnormalized power parameter vectors. Native SDP coefficients remain unscaled;
solver-specific `sqrt(2)` scaling and permutations occur in adapters.

Native nonconvex quadratics can be stored. They are not silently turned into
convex cone programs. SCS/Clarabel adapters reject nonconvex objective matrices
using a numerical eigenvalue check (or a sufficient diagonal-dominance check);
this is a floating-point check, not a formal convexity certificate. Dual
exponential/power cones and generalized power cones have solver-specific adapter
limits listed in [the roadmap](docs/roadmap.md).

## Storage and performance

Blocks use exact constant/integer/FP32/dictionary encodings for FP64 arrays, raw
or delta/zigzag varints for integer arrays, and optional Zstd. Coefficients decode
to FP64. This reduces file and transfer sizes, not the solver's expanded arrays.
Every block has a checksum over decoded bytes. The directory permits seeking to
an array; it does not yet index every block or provide parallel/GPU decoding.

The [practical comparison](docs/gzip-comparison.md) measures MPS.gz and
CBLIB CBF.gz against raw.h5.gz, default OptBin and OptBin.gz, using Gurobi and
MOSEK source readers. Binary times include decompression, allocation and full
native validation; independent numerical comparisons occur outside timing. Stored size and
loading time favor different choices on some models; these results do not
establish a universal compression or memory advantage.

Optional HDF5 evaluation frontends are retained for reproducibility, with
[separate archived methods](docs/native-hdf5.md). They are not dependencies of
the converters or the production reader. The current note presents the
practical gzip comparisons above.

The MPS converter streams columns into compressed temporary section spools.
The CBF converter currently sorts sparse coordinates in memory; external sorting
for models that exceed host RAM remains future work. Full-model C loading
allocates all decoded arrays. This package does not implement GPUDirect Storage.

See [the v2 byte specification](docs/format-v2.md), [validation scope](docs/validation.md),
and [planned improvements](docs/roadmap.md). The MIT license covers this software;
benchmark data retain their original attribution and license.

The [short technical report](docs/optbin-technical-report.pdf) describes installation,
model scope, validation, measured trade-offs and current limits. Regenerate it
with `python3 scripts/build_technical_report.py --render`; it writes the PDF to
`output/pdf/optbin-technical-report.pdf` and records source-report hashes in
`docs/report-evidence.json`. [Release verification](docs/release.md#build-and-verify-release-artifacts)
and [CI](.github/workflows/ci.yml) cover clean distribution/install workflows.

To contribute, see [CONTRIBUTING.md](CONTRIBUTING.md).
