"""Parser interface and synthetic ChipForge report parsers."""
from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class ParseContext:
    run_id: str
    artifact_path: str
    artifact_type: str
    extraction_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class ParseResult:
    values: dict[str, Any]
    metrics: list[dict[str, Any]] = field(default_factory=list)
    timing_paths: list[dict[str, Any]] = field(default_factory=list)
    cell_statistics: list[dict[str, Any]] = field(default_factory=list)
    violations: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ArtifactParser(Protocol):
    name: str
    version: str
    artifact_types: frozenset[str]
    def parse(self, path: Path, context: ParseContext) -> ParseResult: ...


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, ArtifactParser] = {}

    def register(self, parser: ArtifactParser) -> None:
        for artifact_type in parser.artifact_types:
            if artifact_type in self._parsers:
                raise ValueError(f"parser already registered for {artifact_type}")
            self._parsers[artifact_type] = parser

    def get(self, artifact_type: str) -> ArtifactParser | None:
        return self._parsers.get(artifact_type)

    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        parser = self.get(context.artifact_type)
        if parser is None:
            return ParseResult({}, errors=[f"unsupported artifact_type: {context.artifact_type}"])
        try:
            result = parser.parse(path, context)
            for record in result.metrics + result.timing_paths + result.cell_statistics + result.violations:
                record.update(source_artifact_path=context.artifact_path, artifact_type=context.artifact_type,
                              parser_name=parser.name, parser_version=parser.version,
                              extraction_timestamp=context.extraction_timestamp)
            return result
        except Exception as exc:
            return ParseResult({}, errors=[f"{type(exc).__name__}: {exc}"])


def _metadata(text: str) -> dict[str, Any]:
    return {m.group(1).lower().replace(" ", "_"): m.group(2).strip()
            for m in re.finditer(r"^(Run ID|Design ID|Design|Flow Status|Overall Status):\s*(.*?)\s*$", text, re.M)}


def _kv(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_. ]*):\s*(-?\d+(?:\.\d+)?)\s*$", line)
        if m:
            values[m.group(1).strip().lower().replace(" ", "_")] = float(m.group(2))
        elif (m := re.match(r"^([a-z][a-z0-9_]*):\s*(-?\d+(?:\.\d+)?)\s*$", line)):
            n, v = m.groups(); f = float(v); values[n] = int(f) if f.is_integer() else f
    return values


class SyntheticParser:
    name = "chipforge_mock"
    version = "1.0.0"
    artifact_types: frozenset[str] = frozenset()
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        raise NotImplementedError

    def _result(self, values: dict[str, Any], context: ParseContext) -> ParseResult:
        metadata = values.pop("_metadata", {})
        run_id = metadata.get("run_id")
        errors = []
        if run_id and run_id != context.run_id:
            errors.append(f"report run_id {run_id} does not match manifest {context.run_id}")
        return ParseResult(values=metadata | values, metrics=_metric_rows(values, context), errors=errors)


def _metric_rows(values: dict[str, Any], context: ParseContext) -> list[dict[str, Any]]:
    from .metrics import METRIC_REGISTRY
    return [{"run_id": context.run_id, "metric_name": k, "metric_value": float(v), "metric_scope": "run",
             "unit": METRIC_REGISTRY[k].unit if k in METRIC_REGISTRY else None,
             "source_line_start": None, "source_line_end": None} for k, v in values.items() if isinstance(v, (int, float))]


class ConfigParser(SyntheticParser):
    artifact_types = frozenset({"run_configuration"})
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        values = json.loads(path.read_text())
        run_id = values.pop("run_id", context.run_id)
        return self._result({"_metadata": {"run_id": run_id, "design_id": values.pop("design_id", None)}, **values}, context)


