# RoboquestVR performance-report data audit

This directory contains a reproducible, standard-library-only audit of the
downloaded performance-report artifacts. The backing report repository is at
commit `dfd39f79350d1b2355f75e95425eaf098aaa20ef`.

Run the full integrity and link audit from any directory:

```powershell
python audit/audit_perf_data.py --verify-sha --check-combat-links
```

The script reads the immutable artifact index in `../downloads`, hashes every
indexed artifact (about 9.29 GB), parses the current deterministic-spot JSON,
and extracts the historical combat tables and embedded series from
`../reports/combat-2026-07-19/index.html`.

Outputs in `out/`:

- `audit_digest.json`: compact integrity and coverage summary.
- `artifact_inventory.csv`: URL, expected/actual size, and SHA-256 per asset.
- `normal_spots.csv`: one row per deterministic A/B spot, including CPU thread
  occupancy, active GameThread estimate, GPU, stage, and RenderDoc fields.
- `normal_level_cpu_rankings.csv`: per-level maxima for reported process
  occupancy and active GameThread time.
- `combat_levels.csv`: combat levels ranked by active GameThread time.
- `combat_timer_breakdown.csv`: inclusive Insights timer aggregates by level.
- `combat_series_inventory.csv`: embedded-series coverage, scale-factor branch
  diagnostics, and duplicate-event counts.
- `combat_trace_link_status.csv`: HTTP status for the historical raw `.utrace`
  links.

Interpretation cautions:

- The current deterministic-spot run is not CPU-frame-bound: the highest
  estimated active GameThread value is 5.67 ms. Its `process_pct` is the sum of
  only the 20 highest Perfetto thread-name groups and can exceed 100% because it
  represents concurrent core-time.
- The historical combat run is CPU-frame-bound, but its 12 raw `.utrace` links
  currently return HTTP 404. Only published aggregate timers and downsampled
  series can be independently audited.
- Combat VrApi data for City, Energy_Electrical, and Mines mixes distinct
  same-second scale-factor branches. Combat event payloads also contain exact
  duplicate rows in 11 of 12 levels.
