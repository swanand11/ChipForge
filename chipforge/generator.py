from __future__ import annotations

import json
import math
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import DESIGNS, DesignProfile, DesignRun

ROUTING_OVERFLOW_FAIL_THRESHOLD = 1.0
ROUTING_CONGESTION_FAIL_THRESHOLD = 82.0

ARTIFACTS = [
    ("config/config.json", "config", "run_configuration", "json"),
    ("synthesis/synthesis.log", "synthesis", "synthesis_log", "text"),
    ("synthesis/area.rpt", "synthesis", "area_report", "text"),
    ("synthesis/stat.rpt", "synthesis", "cell_statistics", "text"),
    ("floorplan/floorplan.rpt", "floorplan", "floorplan_summary", "text"),
    ("placement/placement.rpt", "placement", "placement_summary", "text"),
    ("placement/congestion.rpt", "placement", "placement_congestion", "text"),
    ("cts/cts.rpt", "cts", "clock_tree_summary", "text"),
    ("timing/setup.rpt", "timing", "sta_setup", "text"),
    ("timing/hold.rpt", "timing", "sta_hold", "text"),
    ("timing/timing_summary.rpt", "timing", "timing_summary", "text"),
    ("power/power.rpt", "power", "power_report", "text"),
    ("routing/routing.rpt", "routing", "routing_summary", "text"),
    ("routing/routing_congestion.rpt", "routing", "routing_congestion", "text"),
    ("signoff/drc.rpt", "signoff", "drc_report", "text"),
    ("signoff/antenna.rpt", "signoff", "antenna_report", "text"),
    ("signoff/lvs.rpt", "signoff", "lvs_report", "text"),
]


def generate_runs(count: int, seed: int | None, output_root: Path) -> list[DesignRun]:
    rng = random.Random(seed)
    runs_root = output_root / "runs"
    if runs_root.exists():
        shutil.rmtree(runs_root)
    runs_root.mkdir(parents=True, exist_ok=True)
    runs = [_build_run(i + 1, rng) for i in range(count)]
    for run in runs:
        write_run(run, runs_root / run.run_id)
    return runs


