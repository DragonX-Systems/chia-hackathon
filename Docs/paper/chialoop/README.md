# ChiaLoop IEEE paper draft

Title: **Adaptive ChiaLoop for Compute-Efficient Coverage Closure in Chip Verification**

Authors, in order: **Kapil Bansal; Khushal Sethi**.

`chialoop_paper.tex` is the editable manuscript. It uses the included, unmodified `IEEEtran.cls` in conference mode with US Letter paper, two columns, and Times-compatible fonts. `figures/` contains vector PDF diagrams generated with ReportLab by `build_figures.py`.

The deliverable PDF is `../../../output/pdf/chialoop_objective_aware_verification.pdf` relative to this folder. The source bundle can be uploaded to Overleaf or built with a current TeX Live installation or Tectonic:

```sh
python3 build_figures.py  # only needed after editing the diagrams; requires reportlab
tectonic chialoop_paper.tex
# Alternative: latexmk -pdf chialoop_paper.tex
```

## Scope and results

This manuscript follows `Docs/CHIALOOP_ROI_PLAN.md`, whose core study uses one MediumBOOM configuration, five planning partitions, two homogeneous compute slots, and one token per simulation/formal tool class. The different root-level ROI plan and older whitepaper are background, not the governing experimental protocol.

The paper now reports a preregistered ten-pair local MediumBOOM signal-toggle comparison, distinct from the earlier single-pair pilot and five-workload hindsight replay. The target registry contains 11,964 exact toggle-bit IDs left open after the 182-bin pilot hits were excluded. Across ten paired seeded corpora at 0.28 compute-hours per arm, Static-Hybrid averaged 93.1 new bins and Adaptive 84.6; the mean paired difference was -8.5 bins (bootstrap 95% CI [-37.7, 17], exact sign p=1.00). The normalized within-budget AUC difference was -1.46e-4 (95% CI [-7.51e-4, 7.40e-4], exact sign p=0.109). Neither arm attained 95% in any pair, so measured Compute95 is undefined. Log-linear projections are reported as model outputs only; their enormous values are not operational estimates. This is a local-runtime policy experiment, not a CHIA/Ray execution result. The legacy 51-point proxy and 66 semantic families are excluded from the endpoint.

The manuscript retains explicit protocol rules for partial in-flight costs, a completion-independent budget timer, safe target skipping only for exhaustive target sets, and scope-compatible formal evidence. The ten-pair experiment is fixed-budget; it uses no formal jobs because formal SVA/property results cannot soundly close raw toggle-bit IDs.

## Updating after experiments

1. The separate 50-pair/1,000-execution extension is running; keep its results separate from these ten pairs until its analysis is complete.
2. A CHIA/Ray deployment evaluation remains outstanding. Do not describe the local executor runs as CHIA cluster measurements.
3. Validate the extreme log-linear Compute95 projections against a defensible extrapolation range before citing them as anything other than exploratory model outputs; observed target attainment remains 0/10.
4. Confirm author affiliations, email addresses, page limit, and any venue-specific disclosure or copyright requirements before submission. The author block identifies CHIA Hackathon at A3@MICRO 2026 and links to the supplied GitHub artifact. IEEE template use is not a substitute for the selected venue's final submission checks.

## References

Bibliographic entries are embedded in the LaTeX source and cite primary sources: the CHIA preprint, IEEE AITest coverage-directed selection paper, BOOM technical report, Chipyard IEEE Micro paper, Ray OSDI paper, official riscv-dv repository, YosysHQ documentation, and Accellera UCIS specification. Sources were checked during drafting on September 22, 2026.
