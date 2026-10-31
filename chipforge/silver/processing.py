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
S3A_SPARK_PACKAGES = (
    "org.apache.hadoop:hadoop-aws:3.3.4",
    "com.amazonaws:aws-java-sdk-bundle:1.12.262",
)

def normalize_timestamp_fields(rows, spark_schema):
    """Convert schema-declared timestamp values to naive UTC datetimes for Spark."""
    timestamp_fields = {
        field.name for field in spark_schema.fields
        if field.dataType.typeName().lower() == "timestamp"
    }
    normalized = []
    for row in rows:
        converted = dict(row)
        for name in timestamp_fields:
            value = converted.get(name)
            if value is None:
                continue
            if isinstance(value, str):
                # datetime.fromisoformat on older Python versions does not accept Z.
                value = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
            if isinstance(value, datetime) and value.tzinfo is not None:
                value = value.astimezone(timezone.utc).replace(tzinfo=None)
            converted[name] = value
        normalized.append(converted)
    return normalized

def _normalize_value(value, data_type, path, nullable=True):
    """Normalize a value recursively according to a Spark SQL datatype."""
    if value is None:
        if not nullable:
            raise ValueError(f"{path}: null is not allowed by the schema")
        return None

    type_name = data_type.typeName().lower()
    if type_name == "double":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{path}: expected a number for DoubleType, got {value!r} ({type(value).__name__})")
        return float(value)
    if type_name in {"byte", "short", "integer", "long"}:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{path}: expected an integer for {data_type.simpleString()}, got {value!r} ({type(value).__name__})")
        return value
    if type_name == "float":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{path}: expected a number for FloatType, got {value!r} ({type(value).__name__})")
        return float(value)
    if type_name == "string":
        if not isinstance(value, str):
            raise TypeError(f"{path}: expected str for StringType, got {value!r} ({type(value).__name__})")
        return value
    if type_name == "boolean":
        if not isinstance(value, bool):
            raise TypeError(f"{path}: expected bool for BooleanType, got {value!r} ({type(value).__name__})")
        return value
    if type_name == "timestamp":
        if isinstance(value, str):
            value = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        if not isinstance(value, datetime):
            raise TypeError(f"{path}: expected ISO timestamp or datetime, got {value!r} ({type(value).__name__})")
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo is not None else value
    if type_name == "date":
        from datetime import date
        if isinstance(value, datetime) or not isinstance(value, date):
            raise TypeError(f"{path}: expected date, got {value!r} ({type(value).__name__})")
        return value
    if type_name == "decimal":
        from decimal import Decimal
        if not isinstance(value, Decimal):
            try:
                value = Decimal(str(value))
            except Exception as exc:
                raise TypeError(f"{path}: cannot convert {value!r} to DecimalType") from exc
        return value
    if type_name == "binary":
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError(f"{path}: expected bytes for BinaryType, got {value!r} ({type(value).__name__})")
        return bytes(value)
    if type_name == "array":
        if not isinstance(value, (list, tuple)):
            raise TypeError(f"{path}: expected list/tuple for ArrayType, got {value!r} ({type(value).__name__})")
        return [_normalize_value(item, data_type.elementType, f"{path}[{index}]", data_type.containsNull)
                for index, item in enumerate(value)]
    if type_name == "map":
        if not isinstance(value, dict):
            raise TypeError(f"{path}: expected dict for MapType, got {value!r} ({type(value).__name__})")
        normalized = {}
        for key, item in value.items():
            normalized_key = _normalize_value(key, data_type.keyType, f"{path}.<key>", False)
            normalized[normalized_key] = _normalize_value(item, data_type.valueType,
                f"{path}[{key!r}]", data_type.valueContainsNull)
        return normalized
    if type_name == "struct":
        if hasattr(value, "asDict"):
            value = value.asDict(recursive=False)
        if isinstance(value, dict):
            normalized = {}
            for field in data_type.fields:
                field_value = value.get(field.name)
                normalized[field.name] = _normalize_value(field_value, field.dataType,
                    f"{path}.{field.name}", field.nullable)
            return normalized
        if isinstance(value, (list, tuple)):
            if len(value) != len(data_type.fields):
                raise TypeError(f"{path}: expected {len(data_type.fields)} struct values, got {len(value)}")
            return tuple(_normalize_value(item, field.dataType, f"{path}.{field.name}", field.nullable)
                         for item, field in zip(value, data_type.fields))
        raise TypeError(f"{path}: expected dict/Row/tuple for StructType, got {value!r} ({type(value).__name__})")
    if type_name == "null":
        raise TypeError(f"{path}: expected null for NullType, got {value!r} ({type(value).__name__})")
    # Keep additional Spark SQL types intact; Spark's schema validation remains authoritative.
    return value

