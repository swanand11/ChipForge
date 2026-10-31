from __future__ import annotations

import json
from pathlib import Path

from .generator import ARTIFACTS, ROUTING_OVERFLOW_FAIL_THRESHOLD
from .models import DesignRun


def validate_runs(runs: list[DesignRun], runs_root: Path) -> dict[str, int]:
    summary = {
        "runs": len(runs),
        "artifact_valid_runs": 0,
        "runs_completed": 0,
        "flow_failures": 0,
        "clean_runs": 0,
        "runs_with_violations": 0,
        "timing_violations": 0,
        "routing_violations": 0,
        "drc_violations": 0,
        "antenna_violations": 0,
        "lvs_violations": 0,
    }
    seen = set()
    for run in runs:
        errors = validate_run(run, runs_root / run.run_id)
        if not errors:
            summary["artifact_valid_runs"] += 1
        summary["runs_completed"] += int(run.statuses["flow_status"] == "COMPLETED")
        summary["flow_failures"] += int(run.statuses["flow_status"] == "FAILED")
        summary["clean_runs"] += int(run.statuses["overall_status"] == "CLEAN")
        summary["runs_with_violations"] += int(run.statuses["overall_status"] == "CONSTRAINT_VIOLATION")
        summary["timing_violations"] += int(run.statuses["timing_status"] == "FAIL")
        summary["routing_violations"] += int(run.statuses["routing_status"] == "FAIL")
        summary["drc_violations"] += int(run.statuses["drc_status"] == "FAIL")
        summary["antenna_violations"] += int(run.statuses["antenna_status"] == "FAIL")
        summary["lvs_violations"] += int(run.statuses["lvs_status"] == "FAIL")
        if run.run_id in seen:
            raise ValueError(f"duplicate run id {run.run_id}")
        seen.add(run.run_id)
    summary["integrity_errors"] = summary["runs"] - summary["artifact_valid_runs"]
    return summary


def validate_run(run: DesignRun, run_dir: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = run_dir / "manifest.json"
    required = ["manifest.json"] + [path for path, *_ in ARTIFACTS]
    for rel in required:
        if not (run_dir / rel).exists():
            errors.append(f"missing {rel}")
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest["run_id"] != run.run_id or manifest["design_id"] != run.design.design_id:
            errors.append("manifest ids do not match run")
        if manifest.get("statuses") != run.statuses:
            errors.append("manifest statuses do not match run")
        for artifact in manifest["artifacts"]:
            if not (run_dir / artifact["path"]).exists():
                errors.append(f"manifest references missing {artifact['path']}")
    expected_statuses = {
        "flow_status": "COMPLETED",
        "timing_status": "FAIL" if run.timing["wns"] < 0 or run.timing["hold_wns"] < 0 else "PASS",
        "routing_status": "FAIL" if run.routing["routing_overflow"] >= ROUTING_OVERFLOW_FAIL_THRESHOLD or run.routing["unrouted_net_count"] > 0 else "PASS",
        "drc_status": "FAIL" if run.signoff["drc_violations"] else "PASS",
        "antenna_status": "FAIL" if run.signoff["antenna_violations"] else "PASS",
        "lvs_status": run.signoff["lvs_status"],
    }
    expected_overall = "CONSTRAINT_VIOLATION" if any(value == "FAIL" for key, value in expected_statuses.items() if key != "flow_status") else "CLEAN"
    if any(run.statuses.get(key) != value for key, value in expected_statuses.items()):
        errors.append("run statuses do not match generated metrics")
    if run.statuses.get("overall_status") != expected_overall:
        errors.append("overall status does not match generated statuses")
    if sum(run.synthesis["cell_counts"].values()) != run.synthesis["total_cells"]:
        errors.append("cell counts do not sum to total_cells")
    if run.synthesis["combinational_cells"] + run.synthesis["sequential_cells"] + run.synthesis["buffer_cells"] != run.synthesis["total_cells"]:
        errors.append("cell category counts do not sum to total_cells")
    area_parts = round(run.synthesis["combinational_area"] + run.synthesis["sequential_area"] + run.synthesis["buffer_area"], 2)
    if abs(area_parts - run.synthesis["cell_area"]) > 0.02:
        errors.append("area components do not sum to cell_area")
    if abs(run.floorplan["core_width"] * run.floorplan["core_height"] - run.floorplan["core_area"]) > max(1.0, run.floorplan["core_area"] * 0.001):
        errors.append("core dimensions do not reconcile with core_area")
    if abs(run.floorplan["die_width"] * run.floorplan["die_height"] - run.floorplan["die_area"]) > max(1.0, run.floorplan["die_area"] * 0.001):
        errors.append("die dimensions do not reconcile with die_area")
    expected_utilization = round(100 * run.synthesis["total_area"] / run.floorplan["core_area"], 2)
    if abs(expected_utilization - run.placement["placement_utilization"]) > 0.02:
        errors.append("placement utilization does not match occupied area over core area")
    if any(v < 0 for v in [run.synthesis["total_cells"], run.synthesis["total_area"], run.floorplan["core_area"], run.routing["wire_length"]]):
        errors.append("negative impossible metric")
    if run.placement["placement_utilization"] > 100 or run.floorplan["core_utilization"] > 100:
        errors.append("utilization above 100")
    total_power = round(run.power["total_internal_power"] + run.power["total_switching_power"] + run.power["total_leakage_power"], 4)
    if abs(total_power - run.power["total_power"]) > 0.0002:
        errors.append("power totals do not sum")
    if abs(sum(layer["wire_length"] for layer in run.routing["layers"]) - run.routing["wire_length"]) > 0.02:
        errors.append("routing layers do not sum")
    setup_slacks = [p["slack"] for p in run.timing["setup_paths"]]
    hold_slacks = [p["slack"] for p in run.timing["hold_paths"]]
    for path in run.timing["setup_paths"] + run.timing["hold_paths"]:
        if round(path["required_time"] - path["arrival_time"], 3) != round(path["slack"], 3):
            errors.append(f"{path['path_id']} slack does not equal required_time - arrival_time")
    if round(min(setup_slacks), 3) != round(run.timing["wns"], 3):
        errors.append("WNS does not match setup paths")
    if round(sum(s for s in setup_slacks if s < 0), 3) != round(run.timing["tns"], 3):
        errors.append("TNS does not match setup paths")
    if round(min(hold_slacks), 3) != round(run.timing["hold_wns"], 3):
        errors.append("hold WNS does not match hold paths")
    if round(sum(s for s in hold_slacks if s < 0), 3) != round(run.timing["hold_tns"], 3):
        errors.append("hold TNS does not match hold paths")
    return errors
