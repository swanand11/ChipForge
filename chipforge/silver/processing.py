"""Artifact discovery, normalization, audit, and Spark Silver writers."""
from __future__ import annotations
import argparse, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from .parsers import DEFAULT_REGISTRY, ParseContext
from .validation import validate_run, score_quality
DATASETS = ("runs", "run_configurations", "run_artifacts", "metric_observations", "timing_paths", "cell_statistics", "violations", "validation_results", "run_quality")
MANDATORY = {"run_configuration", "timing_summary", "sta_setup", "area_report", "cell_statistics"}

def discover_run(run_dir: Path):
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    files = {str(p.relative_to(run_dir)).replace("\\", "/") for p in run_dir.rglob("*") if p.is_file() and p.name != "manifest.json"}
    return manifest, files

def process_run(run_dir: Path, registry=DEFAULT_REGISTRY, tolerances=None):
    manifest, files = discover_run(run_dir); run_id = manifest["run_id"]
    parsed = {}; audits = []; metrics=[]; paths=[]; cells=[]; violations=[]; artifacts=[]
    by_path = {a["path"]: a for a in manifest.get("artifacts", [])}
    for rel, artifact in by_path.items():
        p = run_dir / rel; typ = artifact["artifact_type"]; exists = rel in files
        parser = registry.get(typ); timestamp = datetime.now(timezone.utc).isoformat()
        result = registry.parse(p, ParseContext(run_id, rel, typ, timestamp)) if exists else None
        parsed[rel] = result
        errors = result.errors if result else ["artifact missing"]
        if result:
            metrics.extend(result.metrics); paths.extend(result.timing_paths); cells.extend(result.cell_statistics); violations.extend(result.violations)
        evidence_base = dict(source_artifact_path=rel, artifact_type=typ, parser_name=parser.name if parser else None,
            parser_version=parser.version if parser else None, extraction_timestamp=timestamp)
        for result_type in (result.metrics if result else [], result.timing_paths if result else [], result.cell_statistics if result else [], result.violations if result else []):
            for record in result_type:
                for field in evidence_base:
                    record.setdefault(field, evidence_base[field])
        audits.append({"run_id":run_id,"source_artifact_path":rel,"artifact_type":typ,"format":artifact.get("format"),"stage":artifact.get("stage"),"exists":exists,"parse_success":bool(result and not result.errors),"parse_errors":errors,"parser_name":parser.name if parser else None,"parser_version":parser.version if parser else None,"extraction_timestamp":timestamp})
        artifacts.append({"run_id":run_id, **evidence_base, "format":artifact.get("format"),"stage":artifact.get("stage"),"exists":exists,"parse_success":bool(result and not result.errors),"parse_errors":errors})
    by_type = {by_path[path]["artifact_type"]: result for path,result in parsed.items() if path in by_path and result}
    present_types = {by_path[path]["artifact_type"] for path in files if path in by_path}
    # Parser result row evidence keeps original Bronze path. Missing line numbers remain null.
    validations = validate_run(run_id, manifest, files, by_type, tolerances)
    quality = score_quality(run_id, manifest, {t:(by_type.get(t) if t in present_types else None) for t in {a["artifact_type"] for a in manifest.get("artifacts", [])}}, validations, MANDATORY)
    status = manifest.get("statuses", {})
    runs = [{"run_id":run_id,"design_id":manifest.get("design_id"),"design_name":manifest.get("design_name"),"flow":manifest.get("flow"),"flow_version":manifest.get("flow_version"),"technology":manifest.get("technology"),"process_node_nm":manifest.get("process_node_nm"),"timestamp":manifest.get("timestamp"),**{k:status.get(k) for k in ("flow_status","timing_status","routing_status","drc_status","antenna_status","lvs_status","overall_status")}}]
    cfg_path = run_dir / "config/config.json"; cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else None
    config = [{"run_id":run_id,"configuration":{k:str(v) for k,v in cfg.items()} if cfg else None, "source_artifact_path":"config/config.json", "artifact_type":"run_configuration","parser_name":"chipforge_mock","parser_version":"1.0.0","source_line_start":None,"source_line_end":None,"extraction_timestamp":datetime.now(timezone.utc)}]
    # Normalize parser keys to the declared Spark schemas.
    for row in violations:
        row.setdefault("violation_type", row.get("violation_type")); row.setdefault("layer", row.get("layer"))
    return {"runs":runs,"run_configurations":config,"run_artifacts":artifacts,"metric_observations":metrics,
        "timing_paths":paths,"cell_statistics":cells,"violations":violations,"validation_results":validations,"run_quality":[quality],"audit":audits}