def normalize_rows_for_schema(rows, spark_schema, dataset):
    """Apply schema-guided normalization to rows, including nested Spark types."""
    normalized_rows = []
    for row_index, row in enumerate(rows):
        try:
            if not isinstance(row, dict):
                raise TypeError(f"expected a dict row, got {row!r} ({type(row).__name__})")
            normalized = dict(row)
            for field in spark_schema.fields:
                value = row.get(field.name)
                normalized[field.name] = _normalize_value(
                    value, field.dataType, f"{dataset}.{field.name}[row {row_index}]", field.nullable
                )
            normalized_rows.append(normalized)
        except (TypeError, ValueError) as exc:
            raise type(exc)(f"{dataset} row {row_index}: {exc}") from exc
    return normalized_rows

def normalize_audit_rows(rows, audit_schema=None):
    """Normalize the existing processing audit fields to their explicit Spark schema."""
    if audit_schema is None:
        from .schemas import AUDIT_SCHEMA
        audit_schema = AUDIT_SCHEMA
    prepared = []
    boolean_fields = {field.name for field in audit_schema.fields
                      if field.dataType.typeName().lower() == "boolean"}
    for row_index, row in enumerate(rows):
        normalized = dict(row)
        for name in boolean_fields:
            value = normalized.get(name)
            if isinstance(value, int) and not isinstance(value, bool) and value in (0, 1):
                normalized[name] = bool(value)
        prepared.append(normalized)
    return normalize_rows_for_schema(prepared, audit_schema, "processing_audit")

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

def create_spark(app_name="ChipForgeBronzeToSilver", spark_packages=None):
    try:
        from pyspark.sql import SparkSession
    except ImportError as exc:
        raise RuntimeError("PySpark is required to write Silver Parquet datasets; install pyspark in the processing environment") from exc
    builder = SparkSession.builder.appName(app_name)
    if spark_packages:
        builder = builder.config("spark.jars.packages", ",".join(spark_packages))
    return builder.getOrCreate()

def write_silver(spark, records: list[dict[str, Any]], output_uri: str, overwrite=False):
    """Write explicit-schema Parquet. Existing run partitions are preserved by default."""
    from .schemas import SCHEMAS
    for dataset in DATASETS:
        rows = records.get(dataset, [])
        if not rows: continue
        rows = normalize_rows_for_schema(rows, SCHEMAS[dataset], dataset)
        try:
            frame = spark.createDataFrame(rows, schema=SCHEMAS[dataset])
        except Exception as exc:
            raise TypeError(f"Spark rejected normalized rows for Silver dataset {dataset}: {exc}") from exc
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
    from .schemas import AUDIT_SCHEMA
    normalized_rows = normalize_audit_rows(rows, AUDIT_SCHEMA)
    if uri.startswith("s3a://"):
        if spark is None: raise RuntimeError("Spark session is required for S3A audit writes")
        spark.createDataFrame(normalized_rows, schema=AUDIT_SCHEMA).write.mode("append").json(uri.rstrip("/") + "/processing_audit")
    else:
        path=Path(uri); path.mkdir(parents=True, exist_ok=True)
        target=path / "processing_audit.jsonl"
        with target.open("a",encoding="utf-8") as f:
            for row in normalized_rows: f.write(json.dumps(row,default=_audit_json_default)+"\n")

def _audit_json_default(value):
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.isoformat() + "Z"
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)

def main(argv=None):
    ap=argparse.ArgumentParser(description="Parse ChipForge Bronze reports and write Silver Parquet")
    ap.add_argument("--input",type=Path,default=Path("data/raw/runs")); ap.add_argument("--output",default="data/silver"); ap.add_argument("--overwrite",action="store_true")
    ap.add_argument("--tolerance",action="append",default=[],help="NAME=FLOAT validation tolerance")
    ap.add_argument("--spark-log-level", default="WARN", help="Spark driver/executor log level (default: WARN)")
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
    packages = list(S3A_SPARK_PACKAGES)
    spark=create_spark(spark_packages=packages)
    spark.sparkContext.setLogLevel(args.spark_log_level)
    audit_uri = args.output
    if not audit_uri.startswith(("s3a://", "s3://", "file://")):
        audit_uri = str(Path(audit_uri) / "_audit")
    write_silver(spark,combined,args.output,args.overwrite); write_audit(combined,audit_uri,spark)
    spark.stop(); return 0

if __name__ == "__main__": raise SystemExit(main())
