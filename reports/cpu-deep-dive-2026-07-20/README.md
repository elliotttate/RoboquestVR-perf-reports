# RoboquestVR Quest 3 CPU deep dive

Open `index.html` or visit the published report:

https://elliotttate.github.io/RoboquestVR-perf-reports/reports/cpu-deep-dive-2026-07-20/

The report combines the deterministic A/B capture set, the historical
live-combat report, the supplied Claude live-combat artifact, and source review
at the recorded capture commit. It focuses on Oasis and Purification II as the
cleanest sustained CPU-bound case studies, while retaining Fields and
BuggedCity as GPU-bound counterexamples.

Published companion files:

- `artifact.json`: canonical portable-report definition.
- `derived_metrics.json`: compact headline metrics and provenance.
- `data/*.csv`: 14 evidence, quality, remediation, and capture-plan tables.

The raw 8.65 GiB capture pack is intentionally not duplicated in this Git
repository. Its original URLs and SHA-256 values remain in the source artifact
index, and the reproducible verifier lives in `../../audit/`.

The supplied artifact's headset serial was removed from every published file.
