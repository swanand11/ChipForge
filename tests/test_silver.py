import json
from pathlib import Path

from chipforge.generator import _build_run, write_run
from chipforge.silver.parsers import DEFAULT_REGISTRY, ParseContext
from chipforge.silver.processing import process_run
from chipforge.silver.validation import score_quality, validate_run
import random


def test_initial_report_parsers_match_designtruth(tmp_path: Path):
    truth = _build_run(1, random.Random(42)); run_dir = tmp_path / truth.run_id; write_run(truth, run_dir)
    manifest = json.loads((run_dir / "manifest.json").read_text())
    parsed = {}
    for artifact in manifest["artifacts"]:
        if artifact["artifact_type"] not in {"run_configuration", "timing_summary", "sta_setup", "area_report", "cell_statistics"}: continue
        rel = artifact["path"]
        parsed[artifact["artifact_type"]] = DEFAULT_REGISTRY.parse(run_dir / rel, ParseContext(truth.run_id, rel, artifact["artifact_type"]))
    assert parsed["timing_summary"].values["wns"] == truth.timing["wns"]
    assert parsed["timing_summary"].values["tns"] == truth.timing["tns"]
    assert parsed["area_report"].values["cell_area"] == truth.synthesis["cell_area"]
    assert parsed["area_report"].values["total_area"] == truth.synthesis["total_area"]
    assert parsed["cell_statistics"].values["total_cells"] == truth.synthesis["total_cells"]
    assert sum(x["slack"] for x in parsed["sta_setup"].timing_paths if x["slack"] < 0) == truth.timing["tns"]
    assert not any(p.errors for p in parsed.values())


def test_all_mock_artifact_types_have_registry_parser(tmp_path: Path):
    truth = _build_run(2, random.Random(9)); run_dir=tmp_path / truth.run_id; write_run(truth,run_dir)
    manifest=json.loads((run_dir/"manifest.json").read_text())
    for artifact in manifest["artifacts"]:
        result=DEFAULT_REGISTRY.parse(run_dir/artifact["path"],ParseContext(truth.run_id,artifact["path"],artifact["artifact_type"]))
        assert not result.errors, (artifact["artifact_type"],result.errors)


def test_cross_report_detects_tampering_and_quality_is_separate(tmp_path: Path):
    truth=_build_run(3,random.Random(10)); run_dir=tmp_path/truth.run_id; write_run(truth,run_dir)
    summary=run_dir/"timing/timing_summary.rpt"; summary.write_text(summary.read_text().replace(f"wns: {truth.timing['wns']}", "wns: 9.999"))
    output=process_run(run_dir)
    by_id={r["check_id"]:r for r in output["validation_results"]}
    assert by_id["TIMING_WNS"]["check_status"] == "FAIL"
    assert output["run_quality"][0]["quality_status"] != "HIGH"
    assert output["runs"][0]["overall_status"] == truth.statuses["overall_status"]


def test_manifest_inventory_detects_missing_and_unlisted_objects(tmp_path: Path):
    truth=_build_run(4,random.Random(13)); run_dir=tmp_path/truth.run_id; write_run(truth,run_dir)
    (run_dir/"synthesis/area.rpt").unlink(); (run_dir/"extra.bin").write_text("extra")
    output=process_run(run_dir)
    inventory=[r for r in output["validation_results"] if r["check_id"] == "ARTIFACT_INVENTORY"]
    assert any(r["check_status"] == "FAIL" for r in inventory)
    assert output["run_artifacts"][2]["exists"] is False
    assert output["run_quality"][0]["quality_status"] != "HIGH"
    assert output["runs"][0]["overall_status"] == truth.statuses["overall_status"]


def test_inventory_boolean_comparison_and_valid_inventory_quality(tmp_path: Path):
    truth = _build_run(5, random.Random(17)); run_dir = tmp_path / truth.run_id; write_run(truth, run_dir)
    valid = process_run(run_dir)
    inventory = next(x for x in valid["validation_results"] if x["check_id"] == "ARTIFACT_INVENTORY")
    assert inventory["check_status"] == "PASS"
    assert valid["run_quality"][0]["quality_status"] == "HIGH"
    (run_dir / "unexpected.rpt").write_text("unexpected")
    invalid = process_run(run_dir)
    inventory = next(x for x in invalid["validation_results"] if x["check_id"] == "ARTIFACT_INVENTORY")
    assert inventory["check_status"] == "FAIL"
    assert invalid["run_quality"][0]["quality_status"] != "HIGH"
    assert invalid["runs"][0]["overall_status"] == truth.statuses["overall_status"]
