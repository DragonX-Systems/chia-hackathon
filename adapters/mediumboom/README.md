# MediumBOOM adapter

MediumBOOM-specific verification code and collateral live here:

- Python modules implement the historical coverage model and engine backends.
- `stimulus/` contains constrained-random generator configuration.
- `testbenches/` contains Verilator harnesses.
- `rtl/` contains formal wrappers and properties.
- `scripts/` installs or generates MediumBOOM-specific prerequisites.

The reusable Chialoop is in `src/chialoop`. Experiment selection and frozen
inputs belong in `experiments/coverage_growth`; generated results belong in
`runs`.