def _build_run(index: int, rng: random.Random) -> DesignRun:
    design = rng.choice(DESIGNS)
    cfg = _sample_configuration(design, rng)
    timestamp = (datetime(2026, 10, 8, 10, 0, tzinfo=timezone.utc) + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
    run = DesignRun(f"RUN_{index:06d}", design, timestamp, cfg)
    _populate_metrics(run, rng)
    return run


def _sample_configuration(design: DesignProfile, rng: random.Random) -> dict[str, float | int | str]:
    sample_region = rng.choices(["EASY", "MODERATE", "AGGRESSIVE"], weights=[0.22, 0.50, 0.28], k=1)[0]
    if sample_region == "EASY":
        clock_base = rng.uniform(2.1, 4.0)
        util = rng.uniform(45, 62)
        density_delta = rng.uniform(0.5, 4.5)
        effort_center = 2.2
    elif sample_region == "MODERATE":
        clock_base = rng.uniform(1.05, 2.7)
        util = rng.uniform(58, 77)
        density_delta = rng.uniform(2.5, 8.0)
        effort_center = 2.8
    else:
        clock_base = rng.uniform(0.70, 1.55)
        util = rng.uniform(70, 86)
        density_delta = rng.uniform(4.5, 10.5)
        effort_center = 3.3

    clock = round(clock_base * (1 + design.timing_difficulty * 0.10), 3)
    tightness = max(0.0, (2.2 - clock) / 1.6) + design.timing_difficulty * 0.35
    effort_bias = max(1, min(4, round(effort_center + tightness * 0.7 + rng.uniform(-0.7, 0.7))))
    util = round(util, 1)
    return {
        "sample_region": sample_region,
        "clock_period_ns": clock,
        "target_frequency_mhz": round(1000 / clock, 2),
        "core_utilization_pct": util,
        "placement_density_pct": round(min(89, util + density_delta), 1),
        "synthesis_effort": effort_bias,
        "placement_effort": max(1, min(4, effort_bias + rng.choice([-1, 0, 0, 1]))),
        "routing_effort": max(1, min(4, effort_bias + rng.choice([-1, 0, 1]))),
        "buffering_effort": max(1, min(4, effort_bias + rng.choice([0, 0, 1]))),
        "die_area_target": round(design.size_factor * rng.uniform(145_000, 230_000), 2),
        "core_aspect_ratio": round(rng.uniform(0.75, 1.35), 2),
    }


def _populate_metrics(run: DesignRun, rng: random.Random) -> None:
    d, c = run.design, run.configuration
    tightness = max(0.0, (2.2 - c["clock_period_ns"]) / 1.6) + d.timing_difficulty * 0.40
    density = (c["placement_density_pct"] - 50) / 38
    effort_relief = (c["synthesis_effort"] + c["placement_effort"] + c["routing_effort"] + c["buffering_effort"]) / 16

    total_cells = max(800, int(d.size_factor * rng.randint(9000, 18000) * (1 + 0.10 * c["synthesis_effort"])))
    seq = int(total_cells * d.sequential_ratio * rng.uniform(0.88, 1.12))
    buffers = int(total_cells * (0.045 + 0.035 * c["buffering_effort"] + 0.035 * tightness))
    combo = total_cells - seq - buffers
    cell_counts = _cell_counts(combo, seq, buffers, rng)
    cell_area = round(combo * rng.uniform(1.35, 2.2) + seq * rng.uniform(4.0, 6.2) + buffers * rng.uniform(1.0, 1.8), 2)
    run.synthesis = {
        "wires": int(total_cells * rng.uniform(1.7, 2.4)),
        "wire_bits": int(total_cells * rng.uniform(2.4, 3.4)),
        "public_wires": int(total_cells * rng.uniform(0.08, 0.16)),
        "public_wire_bits": int(total_cells * rng.uniform(0.16, 0.28)),
        "memories": rng.randint(0, 2 if d.macro_probability < 0.2 else 6),
        "memory_bits": rng.randint(0, int(8192 * d.size_factor)),
        "processes": rng.randint(0, 4),
        "cell_counts": cell_counts,
        "total_cells": total_cells,
        "combinational_cells": combo,
        "sequential_cells": seq,
        "buffer_cells": buffers,
        "combinational_area": round(cell_area * combo / total_cells, 2),
        "sequential_area": round(cell_area * seq / total_cells, 2),
        "buffer_area": round(cell_area * buffers / total_cells, 2),
        "cell_area": cell_area,
        "total_area": round(cell_area * rng.uniform(1.02, 1.09), 2),
    }
    core_area = run.synthesis["total_area"] / (c["core_utilization_pct"] / 100)
    core_w = math.sqrt(core_area * c["core_aspect_ratio"])
    core_h = core_area / core_w
    die_w, die_h = core_w * 1.18, core_h * 1.18
    macro_count = rng.randint(0, max(0, round(d.macro_probability * 8)))
    macros = []
    for i in range(macro_count):
        w = round(rng.uniform(25, 90) * d.size_factor**0.25, 2)
        h = round(rng.uniform(20, 80) * d.size_factor**0.25, 2)
        macros.append({"macro_name": f"u_macro_{i}", "macro_type": rng.choice(["sram", "multiplier", "rom"]), "width": w, "height": h, "area": round(w * h, 2), "x": round(rng.uniform(5, max(6, core_w - w)), 2), "y": round(rng.uniform(5, max(6, core_h - h)), 2)})
    run.floorplan = {"die_area": round(die_w * die_h, 2), "core_area": round(core_area, 2), "die_width": round(die_w, 2), "die_height": round(die_h, 2), "core_width": round(core_w, 2), "core_height": round(core_h, 2), "core_utilization": c["core_utilization_pct"], "aspect_ratio": c["core_aspect_ratio"], "macro_count": macro_count, "standard_cell_count": total_cells, "io_pin_count": int(40 + d.size_factor * rng.randint(55, 160)), "macros": macros}
    overflow = max(0, density * rng.uniform(2.8, 13.5) + tightness * 2.3 - c["placement_effort"] * 1.65 + rng.uniform(-3.2, 4.2))
    congestion = min(99, max(5, c["placement_density_pct"] * 0.55 + overflow * 2.1 + d.size_factor * 4 + rng.uniform(-6, 8)))
    achieved_utilization = round(100 * run.synthesis["total_area"] / run.floorplan["core_area"], 2)
    run.placement = {"placed_cell_count": total_cells, "unplaced_cell_count": 0 if overflow < 15 else rng.randint(1, 12), "placement_utilization": achieved_utilization, "average_displacement": round(rng.uniform(0.4, 4.5) * (1 + overflow / 20), 2), "max_displacement": round(rng.uniform(12, 55) * (1 + overflow / 25), 2), "hpwl": round(total_cells * rng.uniform(8, 15) * (1 + density * 0.25), 2), "estimated_wirelength": 0, "overflow": round(overflow, 2), "density": c["placement_density_pct"], "horizontal_congestion_pct": round(congestion * rng.uniform(0.85, 1.1), 2), "vertical_congestion_pct": round(congestion * rng.uniform(0.9, 1.15), 2), "routing_congestion_pct": round(congestion, 2), "routing_overflow_pct": round(max(0, overflow * rng.uniform(0.8, 1.5)), 2)}
    run.placement["estimated_wirelength"] = round(run.placement["hpwl"] * rng.uniform(1.08, 1.34), 2)
    sink_count = max(1, seq)
    run.cts = {"clock_name": "clk", "clock_period": c["clock_period_ns"], "clock_source": "clk", "clock_sink_count": sink_count, "clock_buffer_count": max(4, int(sink_count * rng.uniform(0.035, 0.085) * (1 + tightness))), "clock_inverter_count": int(sink_count * rng.uniform(0.004, 0.014)), "clock_tree_depth": rng.randint(4, 10), "clock_skew": 0, "max_skew": round(rng.uniform(0.035, 0.16) * (1 + density), 3), "min_skew": round(rng.uniform(0.002, 0.02), 3), "clock_wirelength": round(sink_count * rng.uniform(2.2, 5.5), 2), "clock_buffer_area": 0}
    run.cts["clock_skew"] = round(run.cts["max_skew"] - run.cts["min_skew"], 3)
    run.cts["clock_buffer_area"] = round(run.cts["clock_buffer_count"] * rng.uniform(1.4, 2.3), 2)
    setup = _paths("setup", run, rng, 12, tightness, density, effort_relief)
    hold = _paths("hold", run, rng, 8, tightness * 0.45, density, effort_relief)
    setup_slacks = [p["slack"] for p in setup]
    hold_slacks = [p["slack"] for p in hold]
    run.timing = {"setup_paths": setup, "hold_paths": hold, "wns": min(setup_slacks), "tns": round(sum(s for s in setup_slacks if s < 0), 3), "hold_wns": min(hold_slacks), "hold_tns": round(sum(s for s in hold_slacks if s < 0), 3), "number_of_endpoints": max(10, int(seq * rng.uniform(0.75, 1.05))), "number_of_violating_endpoints": sum(1 for s in setup_slacks if s < 0), "hold_violating_endpoints": sum(1 for s in hold_slacks if s < 0), "critical_path_delay": max(p["path_delay"] for p in setup)}
    run.power = _power(run, rng, tightness)
    total_wire = run.placement["estimated_wirelength"] * rng.uniform(1.05, 1.35)
    layers = _routing_layers(total_wire, rng, congestion)
    run.routing = {"wire_length": round(sum(layer["wire_length"] for layer in layers), 2), "via_count": int(sum(layer["via_count"] for layer in layers)), "wire_count": int(total_cells * rng.uniform(1.1, 1.9)), "routed_net_count": int(total_cells * rng.uniform(0.72, 1.12)), "unrouted_net_count": 0 if congestion < 82 else rng.randint(1, 18), "layers": layers, "routing_overflow": round(max(0, run.placement["routing_overflow_pct"] * rng.uniform(0.55, 1.25) - c["routing_effort"] * 0.95), 2), "horizontal_overflow": round(max(0, run.placement["horizontal_congestion_pct"] - ROUTING_CONGESTION_FAIL_THRESHOLD) * rng.uniform(0.35, 1.0), 2), "vertical_overflow": round(max(0, run.placement["vertical_congestion_pct"] - ROUTING_CONGESTION_FAIL_THRESHOLD) * rng.uniform(0.35, 1.0), 2)}
    routing_stress = max(0, run.routing["routing_overflow"] - ROUTING_OVERFLOW_FAIL_THRESHOLD) + max(0, congestion - ROUTING_CONGESTION_FAIL_THRESHOLD) / 8
    drc_n = int(max(0, routing_stress * rng.uniform(0.8, 2.8) + (run.routing["unrouted_net_count"] > 0) * rng.uniform(0, 3) - rng.uniform(0.5, 2.5)))
    ant_n = int(max(0, routing_stress * rng.uniform(0, 0.9) + run.routing["via_count"] / 35_000 * rng.uniform(0, 2.2) - 1.8))
    lvs_bad = drc_n > 25 and run.routing["unrouted_net_count"] > 0 and rng.random() < min(0.12, drc_n / 600)
    run.signoff = {"drc_violations": _violations(drc_n, rng), "antenna_violations": _antenna(ant_n, rng), "lvs_status": "FAIL" if lvs_bad else "PASS", "matched_devices": total_cells + macro_count, "unmatched_devices": rng.randint(1, 4) if lvs_bad else 0, "net_mismatches": rng.randint(1, 5) if lvs_bad else 0, "pin_mismatches": rng.randint(0, 3) if lvs_bad else 0}
    run.statuses = _derive_statuses(run)


def _derive_statuses(run: DesignRun) -> dict[str, str]:
    flow_status = "COMPLETED"
    timing_status = "FAIL" if run.timing["wns"] < 0 or run.timing["hold_wns"] < 0 else "PASS"
    routing_status = "FAIL" if run.routing["routing_overflow"] >= ROUTING_OVERFLOW_FAIL_THRESHOLD or run.routing["unrouted_net_count"] > 0 else "PASS"
    drc_status = "FAIL" if run.signoff["drc_violations"] else "PASS"
    antenna_status = "FAIL" if run.signoff["antenna_violations"] else "PASS"
    lvs_status = run.signoff["lvs_status"]
    constraint_statuses = [timing_status, routing_status, drc_status, antenna_status, lvs_status]
    if flow_status == "FAILED":
        overall_status = "FLOW_FAILED"
    elif any(status == "FAIL" for status in constraint_statuses):
        overall_status = "CONSTRAINT_VIOLATION"
    else:
        overall_status = "CLEAN"
    return {
        "flow_status": flow_status,
        "timing_status": timing_status,
        "routing_status": routing_status,
        "drc_status": drc_status,
        "antenna_status": antenna_status,
        "lvs_status": lvs_status,
        "overall_status": overall_status,
    }


def _cell_counts(combo: int, seq: int, buffers: int, rng: random.Random) -> dict[str, int]:
    names = ["AND", "OR", "XOR", "XNOR", "NAND", "NOR", "MUX", "INV"]
    weights = [0.10, 0.08, 0.08, 0.04, 0.20, 0.08, 0.11, 0.31]
    counts = {n: int(combo * w * rng.uniform(0.8, 1.2)) for n, w in zip(names, weights)}
    counts["BUF"] = buffers
    counts["DFF"] = int(seq * rng.uniform(0.90, 0.99))
    counts["LATCH"] = seq - counts["DFF"]
    counts["INV"] += combo - sum(counts[n] for n in names)
    return counts


def _paths(kind: str, run: DesignRun, rng: random.Random, count: int, tightness: float, density: float, effort_relief: float) -> list[dict[str, Any]]:
    paths = []
    period = run.configuration["clock_period_ns"]
    for i in range(count):
        depth = max(1, int(rng.gauss(9 + run.design.size_factor * 3 + tightness * 4, 3)))
        fanout = max(1, int(rng.expovariate(0.35)))
        skew = round(run.cts["clock_skew"] * rng.uniform(-0.4, 1.0), 3)
        if kind == "setup":
            required = period - rng.uniform(0.02, 0.08)
            delay = period * rng.uniform(0.44, 0.94) + tightness * rng.uniform(0.06, 0.30) + density * 0.09 - effort_relief * 0.15
            arrival = delay + max(0, skew)
        else:
            required = rng.uniform(0.10, 0.24)
            delay = rng.uniform(0.03, 0.19) - effort_relief * 0.040 + tightness * 0.038 + density * 0.032
            arrival = max(0.005, delay - max(0, skew * 0.5))
        required_time = round(required, 3)
        arrival_time = round(arrival, 3)
        slack = round(required_time - arrival_time, 3)
        paths.append({"path_id": f"{kind.upper()}_{i + 1:03d}", "startpoint": f"core/u_reg_{rng.randint(1, 9999)}/CK", "endpoint": f"core/u_reg_{rng.randint(1, 9999)}/D", "start_clock": "clk", "end_clock": "clk", "required_time": required_time, "arrival_time": arrival_time, "slack": slack, "path_delay": round(max(0.001, delay), 3), "logic_depth": depth, "fanout": fanout, "clock_skew": skew})
    return paths


def _power(run: DesignRun, rng: random.Random, tightness: float) -> dict[str, Any]:
    cats = {"Sequential": 0.30, "Combinational": 0.38, "Macro": 0.10 + 0.04 * run.floorplan["macro_count"], "Pad": 0.04, "Clock": 0.18}
    scale = run.synthesis["total_cells"] * run.design.power_density * (1000 / run.configuration["clock_period_ns"]) / 1_000_000
    rows = []
    for name, weight in cats.items():
        internal = round(scale * weight * rng.uniform(0.35, 0.65), 4)
        switching = round(scale * weight * rng.uniform(0.30, 0.70) * (1 + tightness * 0.16), 4)
        leakage = round(scale * weight * rng.uniform(0.035, 0.10), 4)
        rows.append({"category": name, "internal_power": internal, "switching_power": switching, "leakage_power": leakage, "total_power": round(internal + switching + leakage, 4), "percentage": 0})
    total = round(sum(r["total_power"] for r in rows), 4)
    for row in rows:
        row["percentage"] = round(100 * row["total_power"] / total, 2) if total else 0
    return {"categories": rows, "total_internal_power": round(sum(r["internal_power"] for r in rows), 4), "total_switching_power": round(sum(r["switching_power"] for r in rows), 4), "total_leakage_power": round(sum(r["leakage_power"] for r in rows), 4), "total_power": total}


def _routing_layers(total: float, rng: random.Random, congestion: float) -> list[dict[str, Any]]:
    weights = [0.15, 0.24, 0.22, 0.18, 0.13, 0.08]
    layers = []
    for i, weight in enumerate(weights, 1):
        wire = round(total * weight * rng.uniform(0.92, 1.08), 2)
        layers.append({"metal_layer": f"M{i}", "wire_length": wire, "via_count": int(wire * rng.uniform(0.015, 0.045)), "utilization_pct": round(min(98, congestion * rng.uniform(0.55, 1.08) - i * 2), 2)})
    return layers


def _violations(count: int, rng: random.Random) -> list[dict[str, Any]]:
    types = ["minimum_spacing", "minimum_width", "enclosure", "short"]
    return [{"violation_type": rng.choice(types), "layer": f"M{rng.randint(1, 6)}", "location": f"({rng.randint(0, 1200)}, {rng.randint(0, 1200)})", "description": "rule check violation near routed geometry"} for _ in range(count)]


def _antenna(count: int, rng: random.Random) -> list[dict[str, Any]]:
    return [{"violating_net": f"net_{rng.randint(100, 99999)}", "layer": f"M{rng.randint(2, 6)}", "ratio": round(rng.uniform(410, 1200), 2), "limit": 400.0} for _ in range(count)]


def write_run(run: DesignRun, run_dir: Path) -> None:
    for artifact, *_ in ARTIFACTS:
        (run_dir / artifact).parent.mkdir(parents=True, exist_ok=True)
    manifest = _manifest(run)
    _write_json(run_dir / "manifest.json", manifest)
    _write_json(run_dir / "config/config.json", run.configuration | {"run_id": run.run_id, "design_id": run.design.design_id})
    _write(run_dir / "synthesis/synthesis.log", render_synthesis_log(run))
    _write(run_dir / "synthesis/stat.rpt", render_stat(run))
    _write(run_dir / "synthesis/area.rpt", render_area(run))
    _write(run_dir / "floorplan/floorplan.rpt", render_floorplan(run))
    _write(run_dir / "placement/placement.rpt", render_placement(run))
    _write(run_dir / "placement/congestion.rpt", render_placement_congestion(run))
    _write(run_dir / "cts/cts.rpt", render_kv("CTS Report", run, run.cts))
    _write(run_dir / "timing/setup.rpt", render_timing_paths("Setup Timing Report", run, run.timing["setup_paths"]))
    _write(run_dir / "timing/hold.rpt", render_timing_paths("Hold Timing Report", run, run.timing["hold_paths"]))
    _write(run_dir / "timing/timing_summary.rpt", render_kv("Timing Summary", run, {k: v for k, v in run.timing.items() if not k.endswith("_paths")}))
    _write(run_dir / "power/power.rpt", render_power(run))
    _write(run_dir / "routing/routing.rpt", render_routing(run))
    _write(run_dir / "routing/routing_congestion.rpt", render_routing_congestion(run))
    _write(run_dir / "signoff/drc.rpt", render_drc(run))
    _write(run_dir / "signoff/antenna.rpt", render_antenna(run))
    _write(run_dir / "signoff/lvs.rpt", render_kv("LVS Report", run, {"LVS status": run.signoff["lvs_status"], "matched devices": run.signoff["matched_devices"], "unmatched devices": run.signoff["unmatched_devices"], "net mismatches": run.signoff["net_mismatches"], "pin mismatches": run.signoff["pin_mismatches"]}))


def _manifest(run: DesignRun) -> dict[str, Any]:
    return {"run_id": run.run_id, "design_id": run.design.design_id, "design_name": run.design.name.lower(), "flow": "chipforge-mock", "flow_version": "1.0", "technology": "sky130", "process_node_nm": 130, "timestamp": run.timestamp, "configuration": run.configuration, "statuses": run.statuses, "artifacts": [{"path": p, "stage": s, "artifact_type": t, "format": f} for p, s, t, f in ARTIFACTS]}


def _write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _header(title: str, run: DesignRun) -> str:
    return f"{title}\nRun ID: {run.run_id}\nDesign ID: {run.design.design_id}\nDesign: {run.design.name}\nFlow Status: {run.statuses['flow_status']}\nOverall Status: {run.statuses['overall_status']}\n\n"


def render_kv(title: str, run: DesignRun, data: dict[str, Any]) -> str:
    return _header(title, run) + "\n".join(f"{k}: {v}" for k, v in data.items()) + "\n"


def render_synthesis_log(run: DesignRun) -> str:
    return _header("Yosys/ABC Synthesis Log", run) + f"Top module: {run.design.top_module}\nABC: mapped {run.synthesis['total_cells']} gates to sky130_fd_sc_hd cells\nSynthesis completed.\n"


def render_stat(run: DesignRun) -> str:
    s = run.synthesis
    lines = [_header("Yosys Statistics Report", run), f"Top module: {run.design.top_module}", f"Number of wires: {s['wires']}", f"Number of wire bits: {s['wire_bits']}", f"Number of public wires: {s['public_wires']}", f"Number of public wire bits: {s['public_wire_bits']}", f"Number of memories: {s['memories']}", f"Number of memory bits: {s['memory_bits']}", f"Number of processes: {s['processes']}", "", "Cell statistics:"]
    lines += [f"  {k}: {v}" for k, v in s["cell_counts"].items()]
    lines += ["", f"total_cells: {s['total_cells']}", f"combinational_cells: {s['combinational_cells']}", f"sequential_cells: {s['sequential_cells']}", f"buffer_cells: {s['buffer_cells']}"]
    return "\n".join(lines) + "\n"


def render_area(run: DesignRun) -> str:
    keys = ["cell_area", "combinational_area", "sequential_area", "buffer_area", "total_area"]
    return _header("Synthesis Area Report", run) + "\n".join(f"{k}: {run.synthesis[k]}" for k in keys) + "\n"


def render_floorplan(run: DesignRun) -> str:
    fp = run.floorplan
    keys = ["die_area", "core_area", "die_width", "die_height", "core_width", "core_height", "core_utilization", "aspect_ratio", "macro_count", "standard_cell_count", "io_pin_count"]
    text = _header("Floorplan Report", run) + "\n".join(f"{k}: {fp[k]}" for k in keys) + "\n\nMacro summary:\n"
    return text + "\n".join(", ".join(f"{k}={v}" for k, v in m.items()) for m in fp["macros"]) + "\n"


def render_placement(run: DesignRun) -> str:
    keys = ["placed_cell_count", "unplaced_cell_count", "placement_utilization", "average_displacement", "max_displacement", "hpwl", "estimated_wirelength", "overflow", "density"]
    return _header("Placement Report", run) + "\n".join(f"{k}: {run.placement[k]}" for k in keys) + "\n"


def render_placement_congestion(run: DesignRun) -> str:
    p = run.placement
    lines = [_header("Placement Congestion Report", run), f"horizontal_congestion_pct: {p['horizontal_congestion_pct']}", f"vertical_congestion_pct: {p['vertical_congestion_pct']}", f"routing_congestion_pct: {p['routing_congestion_pct']}", f"routing_overflow_pct: {p['routing_overflow_pct']}", "", "grid_x,grid_y,horizontal_congestion,vertical_congestion"]
    for x in range(4):
        for y in range(4):
            h_scale = 0.82 + ((x * 7 + y * 3) % 9) / 25
            v_scale = 0.80 + ((x * 5 + y * 11) % 10) / 26
            lines.append(f"{x},{y},{round(p['horizontal_congestion_pct'] * h_scale, 2)},{round(p['vertical_congestion_pct'] * v_scale, 2)}")
    return "\n".join(lines) + "\n"


def render_timing_paths(title: str, run: DesignRun, paths: list[dict[str, Any]]) -> str:
    chunks = [_header(title, run)]
    for p in paths:
        chunks.append(f"Path ID: {p['path_id']}\nStartpoint: {p['startpoint']}\nEndpoint:   {p['endpoint']}\nPath Group: clk\nStart Clock: {p['start_clock']}\nEnd Clock: {p['end_clock']}\nRequired Time: {p['required_time']:.3f} ns\nArrival Time:  {p['arrival_time']:.3f} ns\nSlack:         {p['slack']:.3f} ns\nData Path Delay: {p['path_delay']:.3f} ns\nLogic Depth: {p['logic_depth']}\nFanout: {p['fanout']}\nClock Skew: {p['clock_skew']:.3f} ns\n")
    return "\n".join(chunks)


def render_power(run: DesignRun) -> str:
    lines = [_header("Power Report", run), "Category,internal_power,switching_power,leakage_power,total_power,percentage"]
    lines += [f"{r['category']},{r['internal_power']},{r['switching_power']},{r['leakage_power']},{r['total_power']},{r['percentage']}" for r in run.power["categories"]]
    lines += ["", f"total_internal_power: {run.power['total_internal_power']}", f"total_switching_power: {run.power['total_switching_power']}", f"total_leakage_power: {run.power['total_leakage_power']}", f"total_power: {run.power['total_power']}"]
    return "\n".join(lines) + "\n"


def render_routing(run: DesignRun) -> str:
    r = run.routing
    lines = [_header("Routing Report", run), f"wire_length: {r['wire_length']}", f"via_count: {r['via_count']}", f"wire_count: {r['wire_count']}", f"routed_net_count: {r['routed_net_count']}", f"unrouted_net_count: {r['unrouted_net_count']}", "", "metal_layer,wire_length,via_count,utilization_pct"]
    lines += [f"{x['metal_layer']},{x['wire_length']},{x['via_count']},{x['utilization_pct']}" for x in r["layers"]]
    return "\n".join(lines) + "\n"


def render_routing_congestion(run: DesignRun) -> str:
    keys = ["routing_overflow", "horizontal_overflow", "vertical_overflow"]
    return _header("Routing Congestion Report", run) + "\n".join(f"{k}: {run.routing[k]}" for k in keys) + "\n"


def render_drc(run: DesignRun) -> str:
    rows = run.signoff["drc_violations"]
    lines = [_header("DRC Report", run), f"total_violations: {len(rows)}", "violation_type,layer,location,description"]
    lines += [f"{v['violation_type']},{v['layer']},{v['location']},{v['description']}" for v in rows]
    return "\n".join(lines) + "\n"


def render_antenna(run: DesignRun) -> str:
    rows = run.signoff["antenna_violations"]
    lines = [_header("Antenna Report", run), f"antenna_violations: {len(rows)}", "violating_net,layer,ratio,limit"]
    lines += [f"{v['violating_net']},{v['layer']},{v['ratio']},{v['limit']}" for v in rows]
    return "\n".join(lines) + "\n"
