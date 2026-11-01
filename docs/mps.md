# MPS quadratic conventions

The objective is `c0 + c'x + 0.5*x'Q*x`, where Q is symmetric. Only one triangle
is stored in OptBin. `QUADOBJ` and MOSEK `QSECTION` must provide one consistent
triangle. `QMATRIX` and `QCMATRIX` must provide matching symmetric entries; the
converter checks both sides before retaining one.

Quadratic constraint rows use the row's existing linear coefficients and bounds:

`row_lower[i] <= A[i,:]*x + scale[k]*x'Q[k]*x <= row_upper[i]`.

The `qc_row` array associates k with i. `qc_scale` is 1 for `QCMATRIX` in the
tested Gurobi 13.0.3 and MOSEK 11.2.7 readers, and 0.5 for MOSEK `QSECTION`.
The input matrix coefficients are retained
without multiplication by two. No PSD factorization, SOC reformulation, variable
reordering, or convexity assertion occurs during conversion.

For example, symmetric entries Q00=2, Q01=Q10=3, Q11=4 represent:

- `QCMATRIX` in both tested readers: `2*x0^2 + 6*x0*x1 + 4*x1^2`.
- MOSEK `QSECTION`: `x0^2 + 3*x0*x1 + 2*x1^2`.

An explicit dialect remains required to declare the supported producer and
section vocabulary. We have not validated CPLEX QCQP conventions. Conventions
are verified against actual vendor readers, rather than inferred from a section
name or assumed identical across all producer versions.

`tests/test_quadratic_solvers.py::test_vendor_mosek_source_reader` reads both
section forms through MOSEK's SDK and compares the resulting polynomials. Five
real QCQPs also match Gurobi's source reader coefficient by coefficient. Reading
through the MOSEK SDK for this test requires no optimization license.

**Development correction:** 0.2.0-dev0 incorrectly assigned multiplier 0.5 to
MOSEK `QCMATRIX`. Regenerate those converted files with dev1. Their stored
multiplier cannot be silently reinterpreted by the reader, because multipliers
are part of the numerical model. The wire schema and legacy BQP files did not
change.

Producer references (runtime regressions define the tested behavior above):

- [Gurobi MPS reference](https://docs.gurobi.com/projects/optimizer/en/current/reference/fileformats/modelformats.html)
- [MOSEK MPS reference](https://docs.mosek.com/latest/cxxfusion/mps-format.html)

The current CBF converter preserves affine conic data. Converting a general QCQP
to CBF is outside the storage conversion path. A future optional reformulation
tool must check convexity, document numerical transformations and expansion, and
maintain a map back to the original variables/rows.
