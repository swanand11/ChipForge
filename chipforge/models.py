from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DesignProfile:
    design_id: str
    name: str
    top_module: str
    size_factor: float
    sequential_ratio: float
    macro_probability: float
    timing_difficulty: float
    power_density: float


DESIGNS = [
    DesignProfile("DESIGN_AES", "AES", "aes_top", 1.0, 0.30, 0.15, 0.55, 1.05),
    DesignProfile("DESIGN_UART", "UART", "uart_top", 0.34, 0.24, 0.02, 0.25, 0.55),
    DesignProfile("DESIGN_RISCV_CORE", "RISCV_CORE", "riscv_core", 2.25, 0.43, 0.30, 0.82, 1.35),
    DesignProfile("DESIGN_SHA256", "SHA256", "sha256_core", 1.25, 0.22, 0.05, 0.67, 1.15),
    DesignProfile("DESIGN_DSP", "DSP", "dsp_top", 1.7, 0.34, 0.45, 0.62, 1.45),
]


@dataclass
class DesignTruth:
    run_id: str
    design: DesignProfile
    timestamp: str
    configuration: dict[str, Any]
    statuses: dict[str, str] = field(default_factory=dict)
    synthesis: dict[str, Any] = field(default_factory=dict)
    floorplan: dict[str, Any] = field(default_factory=dict)
    placement: dict[str, Any] = field(default_factory=dict)
    cts: dict[str, Any] = field(default_factory=dict)
    timing: dict[str, Any] = field(default_factory=dict)
    power: dict[str, Any] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    signoff: dict[str, Any] = field(default_factory=dict)


DesignRun = DesignTruth
