from __future__ import annotations

import json
from statistics import mean, median, pstdev
from typing import Any, Callable

from .models import DesignRun
from .validator import validate_runs

PARAMETERS = [
    "clock_period_ns",
    "target_frequency_mhz",
    "core_utilization_pct",
    "placement_density_pct",
    "synthesis_effort",
    "placement_effort",
    "routing_effort",
    "buffering_effort",
    "core_aspect_ratio",
    "die_area_target",
]


def calibration_report(runs: list[DesignRun], runs_root) -> dict[str, Any]:
    summary = validate_runs(runs, runs_root)
    return {
        "summary": summary,
        "outcomes": _outcomes(runs),
        "timing": {
            "wns": _stats([run.timing["wns"] for run in runs]),
            "hold_wns": _stats([run.timing["hold_wns"] for run in runs]),
            "pass_rate_by_clock_period": _bucket_rates(
                runs,
                lambda run: run.configuration["clock_period_ns"],
                [(0, 1.2, "<1.2 ns"), (1.2, 1.6, "1.2-1.6 ns"), (1.6, 2.0, "1.6-2.0 ns"), (2.0, 2.6, "2.0-2.6 ns"), (2.6, 10, ">2.6 ns")],
                lambda run: run.statuses["timing_status"] == "PASS",
                lambda run: run.timing["wns"],
            ),
        },
        "routing": {
            "congestion": _stats([run.placement["routing_congestion_pct"] for run in runs]),
            "overflow": _stats([run.routing["routing_overflow"] for run in runs]),
            "pass_rate_by_utilization": _bucket_rates(
                runs,
                lambda run: run.configuration["core_utilization_pct"],
                [(0, 55, "<55%"), (55, 65, "55-65%"), (65, 72, "65-72%"), (72, 78, "72-78%"), (78, 101, ">78%")],
                lambda run: run.statuses["routing_status"] == "PASS",
                lambda run: run.placement["routing_congestion_pct"],
            ),
        },
        "parameters": {param: _stats([run.configuration[param] for run in runs]) for param in PARAMETERS},
        "by_design": _by_design(runs),
        "by_sample_region": _by_sample_region(runs),
    }


def write_calibration_reports(report: dict[str, Any], json_path, text_path) -> None:
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    text_path.write_text(format_calibration_report(report), encoding="utf-8")


def format_calibration_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "ChipForge Layer 1 Calibration Report",
        "====================================",
        "",
        f"Runs analyzed:          {summary['runs']}",
        f"Runs completed:         {summary['runs_completed']}",
        f"Artifact-valid:         {summary['artifact_valid_runs']}",
        f"Integrity errors:       {summary['integrity_errors']}",
        "",
        "Outcome distribution",
        "--------------------",
        f"Clean:                  {summary['clean_runs']} ({_pct(summary['clean_runs'], summary['runs'])}%)",
        f"Constraint violation:   {summary['runs_with_violations']} ({_pct(summary['runs_with_violations'], summary['runs'])}%)",
        "",
        "Constraint violations",
        "---------------------",
        f"Timing:                 {summary['timing_violations']} ({_pct(summary['timing_violations'], summary['runs'])}%)",
        f"Routing:                {summary['routing_violations']} ({_pct(summary['routing_violations'], summary['runs'])}%)",
        f"DRC:                    {summary['drc_violations']} ({_pct(summary['drc_violations'], summary['runs'])}%)",
        f"Antenna:                {summary['antenna_violations']} ({_pct(summary['antenna_violations'], summary['runs'])}%)",
        f"LVS:                    {summary['lvs_violations']} ({_pct(summary['lvs_violations'], summary['runs'])}%)",
        "",
        "Timing",
        "------",
        _format_stats("WNS", report["timing"]["wns"]),
        _format_stats("Hold WNS", report["timing"]["hold_wns"]),
        "",
        "Timing pass rate by clock-period bucket",
        "----------------------------------------",
    ]
    lines.extend(_format_buckets(report["timing"]["pass_rate_by_clock_period"], "median WNS"))
    lines.extend(["", "Routing", "-------", _format_stats("Congestion", report["routing"]["congestion"]), _format_stats("Overflow", report["routing"]["overflow"]), "", "Routing pass rate by utilization bucket", "---------------------------------------"])
    lines.extend(_format_buckets(report["routing"]["pass_rate_by_utilization"], "median congestion"))
    lines.extend(["", "Parameter distributions", "-----------------------"])
    for param, values in report["parameters"].items():
        lines.append(_format_stats(param, values))
    lines.extend(["", "By design", "---------"])
    for design, row in report["by_design"].items():
        lines.append(f"{design}: clean={row['clean_rate_pct']}%, timing_fail={row['timing_fail_rate_pct']}%, routing_fail={row['routing_fail_rate_pct']}%, drc_fail={row['drc_fail_rate_pct']}%, antenna_fail={row['antenna_fail_rate_pct']}%, lvs_fail={row['lvs_fail_rate_pct']}%")
    lines.extend(["", "By sample region", "----------------"])
    for region, row in report["by_sample_region"].items():
        lines.append(f"{region}: runs={row['runs']}, clean={row['clean_rate_pct']}%, timing_fail={row['timing_fail_rate_pct']}%, routing_fail={row['routing_fail_rate_pct']}%")
    return "\n".join(lines) + "\n"


