# Contributing to OptBin

The supported release scope is continuous LP/QP/QCQP MPS and continuous
CBF 1-3 on 64-bit little-endian Linux. The wire schema and C ABI remain
experimental. Start with the [installation instructions](docs/release.md),
[format specification](docs/format-v2.md) and [validation scope](docs/validation.md).

Build and run the dependency-light core checks with:

```bash
make -j4 test
```

For a reader or converter bug, provide a small source file or a file-generating
script, the exact command and MPS dialect, expected numerical behavior, actual
error/output, OptBin version, platform and compiler. Include the source SHA-256
when reporting a public benchmark. Do not attach models you cannot redistribute.
Incorrect coefficients, dropped constraints and incorrect feature acceptance
are bugs even when a solver happens to return a similar objective.

Changes to parsers, codecs or validation should include a regression that would
fail before the correction. Compare complete numerical models and expression
conventions, not just solver objectives. Keep known reference-reader differences
explicit. New model features need a documented schema and an independent source
reader check before support is claimed. Do not silently reinterpret an existing
file after a converter correction.

Use the existing Make/CMake builds. Optional solver/HDF5 and pinned JuMP checks
are described in the validation documentation and CI workflow. Report changes
must preserve source-report hashes and distinguish conversion, complete loading
and solver setup. Run `python3 scripts/build_technical_report.py --render` after
updating the note, then inspect the rendered pages.

Maintainers build source, wheel, report and installation evidence from a clean
commit with `scripts/make_release.py`. Use a new version and immutable tag for
each candidate; keep prior artifacts intact. A hosted release should identify
the exact tested commit and include the report and checksum manifest.

Project contributions use the repository's [MIT license](LICENSE). External
libraries and benchmark collections retain their own licenses and attribution.
