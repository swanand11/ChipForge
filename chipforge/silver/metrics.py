"""Identity-mapped canonical metrics emitted by ChipForge's mock reports."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    data_type: str
    unit: str | None
    category: str
    description: str
    minimum: float | None = None
    aggregation: str | None = None


_DEFS = [
    ("cell_area", "double", "um^2", "synthesis", "Mapped standard cell area", 0, "last"),
    ("combinational_area", "double", "um^2", "synthesis", "Combinational cell area", 0, "last"),
    ("sequential_area", "double", "um^2", "synthesis", "Sequential cell area", 0, "last"),
    ("buffer_area", "double", "um^2", "synthesis", "Buffer cell area", 0, "last"),
    ("total_area", "double", "um^2", "synthesis", "Area including synthesis overhead", 0, "last"),
    ("total_cells", "long", "count", "synthesis", "Total mapped cell count", 0, "last"),
    ("combinational_cells", "long", "count", "synthesis", "Combinational cell count", 0, "last"),
    ("sequential_cells", "long", "count", "synthesis", "Sequential cell count", 0, "last"),
    ("buffer_cells", "long", "count", "synthesis", "Buffer cell count", 0, "last"),
    ("wns", "double", "ns", "timing", "Worst setup slack", None, "min"),
    ("tns", "double", "ns", "timing", "Sum of negative setup slacks", None, "sum"),
    ("hold_wns", "double", "ns", "timing", "Worst hold slack", None, "min"),
    ("hold_tns", "double", "ns", "timing", "Sum of negative hold slacks", None, "sum"),
    ("number_of_endpoints", "long", "count", "timing", "Timing endpoint count", 0, "last"),
    ("number_of_violating_endpoints", "long", "count", "timing", "Setup violating endpoints", 0, "last"),
    ("hold_violating_endpoints", "long", "count", "timing", "Hold violating endpoints", 0, "last"),
    ("critical_path_delay", "double", "ns", "timing", "Critical setup path delay", 0, "max"),
    ("total_internal_power", "double", "mock_power_unit", "power", "Internal power total", 0, "last"),
    ("total_switching_power", "double", "mock_power_unit", "power", "Switching power total", 0, "last"),
    ("total_leakage_power", "double", "mock_power_unit", "power", "Leakage power total", 0, "last"),
    ("total_power", "double", "mock_power_unit", "power", "Reported total power", 0, "last"),
    ("wire_length", "double", "mock_wire_unit", "routing", "Routed total wire length", 0, "last"),
    ("via_count", "long", "count", "routing", "Total via count", 0, "last"),
    ("wire_count", "long", "count", "routing", "Wire count", 0, "last"),
    ("routed_net_count", "long", "count", "routing", "Routed net count", 0, "last"),
    ("unrouted_net_count", "long", "count", "routing", "Unrouted net count", 0, "last"),
]
METRIC_REGISTRY = {d[0]: MetricDefinition(*d) for d in _DEFS}


def validate_metric(name: str, value: Any) -> str | None:
    definition = METRIC_REGISTRY.get(name)
    if definition is None:
        return None
    if value is None:
        return "metric value is null"
    if definition.minimum is not None and value < definition.minimum:
        return f"{name} must be >= {definition.minimum}"
    return None