def _outcomes(runs: list[DesignRun]) -> dict[str, int]:
    return {
        "clean": sum(run.statuses["overall_status"] == "CLEAN" for run in runs),
        "constraint_violation": sum(run.statuses["overall_status"] == "CONSTRAINT_VIOLATION" for run in runs),
        "flow_failed": sum(run.statuses["overall_status"] == "FLOW_FAILED" for run in runs),
    }


def _by_design(runs: list[DesignRun]) -> dict[str, Any]:
    return {design: _group_row([run for run in runs if run.design.name == design]) for design in sorted({run.design.name for run in runs})}


def _by_sample_region(runs: list[DesignRun]) -> dict[str, Any]:
    return {region: _group_row([run for run in runs if run.configuration["sample_region"] == region]) for region in ["EASY", "MODERATE", "AGGRESSIVE"]}


def _group_row(rows: list[DesignRun]) -> dict[str, Any]:
    total = len(rows)
    return {
        "runs": total,
        "clean_rate_pct": _pct(sum(run.statuses["overall_status"] == "CLEAN" for run in rows), total),
        "timing_fail_rate_pct": _pct(sum(run.statuses["timing_status"] == "FAIL" for run in rows), total),
        "routing_fail_rate_pct": _pct(sum(run.statuses["routing_status"] == "FAIL" for run in rows), total),
        "drc_fail_rate_pct": _pct(sum(run.statuses["drc_status"] == "FAIL" for run in rows), total),
        "antenna_fail_rate_pct": _pct(sum(run.statuses["antenna_status"] == "FAIL" for run in rows), total),
        "lvs_fail_rate_pct": _pct(sum(run.statuses["lvs_status"] == "FAIL" for run in rows), total),
    }


def _bucket_rates(runs: list[DesignRun], value_fn: Callable[[DesignRun], float], buckets: list[tuple[float, float, str]], pass_fn: Callable[[DesignRun], bool], metric_fn: Callable[[DesignRun], float]) -> list[dict[str, Any]]:
    rows = []
    for lower, upper, label in buckets:
        bucket = [run for run in runs if lower <= value_fn(run) < upper]
        rows.append({"bucket": label, "runs": len(bucket), "pass_rate_pct": _pct(sum(pass_fn(run) for run in bucket), len(bucket)), "metric_median": round(median([metric_fn(run) for run in bucket]), 3) if bucket else None})
    return rows


def _stats(values: list[float]) -> dict[str, float]:
    sorted_values = sorted(values)
    return {
        "min": round(min(sorted_values), 3),
        "p10": round(_percentile(sorted_values, 10), 3),
        "p25": round(_percentile(sorted_values, 25), 3),
        "mean": round(mean(sorted_values), 3),
        "median": round(median(sorted_values), 3),
        "p75": round(_percentile(sorted_values, 75), 3),
        "p90": round(_percentile(sorted_values, 90), 3),
        "max": round(max(sorted_values), 3),
        "std": round(pstdev(sorted_values), 3),
    }


def _percentile(sorted_values: list[float], percentile: float) -> float:
    index = (len(sorted_values) - 1) * percentile / 100
    lower = int(index)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (index - lower)


def _pct(count: int, total: int) -> float:
    return round(100 * count / total, 1) if total else 0.0


def _format_stats(name: str, values: dict[str, float]) -> str:
    return f"{name}: min={values['min']}, p10={values['p10']}, median={values['median']}, p90={values['p90']}, max={values['max']}"


def _format_buckets(rows: list[dict[str, Any]], metric_label: str) -> list[str]:
    return [f"{row['bucket']}: runs={row['runs']}, pass={row['pass_rate_pct']}%, {metric_label}={row['metric_median']}" for row in rows]
