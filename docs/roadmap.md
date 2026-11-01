# Scope and planned improvements

OptBin is an experimental tool for storing and loading continuous optimization
models. The [README](../README.md#supported-model-scope) lists the supported
input profiles; [validation.md](validation.md) describes the checks and known
reference-reader differences. The wire schema and C ABI are not frozen.

## Implemented

- MPS LP/QP/QCQP and continuous CBF 1-3 converters with explicit rejection of
  unsupported features and producer-specific MPS quadratic conventions.
- Lossless numerical blocks, checksums, full native C validation, bounded block
  traversal, Python bindings and legacy BQP v1 compatibility.
- Make/CMake installation, an external C consumer, bundled Python wheels and
  source distributions, and automated installation checks.
- Independent Gurobi, MOSEK, CBLIB and JuMP/MOI model comparisons; solver
  adapters and analytical fixtures; documented storage/loading measurements.

## Solver adapter limits

Storage coverage is broader than adapter coverage. Native Gurobi/SCIP adapters
handle bounded LP/QP/QCQP models. SCS/Clarabel require supported affine conic
constraints and convex objectives; native quadratic constraints are rejected.

SCS requires power cones to have two weights and dimension three. Clarabel
supports generalized primal power cones, but currently rejects dual exponential
and dual power cones. PSD coordinate scaling and permutations are performed by
adapters. Numerical convexity checks are not formal certificates.

The current infeasibility diagnostics check necessary linear conditions.
Complete original-coordinate dual-cone membership and certificate validation
remain future work. Solver status and residuals must be inspected separately
from agreement of the input models.

## Planned improvements

- External sorting for CBF models that exceed host memory.
- Broader large QP/QCQP/PSD and infeasible/unbounded model coverage.
- Additional MPS producer/version regressions; CPLEX QCQP conventions require
  independent validation before support is added.
- Indexed blocks, parallel decoding and solver-selected sparse output layouts.
- A v2 GPU adapter, followed by separately measured device decoding and I/O.
- Broader platform, endianness and long-running fuzz validation.

These capabilities are not implemented by the current release. Full loading
allocates expanded numerical arrays; compact stored files do not imply smaller
solver working memory. Measured trade-offs and counterexamples are retained in
[the technical note](optbin-technical-report.pdf) and
[the loading comparison](gzip-comparison.md).
