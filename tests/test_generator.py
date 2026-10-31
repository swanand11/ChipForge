import json
from pathlib import Path

from chipforge.calibration import calibration_report
from chipforge.generator import ARTIFACTS, generate_runs
from chipforge.validator import validate_run, validate_runs


def test_run_ids_are_unique(tmp_path: Path):
    runs = generate_runs(25, 42, tmp_path / "raw")
    assert len({r.run_id for r in runs}) == 25


def test_random_seed_is_reproducible(tmp_path: Path):
    a = generate_runs(5, 42, tmp_path / "a")
    b = generate_runs(5, 42, tmp_path / "b")
    assert [r.configuration for r in a] == [r.configuration for r in b]
    assert [r.timing["wns"] for r in a] == [r.timing["wns"] for r in b]


def test_all_required_artifacts_and_manifest_references_exist(tmp_path: Path):
    runs = generate_runs(3, 7, tmp_path / "raw")
    root = tmp_path / "raw" / "runs"
    for run in runs:
        run_dir = root / run.run_id
        assert (run_dir / "manifest.json").exists()
        for rel, *_ in ARTIFACTS:
            assert (run_dir / rel).exists()
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert manifest["run_id"] == run.run_id
        assert manifest["design_id"] == run.design.design_id
        assert manifest["statuses"] == run.statuses
        assert manifest["configuration"]["sample_region"] in {"EASY", "MODERATE", "AGGRESSIVE"}
        assert all((run_dir / artifact["path"]).exists() for artifact in manifest["artifacts"])


def test_report_metrics_are_internally_consistent(tmp_path: Path):
    runs = generate_runs(15, 100, tmp_path / "raw")
    root = tmp_path / "raw" / "runs"
    for run in runs:
        assert validate_run(run, root / run.run_id) == []
    assert validate_runs(runs, root)["integrity_errors"] == 0


def test_timing_reports_contain_paths(tmp_path: Path):
    runs = generate_runs(1, 12, tmp_path / "raw")
    text = (tmp_path / "raw" / "runs" / runs[0].run_id / "timing" / "setup.rpt").read_text()
    assert "Startpoint:" in text
    assert "Endpoint:" in text
    assert "Slack:" in text


def test_power_components_sum_correctly(tmp_path: Path):
    run = generate_runs(1, 2, tmp_path / "raw")[0]
    assert round(run.power["total_internal_power"] + run.power["total_switching_power"] + run.power["total_leakage_power"], 4) == run.power["total_power"]


def test_routing_layers_sum_approximately_correctly(tmp_path: Path):
    run = generate_runs(1, 5, tmp_path / "raw")[0]
    assert abs(sum(layer["wire_length"] for layer in run.routing["layers"]) - run.routing["wire_length"]) < 0.02


def test_invalid_runs_are_generated(tmp_path: Path):
    runs = generate_runs(200, 42, tmp_path / "raw")
    summary = validate_runs(runs, tmp_path / "raw" / "runs")
    assert summary["runs_with_violations"] > 0
    assert summary["runs_with_violations"] < summary["runs"]
    assert summary["timing_violations"] > 0
    assert summary["clean_runs"] > 0


def test_statuses_distinguish_flow_from_constraints(tmp_path: Path):
    runs = generate_runs(80, 42, tmp_path / "raw")
    summary = validate_runs(runs, tmp_path / "raw" / "runs")
    assert summary["runs_completed"] == 80
    assert summary["flow_failures"] == 0
    assert all(run.statuses["flow_status"] == "COMPLETED" for run in runs)
    assert {run.statuses["overall_status"] for run in runs} >= {"CLEAN", "CONSTRAINT_VIOLATION"}
    assert all(run.statuses["timing_status"] in {"PASS", "FAIL"} for run in runs)


def test_mathematical_invariants_hold(tmp_path: Path):
    runs = generate_runs(20, 123, tmp_path / "raw")
    for run in runs:
        assert sum(run.synthesis["cell_counts"].values()) == run.synthesis["total_cells"]
        assert run.synthesis["combinational_cells"] + run.synthesis["sequential_cells"] + run.synthesis["buffer_cells"] == run.synthesis["total_cells"]
        for path in run.timing["setup_paths"] + run.timing["hold_paths"]:
            assert round(path["required_time"] - path["arrival_time"], 3) == path["slack"]
        expected_utilization = round(100 * run.synthesis["total_area"] / run.floorplan["core_area"], 2)
        assert abs(expected_utilization - run.placement["placement_utilization"]) < 0.02


def test_statuses_are_derived_from_metrics(tmp_path: Path):
    runs = generate_runs(120, 42, tmp_path / "raw")
    for run in runs:
        assert run.statuses["timing_status"] == ("FAIL" if run.timing["wns"] < 0 or run.timing["hold_wns"] < 0 else "PASS")
        assert run.statuses["drc_status"] == ("FAIL" if run.signoff["drc_violations"] else "PASS")
        assert run.statuses["antenna_status"] == ("FAIL" if run.signoff["antenna_violations"] else "PASS")
        constraint_fail = any(run.statuses[key] == "FAIL" for key in ["timing_status", "routing_status", "drc_status", "antenna_status", "lvs_status"])
        assert run.statuses["overall_status"] == ("CONSTRAINT_VIOLATION" if constraint_fail else "CLEAN")


def test_calibration_report_has_useful_spectrum(tmp_path: Path):
    runs = generate_runs(1000, 42, tmp_path / "raw")
    report = calibration_report(runs, tmp_path / "raw" / "runs")
    summary = report["summary"]
    clean_rate = summary["clean_runs"] / summary["runs"]
    assert summary["integrity_errors"] == 0
    assert 0.10 <= clean_rate <= 0.40
    assert report["timing"]["wns"]["min"] < -0.05
    assert report["timing"]["wns"]["p25"] < 0.10
    assert report["timing"]["wns"]["p75"] > 0.10
    assert report["timing"]["wns"]["max"] > 0.40
    assert report["routing"]["congestion"]["p10"] < report["routing"]["congestion"]["median"] < report["routing"]["congestion"]["p90"]
    assert report["by_sample_region"]["EASY"]["clean_rate_pct"] > report["by_sample_region"]["AGGRESSIVE"]["clean_rate_pct"]


def test_multiple_designs_are_generated(tmp_path: Path):
    runs = generate_runs(50, 42, tmp_path / "raw")
    assert len({run.design.design_id for run in runs}) >= 3


def test_configuration_parameters_affect_generated_metrics(tmp_path: Path):
    runs = generate_runs(120, 99, tmp_path / "raw")
    tight = [r for r in runs if r.configuration["clock_period_ns"] < 1.5]
    loose = [r for r in runs if r.configuration["clock_period_ns"] > 2.7]
    assert tight and loose
    assert sum(r.power["total_power"] for r in tight) / len(tight) > sum(r.power["total_power"] for r in loose) / len(loose)
