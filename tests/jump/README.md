# JuMP/MOI independent-reader environment

`Project.toml` and `Manifest.toml` pin the dependencies used by
`scripts/jump_reference.jl`. The tested Julia runtime is 1.12.6.

This environment contains no OptBin parser or solver backend. The Julia script
reads original MPS/CBF inputs using `JuMP.read_from_file` and exports MOI functions
and sets to an ignored JSONL cache. `scripts/validate_jump.py` separately
converts/loads the source through OptBin and compares canonical numerical models.

See [the results and reproduction instructions](../../docs/jump-validation.md).
`known-reference-issues.json` documents five vendor-adjudicated differences in
the pinned reader. They remain discrepancies, not agreement passes. Recognition
requires exact input/model hashes and versions; unexpected differences fail.
Use `--strict-reference` to fail for the reviewed cases too.
