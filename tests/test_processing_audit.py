import sys
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from chipforge.silver.processing import normalize_audit_rows, write_audit


class FakeType:
    def __init__(self, name, **kwargs):
        self.name = name
        self.__dict__.update(kwargs)

    def typeName(self):
        return self.name

    def simpleString(self):
        return self.name


class FakeField:
    def __init__(self, name, data_type, nullable=True):
        self.name = name
        self.dataType = data_type
        self.nullable = nullable


class FakeSchema:
    fields = [
        FakeField("run_id", FakeType("string"), False),
        FakeField("source_artifact_path", FakeType("string")),
        FakeField("artifact_type", FakeType("string")),
        FakeField("format", FakeType("string")),
        FakeField("stage", FakeType("string")),
        FakeField("exists", FakeType("boolean")),
        FakeField("parse_success", FakeType("boolean")),
        FakeField("parse_errors", FakeType("array", elementType=FakeType("string"), containsNull=False)),
        FakeField("parser_name", FakeType("string")),
        FakeField("parser_version", FakeType("string")),
        FakeField("extraction_timestamp", FakeType("timestamp")),
    ]


class ProcessingAuditTests(unittest.TestCase):
    def test_all_null_optional_fields_are_preserved(self):
        row = {"run_id": "RUN_1", "exists": True, "parse_success": False, "parse_errors": []}
        normalized = normalize_audit_rows([row], FakeSchema)[0]
        for name in ("source_artifact_path", "artifact_type", "format", "stage", "parser_name", "parser_version", "extraction_timestamp"):
            self.assertIsNone(normalized[name])
        self.assertEqual(normalized["run_id"], "RUN_1")

    def test_mixed_numeric_boolean_flags_and_timestamp_representations(self):
        rows = [
            {"run_id": "RUN_1", "exists": 1, "parse_success": 0, "parse_errors": [],
             "extraction_timestamp": "2026-10-08T10:01:00Z"},
            {"run_id": "RUN_2", "exists": False, "parse_success": True, "parse_errors": [],
             "extraction_timestamp": datetime(2026, 10, 8, 15, 31, tzinfo=timezone(timedelta(hours=5, minutes=30)))},
        ]
        normalized = normalize_audit_rows(rows, FakeSchema)
        self.assertIs(normalized[0]["exists"], True)
        self.assertIs(normalized[0]["parse_success"], False)
        self.assertEqual(normalized[0]["extraction_timestamp"], datetime(2026, 10, 8, 10, 1))
        self.assertEqual(normalized[1]["extraction_timestamp"], datetime(2026, 10, 8, 10, 1))

    def test_empty_audit_still_writes_with_explicit_schema(self):
        captured = {}

        class Writer:
            def mode(self, value):
                captured["mode"] = value
                return self
            def json(self, path):
                captured["path"] = path

        class Frame:
            write = Writer()

        class Spark:
            def createDataFrame(self, rows, schema=None):
                captured["rows"] = rows
                captured["schema"] = schema
                return Frame()

        fake_schema_module = types.ModuleType("chipforge.silver.schemas")
        fake_schema_module.AUDIT_SCHEMA = FakeSchema
        with patch.dict(sys.modules, {"chipforge.silver.schemas": fake_schema_module}):
            write_audit({"audit": []}, "s3a://bucket/output", Spark())
        self.assertEqual(captured["rows"], [])
        self.assertIs(captured["schema"], FakeSchema)
        self.assertEqual(captured["mode"], "append")
        self.assertEqual(captured["path"], "s3a://bucket/output/processing_audit")


if __name__ == "__main__":
    unittest.main()
