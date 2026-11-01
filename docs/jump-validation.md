# Independent JuMP / MathOptInterface reader check

Tested on 27 October 2026 with Julia 1.12.6, JuMP 1.31.2 and
MathOptInterface (MOI) 1.53.0. The dependency graph is pinned in
`tests/jump/Manifest.toml`. The tested converter/reader binaries are the existing
0.2.0rc1 build; no numerical implementation change was needed for these checks.

## Results

[The full report](../benchmarks/jump-correctness.json) records:

| Cases | Result |
|---|---|
| Eight real LP/QPs | Complete canonical numerical models match JuMP/MOI |
| Five real QCQPs | Complete canonical numerical models match JuMP/MOI |
| Nine real CBFs | Complete canonical numerical models match JuMP/MOI |
| Fifteen additional fixture/configuration cases | Match, including QMATRIX, ranges, bounds, maximization, duplicate terms, exponential/dual-exponential, rotated SOC, power/dual-power and PSD operators |
| Five targeted MPS fixtures | Reviewed reader discrepancies, retained as discrepancies rather than passes |
| One four-dimensional generalized power cone | OptBin converts and fully loads it; MOI's CBF reader explicitly rejects it |

Thus all 22 real sources agree with JuMP/MOI. The extra fixture suite does not
establish agreement for every supported producer extension or every CBF cone.
No optimization was performed. The two difficult SDP examples are data
comparisons here, independently of their earlier inaccurate solver outcomes.

## What is compared

JuMP's public `read_from_file` reads each original source, with the declared
Gurobi/MOSEK quadratic format for MPS. A separate Julia process exports its MOI
model; it loads no OptBin code. Python runs a fresh conversion and uses the C
reader to load the result. Both models are converted into numerical
function-in-set representations and compared exactly after these explicit
representation changes:

- Sum duplicate terms and remove numerical zeros, treating both signs of zero
  as equal for cross-reader comparison. OptBin's separate codec tests check
  signed-zero bit preservation.
- Split scalar interval/equality domains into lower/upper conditions and move
  affine constants into the bounds.
- Ignore names, row ordering and unconstrained Reals domains. Preserve duplicate
  nontrivial constraints. This is not a comparison of physical sparse layouts.
- Convert MOI quadratic coefficients to polynomial monomials, including the
  half factor for repeated-variable terms.
- Expand PSD variables into unscaled triangular coordinates; count trace
  off-diagonal coefficients twice. No numerical PSD factorization is used.
- Apply CBF/MOI exponential-coordinate permutations and the two-weight power
  parameter normalization. These do not alter stored OptBin parameters.

Every objective term, nontrivial constraint term, bound, cone dimension and
supported cone parameter participates in the comparison. Model fingerprints
are computed after this normalization. A one-ULP change, wrong quadratic
factor, missing/duplicate constraint or changed cone parameter causes the
comparison harness's unit tests to fail.

## Reviewed differences in the pinned reference reader

These findings are specific to the tested versions. They were not resolved by
changing OptBin coefficients to make the two readers agree.

1. **MOSEK QCMATRIX scaling.** With MOI's `kQuadraticFormatMosek`, QCMATRIX
   constraints receive a half factor. The actual MOSEK 11.2.7 SDK source reader
   interprets the tested QCMATRIX as `x'Qx`, matching OptBin. Three fixtures
   exercise this discrepancy, including one that also uses a nonliteral
   objective name. A separate QCMATRIX/QUADOBJ fixture agrees when MOI's Gurobi
   quadratic option is selected; this is a configuration-specific workaround,
   not a general fix for files mixing QCMATRIX and QSECTION constraints.
2. **QSECTION objective name.** MOI recognizes a quadratic objective here only
   when the matrix name is literally `OBJ`. The actual MOSEK reader and OptBin
   also recognize the declared objective named `obj`. Two fixtures expose the
   missing objective; one overlaps with item 1. A separate uppercase-OBJ
   QSECTION fixture matches with the MOSEK option selected.
3. **Negative UP without an explicit lower bound.** OptBin follows the tested
   Gurobi convention: `UP b Y -2` gives `Y <= -2` with lower bound `-Inf`.
   Gurobi 13.0.3 confirms the complete numerical model. MOI leaves an internal
   lower bound of zero and its `bounds_to_set` then drops both bounds when
   lower exceeds upper. A separate ranges/bounds fixture with explicit `MI`
   matches. MOSEK/HiGHS implicit-bound conventions also differ from Gurobi in
   this edge case; use explicit lower bounds for portable source files.
4. **Generalized power cones.** The tested MOI CBF reader accepts two weights
   for a three-dimensional power cone and rejects the four-dimensional,
   three-weight fixture. This is an unsupported reference case, not an
   OptBin/JuMP agreement pass.

All six MOSEK-dialect fixture configurations were additionally compared through
the actual MOSEK SDK; their complete canonical models match OptBin. The negative
UP fixture additionally matches Gurobi's source reader numerically. These SDK
checks need the optional vendor packages; they do not optimize a model.

[The reviewed-issue record](../tests/jump/known-reference-issues.json) pins
package versions, source-input hashes, both canonical-model hashes and the
specific failed comparison fields. A new input or a different numerical
result is not covered by that record. Known differences remain labeled
`known_reference_discrepancy` with `pass: false`; unexpected mismatches still
fail the command. `--strict-reference` also returns nonzero for the reviewed
discrepancies.

The implementation sources used to interpret these findings are
[MOI's MPS reader](https://github.com/jump-dev/MathOptInterface.jl/blob/v1.53.0/src/FileFormats/MPS/read.jl)
and [CBF reader](https://github.com/jump-dev/MathOptInterface.jl/blob/v1.53.0/src/FileFormats/CBF/read.jl).
The JSON report retains their actual installed source SHA-256 hashes.

## Reproduce

Use Julia 1.12.6 with the pinned project. For an isolated depot:

```bash
export JULIA_DEPOT_PATH="$PWD/data/deps/julia-depot"
export JULIA_NUM_PRECOMPILE_TASKS=2
export JULIA_NUM_THREADS=2
julia --startup-file=no --project=tests/jump -e 'using Pkg; Pkg.instantiate()'
make -j4 BUILD=build-release
.venv/bin/python -m pip install numpy
.venv/bin/python scripts/validate_jump.py --julia julia --build build-release
```

The full real-data manifest uses existing local source paths. Fetch public
sources as described in [validation.md](validation.md), or provide a separate
manifest. Data are not bundled. To run only the self-contained fixtures:

```bash
.venv/bin/python scripts/validate_jump.py --julia julia --build build-release \
  --fixtures-only --work benchmarks/runs/jump-fixtures \
  --output benchmarks/jump-fixtures.json
```

`--reuse-reference` reuses a Julia export only when the exporter and every
source-input hash still match. It is useful when investigating the Python-side
comparison; it does not replace source reading on a normal run.

The CI workflow runs the self-contained fixtures with the pinned Julia version.
It does not require access to the real benchmark datasets or vendor optimization
licenses. See [GitHub Actions](https://github.com/PolyU-IOR/OptBin/actions) for
hosted results.