def create_spark(app_name="ChipForgeBronzeToSilver"):
    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise RuntimeError("PySpark is required to write Silver Parquet datasets; install pyspark in the processing environment") from exc
    return SparkSession.builder.appName(app_name).getOrCreate()

def write_silver(spark, records: list[dict[str, Any]], output_uri: str, overwrite=False):
    """Write explicit-schema Parquet. Existing run partitions are preserved by default."""
    from .schemas import SCHEMAS
    for dataset in DATASETS:
        rows = records.get(dataset, [])
        if not rows: continue
        frame = spark.createDataFrame(rows, schema=SCHEMAS[dataset])
        path = f"{output_uri.rstrip('/')}/{dataset}"
        if not overwrite:
            # Processing is run-id incremental. Refuse to silently rewrite IDs already present.
            try:
                existing = spark.read.parquet(path).select("run_id").distinct()
                incoming = frame.select("run_id").distinct().join(existing, "run_id", "inner").limit(1).count()
                if incoming:
                    raise FileExistsError(f"Silver dataset {dataset} already contains this run_id; use --overwrite to replace")
            except FileNotFoundError:
                pass
            except Exception as exc:
                if "Path does not exist" not in str(exc) and "Unable to infer schema" not in str(exc): raise
        mode = "overwrite" if overwrite else "append"
        frame.write.mode(mode).partitionBy("run_id").parquet(path)

def write_audit(records, uri, spark=None):
    """Write JSON audit locally or append JSON via Spark to an S3A-compatible URI."""
    rows = records.get("audit", [])
    if not rows: return
    if uri.startswith("s3a://"):
        if spark is None: raise RuntimeError("Spark session is required for S3A audit writes")
        spark.createDataFrame(rows).write.mode("append").json(uri.rstrip("/") + "/processing_audit")
    else:
        path=Path(uri); path.mkdir(parents=True, exist_ok=True)
        target=path / "processing_audit.jsonl"
        with target.open("a",encoding="utf-8") as f:
            for row in rows: f.write(json.dumps(row,default=str)+"\n")

def main(argv=None):
    ap=argparse.ArgumentParser(description="Parse ChipForge Bronze reports and write Silver Parquet")
    ap.add_argument("--input",type=Path,default=Path("data/raw/runs")); ap.add_argument("--output",default="data/silver"); ap.add_argument("--overwrite",action="store_true")
    ap.add_argument("--tolerance",action="append",default=[],help="NAME=FLOAT validation tolerance")
    args=ap.parse_args(argv); tolerances={}
    for item in args.tolerance:
        name,value=item.split("=",1); tolerances[name]=float(value)
    dirs=sorted(p.parent for p in args.input.glob("*/manifest.json"))
    if not dirs: ap.error(f"no run manifests found in {args.input}")
    # Driver handles run manifests and parser records only; Spark writes with explicit schemas.
    batches=[process_run(run_dir,tolerances=tolerances) for run_dir in dirs]
    combined={dataset:[] for dataset in DATASETS}; combined["audit"]=[]
    for batch in batches:
        for name,rows in batch.items(): combined.setdefault(name,[]).extend(rows)
    spark=create_spark()
    audit_uri = args.output
    if not audit_uri.startswith(("s3a://", "s3://", "file://")):
        audit_uri = str(Path(audit_uri) / "_audit")
    write_silver(spark,combined,args.output,args.overwrite); write_audit(combined,audit_uri,spark)
    spark.stop(); return 0

if __name__ == "__main__": raise SystemExit(main())
