# OptBin 0.2.0rc4: installation and release scope

This is an experimental release candidate for 64-bit little-endian Linux,
validated on x86-64. It supports the documented continuous MPS/CBF subsets.
The v2 wire format and C ABI are not frozen. Existing BQP v1 files remain readable.
Source and documentation are available from
[PolyU-IOR/OptBin](https://github.com/PolyU-IOR/OptBin). Repository publication
does not imply a package-index upload. Benchmark files retain their original
licenses and are not bundled.

## Native converters and C library

Install a C11/C++17 toolchain, CMake 3.16+ and zlib development files. On Debian
or Ubuntu the packages are `build-essential cmake zlib1g-dev`. The optional
runtime `libzstd.so.1` enables Zstd blocks (`libzstd1` on Debian/Ubuntu). Reading
an existing Zstd-compressed model requires that runtime; converters can write
uncompressed blocks with `--zstd 0`. HDF5, Python and solver SDKs are optional.

From the unpacked source distribution:

```bash
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=ON
cmake --build build -j4
ctest --test-dir build --output-on-failure
cmake --install build --prefix "$HOME/.local/optbin"
export PATH="$HOME/.local/optbin/bin:$PATH"

mps2optb examples/qp.mps /tmp/qp.optb
mps2optb examples/qcqp.mps /tmp/qcqp.optb --mps-dialect gurobi
cbf2optb examples/socp.cbf /tmp/socp.optb
optb_inspect /tmp/qp.optb --load
```

Use new output paths on repeat runs: conversion never overwrites files.
Add `--limit BYTES` to `optb_inspect` to bound decoded arrays. The default is
unlimited. This is not a cap on process RSS. Complete loading allocates expanded
binary64 arrays. `--stream` checks framing/CRCs/coverage using bounded scratch;
it does not establish cross-array model validity.

C consumers can use the installed header/library directly or CMake:

```bash
cmake -S examples/consumer -B build-consumer \
  -DCMAKE_PREFIX_PATH="$HOME/.local/optbin"
cmake --build build-consumer
build-consumer/consumer /tmp/qp.optb
```

Installed tools find the installed shared library relative to their own path;
the prefix can be relocated. This release does not promise a stable C ABI.
Static builds use `-DBUILD_SHARED_LIBS=OFF`.

## Python reader and optional adapters

Use CPython 3.10+ with its development headers (for example `python3-dev`), a C
compiler and zlib development files when building from source:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/optbin-info /tmp/qp.optb --limit 8589934592
```

Pip builds a bundled C reader; the installed Python package works outside the
source checkout without `OPTBIN_LIBRARY`. It does not install the standalone
converters or CMake development library; install those using CMake above.
`optbin-info` performs full loading/validation. `python -m optbin` is equivalent.

```python
from optbin import Model

with Model('/tmp/qp.optb', decoded_limit=8 << 30) as model:
    print(model.info)
    coefficients = model.raw(4)  # objective coefficients as binary64 bytes
```

The base reader has no NumPy/SciPy dependency. Install `.[solvers]` for
SCS/Clarabel, `.[quadratic]` for SCIP, `.[gurobi]` for Gurobi, or `.[benchmark]`
for the Python HDF5 experiment. Commercial solvers retain their license
requirements. Adapter cone coverage is narrower than storage coverage; see
[supported scope](../README.md#supported-model-scope).
The `.[mosek]` extra enables the independent CBF reference/benchmark helper;
it does not add a production MOSEK solver adapter.

`OPTBIN_LIBRARY=/absolute/path/liboptbin.so` explicitly selects another C reader.
For a checkout with `make`, the Python binding also has a `build/liboptbin.so`
fallback. Installed packages select their bundled reader first. Explicit
`numpy(..., copy=False)` views must not outlive their owning model.

A wheel built here uses the CPython limited API and a Linux platform tag.
It is a local-platform artifact, not a manylinux or cross-distribution binary
portability guarantee. Prefer the source distribution on another Linux host.
Free-threaded CPython, Windows and macOS are outside the validated scope.

## Build and verify release artifacts

For a complete maintainer bundle, commit the source and run:

```bash
.venv/bin/python -m pip install build twine
.venv/bin/python scripts/make_release.py
```

This requires `pdflatex`, scalable Computer Modern fonts and Poppler for the
short report. On Debian/Ubuntu, install `texlive-latex-extra`,
`texlive-fonts-recommended`, `cm-super` and `poppler-utils`.
It exports the clean Git
commit to a temporary folder, builds the distributions, verifies both installs,
compiles the report, and records the commit, artifact hashes and local check
results. The bundle is `output/releases/optbin-0.2.0rc4-bundle.tar.gz`, with a
separate checksum. Its contents include `SHA256SUMS`, source, a local-platform
wheel, report, license and verification logs. Output directories are exclusive;
use `--output /new/path` for a distinct build, without overwriting a candidate.
The command creates no remote repository and publishes nothing.

To build and check the Python distributions without the PDF dependency:

```bash
python3 -m venv .venv-release
.venv-release/bin/python -m pip install build twine
.venv-release/bin/python -m build
.venv-release/bin/python -m twine check dist/*
.venv-release/bin/python scripts/check_install.py \
  --sdist dist/optbin-0.2.0rc4.tar.gz \
  --wheel dist/optbin-0.2.0rc4-cp310-abi3-linux_x86_64.whl \
  --output output/release/install-check.json
```

The wheel name varies with platform. The build creates the wheel from the
source distribution. The checker extracts that source into a temporary folder,
builds/tests/installs CMake tools, relocates the prefix, disables the original
build location, and builds an external `find_package(OptBin)` consumer. It then
installs both artifacts into separate fresh virtual environments and checks
known arrays, full validation, budgets, version output and error handling from
outside the checkout. Source installation needs access to Python build
dependencies; wheel installation uses `--no-index --no-deps`.

The report includes archive hashes and command exit codes. Logs and build
outputs are ignored by Git. Source archives contain code, small examples,
documentation and numerical benchmark reports, without large models, source
caches, solver SDKs or the built HDF5 dependency. MIT covers this project's code;
external libraries and datasets retain their own terms.

## CI and release policy

[The CI workflow](../.github/workflows/ci.yml) checks core tests and source/wheel
installation on CPython 3.10, 3.12 and 3.14. Separate jobs cover optional
open-source solver/HDF5 frontends, public Gurobi and MOSEK reader contracts,
pinned JuMP fixtures, core UBSan checks and rebuilding the technical note.
Optional MOSEK tests skip when its SDK is absent; Gurobi optimization requires
an installed license.
The [hosted workflow](https://github.com/PolyU-IOR/OptBin/actions/workflows/ci.yml)
runs on pushes and pull requests. Its status records hosted results; local checks
do not establish that the hosted matrix has passed.

This candidate contains no automatic publishing workflow. Keep source, report,
checksum manifests and validation outputs together when distributing it. Use
immutable version tags for candidates. Future incompatible schema/ABI changes
must have explicit migration and compatibility notes.

## Data and technical report

Benchmark datasets are obtained separately from their source collections.
[The large-LP manifest](../benchmarks/large-inputs.json) records model sizes and
verified hashes. Paths in saved reports are anonymized placeholders, not
download links. Large datasets and commercial solver SDKs are not bundled.

[The short technical report](optbin-technical-report.pdf)
([LaTeX source](technical-report.tex)) describes the implemented
tool, verification and gzip-inclusive comparisons with MPS/CBF text and raw
HDF5 arrays. MPS uses Gurobi's public reader and CBF uses MOSEK's; the note
qualifies the difference between their solver models and native arrays. Its
results do not establish a new compression algorithm, general HDF5 superiority,
GPU capability or faster solver iterations.
