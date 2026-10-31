"""Independent artifact, schema, metric, and cross-report validation."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any
from .metrics import validate_metric

DEFAULT_TOLERANCES = {"timing_ns": 0.001, "area": 0.02, "power": 0.0002, "wire_length": 0.02}

def _result(run_id, check_id, name, category, expected, actual, tolerance, explanation, applicable=True):
    if not applicable: status = "NOT_APPLICABLE"
    elif isinstance(expected, bool) or isinstance(actual, bool):
        status = "PASS" if expected is actual else "FAIL"
    else:
        try: difference = float(actual) - float(expected)
        except (TypeError, ValueError): difference = 0.0 if actual == expected else None
        status = "PASS" if difference is not None and (tolerance is None or abs(difference) <= tolerance) else "FAIL"
    try: diff = float(actual) - float(expected)
    except (TypeError, ValueError): diff = None
    return dict(check_id=check_id, run_id=run_id, check_name=name, check_category=category,
                expected_value=None if expected is None else str(expected), actual_value=None if actual is None else str(actual),
                difference=diff, tolerance=tolerance, check_status=status, explanation=explanation)

def _values(parsed):
    out = {}
    for p in parsed.values():
        for key, value in p.values.items():
            if isinstance(value, (int, float)): out[key] = value
        for row in p.values.get("categories", []):
            for key in ("internal_power", "switching_power", "leakage_power", "total_power"):
                out.setdefault("category_" + key, []).append(row.get(key))
        for row in p.values.get("layers", []): out.setdefault("layer_wire_length", []).append(row.get("wire_length"))
    return out

def validate_run(run_id: str, manifest: dict[str, Any], actual_paths: set[str], parsed: dict[str, Any], tolerances=None):
    tol = DEFAULT_TOLERANCES | (tolerances or {}); results = []
    listed = {a["path"] for a in manifest.get("artifacts", [])}
    # actual_paths contains report objects only; manifest.json is the inventory itself.
    inventory_ok = listed == actual_paths
    missing = sorted(listed - actual_paths)
    unlisted = sorted(actual_paths - listed)
    results.append(_result(run_id, "ARTIFACT_INVENTORY", "Manifest inventory matches objects", "artifact",
        True, inventory_ok, None,
        f"Missing artifacts: {missing}; unlisted objects: {unlisted}."))
    for artifact, result in parsed.items():
        for err in result.errors:
            results.append(_result(run_id, "PARSE_ERROR", "Artifact parse", "schema", "no parsing errors", err, None, err))
    values = _values(parsed)
    for name, value in values.items():
        if isinstance(value, (int, float)):
            err = validate_metric(name, value)
            if err: results.append(_result(run_id, "METRIC_VALIDATION", f"Validate {name}", "metric", "valid range", value, None, err))
    def check(id, name, a, b, tolerance, explanation, applicable=True):
        results.append(_result(run_id, id, name, "cross_report", a, b, tolerance, explanation, applicable))
    paths = [r for p in parsed.values() for r in p.timing_paths if r.get("path_kind") == "setup"]
    if "wns" in values and paths:
        expected = min(x["slack"] for x in paths); check("TIMING_WNS", "WNS versus minimum setup slack", expected, values["wns"], tol["timing_ns"], "Compares run scoped setup slack in ns.")
    else: check("TIMING_WNS", "WNS versus minimum setup slack", None, None, tol["timing_ns"], "Requires timing summary and setup paths.", False)
    if "tns" in values and paths:
        expected = sum(x["slack"] for x in paths if x["slack"] < 0); check("TIMING_TNS", "TNS versus negative setup slack sum", expected, values["tns"], tol["timing_ns"], "Compares run scoped setup slack in ns.")
    else: check("TIMING_TNS", "TNS versus negative setup slack sum", None, None, tol["timing_ns"], "Requires timing summary and setup paths.", False)
    stats = [r for p in parsed.values() for r in p.cell_statistics]
    if stats and "total_cells" in values: check("CELL_TOTAL", "Cell statistics versus total cells", values["total_cells"], sum(r["cell_count"] for r in stats), 0, "Sum of synthetic cell type counts against total cell count.")
    else: check("CELL_TOTAL", "Cell statistics versus total cells", None, None, 0, "Requires statistics report.", False)
    if all(k in values for k in ("combinational_area", "sequential_area", "buffer_area", "cell_area")):
        check("AREA_TOTAL", "Area components versus cell area", values["cell_area"], sum(values[k] for k in ("combinational_area", "sequential_area", "buffer_area")), tol["area"], "Areas are all reported in um^2.")
    else: check("AREA_TOTAL", "Area components versus cell area", None, None, tol["area"], "Requires all synthesis area components.", False)
    # Power tables use row totals; reported top-level components are independently scoped total sums.
    categories = [r for p in parsed.values() for r in p.values.get("categories", []) if "total_power" in r]
    if categories and "total_power" in values:
        check("POWER_TOTAL", "Power components versus reported total", values["total_power"], sum(r["total_power"] for r in categories), tol["power"], "Sum of category power totals against reported total.")
    else: check("POWER_TOTAL", "Power components versus reported total", None, None, tol["power"], "Requires power table and reported total.", False)
    layers = [r for p in parsed.values() for r in p.values.get("layers", []) if "wire_length" in r]
    if layers and "wire_length" in values:
        check("ROUTING_WIRE_LENGTH", "Layer wire lengths versus total", values["wire_length"], sum(r["wire_length"] for r in layers), tol["wire_length"], "Sum of routed layer wire lengths against total, same units and scope.")
    else: check("ROUTING_WIRE_LENGTH", "Layer wire lengths versus total", None, None, tol["wire_length"], "Requires routing layer rows and total.", False)
    # Status checks only where report semantics explicitly provide the same status.
    statuses = manifest.get("statuses", {})
    for key, expected in statuses.items():
        seen = [p.values.get(key) for p in parsed.values() if key in p.values]
        check("STATUS_" + key.upper(), f"Status consistency: {key}", expected, seen[0] if seen else None, None,
              "Status comparison uses matching named status fields." if seen else "No artifact exposes this named status.", bool(seen))
    return results

def score_quality(run_id: str, manifest: dict[str, Any], parsed: dict[str, Any], results: list[dict[str, Any]], mandatory_types=None):
    mandatory = set(mandatory_types or {"run_configuration", "timing_summary", "sta_setup", "area_report", "cell_statistics"})
    present = {a for a, p in parsed.items() if p is not None}
    completeness = 100 * len(mandatory & present) / len(mandatory) if mandatory else 100.0
    parsable = [p for p in parsed.values() if p is not None]
    declared_count = len(manifest.get("artifacts", []))
    parsing = 100 * sum(not p.errors for p in parsable) / declared_count if declared_count else 0.0
    schema = 100.0 if all(manifest.get(k) for k in ("run_id", "design_id", "artifacts", "statuses")) else 0.0
    checks = [r for r in results if r["check_category"] in {"artifact", "cross_report", "metric", "schema"} and r["check_status"] != "NOT_APPLICABLE"]
    consistency = 100 * sum(r["check_status"] == "PASS" for r in checks) / len(checks) if checks else 100.0
    overall = round(.20*completeness + .25*parsing + .20*schema + .35*consistency, 2)
    failed = sum(r["check_status"] == "FAIL" for r in results); warnings = sum(r["check_status"] == "WARNING" for r in results)
    critical = (bool(mandatory - present)
                or any(r["check_status"] == "FAIL" and r["check_id"] in {
                    "ARTIFACT_INVENTORY", "TIMING_WNS", "TIMING_TNS", "AREA_TOTAL",
                    "CELL_TOTAL", "POWER_TOTAL", "ROUTING_WIRE_LENGTH"} for r in results))
    status = "HIGH" if overall >= 95 and not critical else "MEDIUM" if overall >= 80 else "LOW"
    return dict(run_id=run_id, artifact_completeness_score=round(completeness,2), parsing_success_score=round(parsing,2), schema_validity_score=round(schema,2), cross_report_consistency_score=round(consistency,2), overall_data_quality_score=overall, quality_status=status, failed_checks=failed, warning_checks=warnings)
