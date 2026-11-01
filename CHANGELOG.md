# Changelog

## Unreleased

- Simplify installation, supported-scope and validation documentation.
- Document the scalable font dependencies required to rebuild the note.

## 0.2.0rc4 - 2026-10-30

Experimental release candidate for 64-bit little-endian Linux x86-64.
The v2 wire schema and C ABI are not frozen.

- Convert continuous MPS LP/QP/QCQP and CBF 1-3 models within documented
  subsets. Preserve existing BQP v1 compatibility and bounded migration.
- Store lossless numerical blocks with exact value/index encodings and
  optional Zstandard compression. Provide a validating C11 reader, block
  traversal, inspector, Python bindings and optional solver adapters.
- Provide Make/CMake builds, relocatable native installation, an external
  CMake consumer and Python source/wheel builds with a bundled reader.
- Compare complete numerical models independently with Gurobi 13.0.3,
  MOSEK 11.2.7, the original CBLIB parser and pinned JuMP/MOI readers.
  Known reference discrepancies remain documented separately from passes.
- Include gzip-inclusive source-reader and binary-loading benchmarks for
  22 smaller real models and two large LPs, with explicit endpoint and
  memory qualifications. Verify a separate model beyond 2^32 entries.
- Reject negative/overflowing decoded-byte budgets and embedded null bytes
  in paths before entering the C loader.
- Exercise Python 3.10/3.12/3.14 builds and installation, optional frontends,
  public reader contracts, JuMP fixtures, UBSan and report compilation in CI.

### Compatibility notes

Development build 0.2.0-dev0 assigned an incorrect 0.5 multiplier to MOSEK
`QCMATRIX`; regenerate those affected conversions. The corrected convention,
`x'Qx`, was checked against MOSEK 11.2.7 and Gurobi 13.0.3. MOSEK `QSECTION`
uses `0.5*x'Qx`. Existing BQP v1 files and the v2 schema are unaffected.

Saved measurements retain the actual development-binary hashes. Packaging
changes are not new performance experiments. There is no v2 GPU adapter or
direct storage-to-GPU implementation; complete loading expands coefficients
to binary64 arrays.
