# ChipForge

Layer 1 mock EDA raw artifact generator.

Run:

```bash
python scripts/run_pipeline.py --runs 100 --seed 42
```

or:

```bash
python -m chipforge --runs 100 --seed 42
```

Artifacts are written under `data/raw/runs/RUN_######/` with an S3-friendly hierarchy that can later map to `s3://chipforge/raw/runs/...`.

Layer 1 uses an internal `DesignTruth` model as the source of truth, renders raw EDA-style reports from that model, then validates mathematical and cross-report invariants. The pipeline also writes:

```text
data/raw/generation_summary.json
data/raw/data_integrity_report.json
```

`flow_status` describes whether the mock EDA flow produced artifacts. Engineering statuses such as `timing_status`, `routing_status`, `drc_status`, `antenna_status`, and `lvs_status` describe constraint quality and may overlap within the same completed run.
