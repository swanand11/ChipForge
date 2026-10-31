from __future__ import annotations

import argparse
import json
from pathlib import Path

from .calibration import calibration_report, write_calibration_reports
from .generator import generate_runs
from .models import DESIGNS
from .validator import validate_runs
from .silver.processing import main as silver_main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate ChipForge Layer 1 raw mock EDA artifacts.")
    parser.add_argument("--runs", type=int, default=10, help="number of runs to generate")
    parser.add_argument("--seed", type=int, default=None, help="random seed")
    parser.add_argument("--output", type=Path, default=Path("data/raw"), help="raw output root")
    parser.add_argument("--calibration-report", action="store_true", help="write calibration report files for distribution diagnostics")
    parser.add_argument("--silver", action="store_true", help="process existing raw artifacts to Silver Parquet")
    parser.add_argument("--silver-output", default="data/silver", help="Silver Parquet output URI (supports s3a://)")
    parser.add_argument("--overwrite-silver", action="store_true", help="replace existing Silver run data")
    parser.add_argument("--spark-log-level", default="WARN", help="Spark log level for Silver processing")
    args = parser.parse_args(argv)
    if args.silver:
        silver_args = ["--input", str(args.output / "runs"), "--output", args.silver_output]
        if args.overwrite_silver:
            silver_args.append("--overwrite")
        silver_args.extend(["--spark-log-level", args.spark_log_level])
        return silver_main(silver_args)
    if args.runs <= 0:
        parser.error("--runs must be positive")

    print("ChipForge Layer 1")
    print("=================")
    print(f"\nGenerating {args.runs} design runs...\n")
    print("Designs:")
    for design in DESIGNS:
        print(f"  {design.name}")

    print("\nGenerating EDA artifacts...\n")
    runs = generate_runs(args.runs, args.seed, args.output)
    for stage in ["Synthesis", "Floorplan", "Placement", "CTS", "Timing", "Power", "Routing", "Signoff"]:
        print(f"{stage:<15} ok")

    print("\nValidating artifacts...\n")
    summary = validate_runs(runs, args.output / "runs")
    summary_path = args.output / "generation_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary | {"seed": args.seed, "output": str(args.output / "runs")}, indent=2) + "\n", encoding="utf-8")
    integrity_path = args.output / "data_integrity_report.json"
    integrity_report = {
        "seed": args.seed,
        "output": str(args.output / "runs"),
        "truth_model": "DesignTruth",
        "checks": {
            "artifacts_exist": summary["integrity_errors"] == 0,
            "manifests_valid": summary["integrity_errors"] == 0,
            "mathematical_invariants_pass": summary["integrity_errors"] == 0,
            "cross_report_invariants_pass": summary["integrity_errors"] == 0,
            "deterministic_with_seed": args.seed is not None,
        },
        "summary": summary,
    }
    integrity_path.write_text(json.dumps(integrity_report, indent=2) + "\n", encoding="utf-8")
    calibration_text_path = args.output / "calibration_report.txt"
    if args.calibration_report:
        report = calibration_report(runs, args.output / "runs")
        write_calibration_reports(report, args.output / "calibration_report.json", calibration_text_path)

    print(f"Runs generated:             {summary['runs']}")
    print(f"Runs completed:             {summary['runs_completed']}")
    print(f"Flow failures:              {summary['flow_failures']}")
    print(f"Artifact-valid runs:        {summary['artifact_valid_runs']}")
    print(f"Integrity errors:           {summary['integrity_errors']}")
    print("\nEngineering results:")
    print(f"  Clean runs:               {summary['clean_runs']}")
    print(f"  Runs with violations:     {summary['runs_with_violations']}")
    print("\nConstraint violations:")
    print(f"  Timing:                   {summary['timing_violations']}")
    print(f"  Routing:                  {summary['routing_violations']}")
    print(f"  DRC:                      {summary['drc_violations']}")
    print(f"  Antenna:                  {summary['antenna_violations']}")
    print(f"  LVS:                      {summary['lvs_violations']}")
    print("\nRaw artifacts written to:\n")
    print(f"{args.output / 'runs'}/")
    print(f"\nData integrity report:\n\n{integrity_path}")
    if args.calibration_report:
        print(f"\nCalibration report:\n\n{calibration_text_path}")
    print("\nGeneration complete.")
    return 0