class KVParser(SyntheticParser):
    artifact_types = frozenset({"area_report", "timing_summary", "clock_tree_summary", "routing_congestion", "lvs_report"})
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        text = path.read_text(); values = _kv(text)
        values["_metadata"] = _metadata(text)
        status = re.search(r"^LVS status:\s*(\w+)", text, re.M)
        if status: values["lvs_status"] = status.group(1)
        return self._result(values, context)


class CellStatsParser(SyntheticParser):
    artifact_types = frozenset({"cell_statistics"})
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        text = path.read_text(); values = _kv(text); stats = []
        for m in re.finditer(r"^  ([A-Z]+):\s*(\d+)\s*$", text, re.M):
            stats.append({"run_id": context.run_id, "cell_type": m.group(1), "cell_count": int(m.group(2)),
                          "source_line_start": text[:m.start()].count("\n") + 1, "source_line_end": text[:m.end()].count("\n") + 1})
        result = self._result({"_metadata": _metadata(text), **values}, context); result.cell_statistics = stats
        return result


class TimingPathsParser(SyntheticParser):
    artifact_types = frozenset({"sta_setup", "sta_hold"})
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        text = path.read_text(); rows = []
        for block in re.split(r"(?=^Path ID:)", text, flags=re.M)[1:]:
            row: dict[str, Any] = {}
            for line in block.splitlines():
                m = re.match(r"^([^:]+):\s*(.*?)\s*$", line)
                if not m: continue
                key, val = m.groups(); key = key.strip().lower().replace(" ", "_")
                num = re.match(r"(-?\d+(?:\.\d+)?)(?:\s+ns)?$", val)
                row[key] = float(num.group(1)) if num else val
                if key in {"logic_depth", "fanout"}: row[key] = int(row[key])
            if row:
                start = text.find(block); row.update(run_id=context.run_id, path_kind="setup" if context.artifact_type == "sta_setup" else "hold",
                    source_line_start=text[:start].count("\n") + 1, source_line_end=text[:start+len(block)].count("\n") + 1)
                if "path_delay" in row:
                    row["data_path_delay"] = row.pop("path_delay")
                rows.append(row)
        errors = [] if rows else ["no timing paths found"]
        return ParseResult(values=_metadata(text), timing_paths=rows, errors=errors)


class TableParser(SyntheticParser):
    artifact_types = frozenset({"power_report", "routing_summary", "drc_report", "antenna_report"})
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        text = path.read_text(); values = _kv(text); meta = _metadata(text); rows = []; header = None
        for line_no, line in enumerate(text.splitlines(), 1):
            if "," not in line: continue
            fields = next(csv.reader([line]))
            if header is None:
                header = fields; continue
            if len(fields) != len(header): continue
            row = dict(zip(header, fields))
            for key, value in list(row.items()):
                if re.fullmatch(r"-?\d+(?:\.\d+)?", value): row[key] = float(value)
            if context.artifact_type in {"drc_report", "antenna_report"}:
                row.update(run_id=context.run_id, source_line_start=line_no, source_line_end=line_no)
                rows.append(row)
            else:
                rows.append(row)
        if context.artifact_type == "routing_summary":
            values["layers"] = rows
        elif context.artifact_type == "power_report":
            values["categories"] = rows
        result = self._result({"_metadata": meta, **values}, context)
        if context.artifact_type == "drc_report": result.violations = rows
        if context.artifact_type == "antenna_report": result.violations = rows
        return result


class UnknownReportParser(SyntheticParser):
    artifact_types = frozenset({"synthesis_log", "floorplan_summary", "placement_summary", "placement_congestion"})
    def parse(self, path: Path, context: ParseContext) -> ParseResult:
        text = path.read_text(); vals = _kv(text); vals["_metadata"] = _metadata(text)
        return self._result(vals, context)


def default_registry() -> ParserRegistry:
    registry = ParserRegistry()
    for parser in (ConfigParser(), KVParser(), CellStatsParser(), TimingPathsParser(), TableParser(), UnknownReportParser()): registry.register(parser)
    return registry

DEFAULT_REGISTRY = default_registry()
