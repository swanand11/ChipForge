import unittest
from datetime import datetime

from chipforge.silver.processing import normalize_rows_for_schema


class FakeType:
    def __init__(self, name, **kwargs):
        self._name = name
        self.__dict__.update(kwargs)

    def typeName(self):
        return self._name

    def simpleString(self):
        return self._name


class FakeField:
    def __init__(self, name, data_type, nullable=True):
        self.name = name
        self.dataType = data_type
        self.nullable = nullable


class FakeSchema:
    def __init__(self, *fields):
        self.fields = list(fields)


def field(name, kind, nullable=True, **kwargs):
    return FakeField(name, FakeType(kind, **kwargs), nullable)


class SilverTypeNormalizationTests(unittest.TestCase):
    def test_converts_integer_double_values_for_every_affected_dataset(self):
        affected = {
            "metric_observations": ("metric_value",),
            "timing_paths": ("required_time", "arrival_time", "slack", "data_path_delay", "clock_skew"),
            "violations": ("ratio", "limit"),
            "validation_results": ("difference", "tolerance"),
            "run_quality": (
                "artifact_completeness_score", "parsing_success_score", "schema_validity_score",
                "cross_report_consistency_score", "overall_data_quality_score",
            ),
        }
        for dataset, names in affected.items():
            with self.subTest(dataset=dataset):
                schema = FakeSchema(*(field(name, "double") for name in names))
                row = {name: 0 for name in names}
                normalized = normalize_rows_for_schema([row], schema, dataset)[0]
                self.assertTrue(all(type(normalized[name]) is float for name in names))
                self.assertTrue(all(normalized[name] == 0.0 for name in names))

    def test_preserves_none_in_nullable_double_fields(self):
        schema = FakeSchema(field("metric_value", "double"), field("difference", "double"))
        normalized = normalize_rows_for_schema([{"metric_value": None, "difference": 0}], schema, "validation_results")
        self.assertIsNone(normalized[0]["metric_value"])
        self.assertEqual(normalized[0]["difference"], 0.0)

    def test_preserves_declared_scalar_types_and_normalizes_timestamp(self):
        schema = FakeSchema(
            field("count", "integer"), field("wide_count", "long"), field("enabled", "boolean"),
            field("label", "string"), field("timestamp", "timestamp"),
        )
        row = {"count": 2, "wide_count": 3, "enabled": True, "label": "2026-10-08T10:01:00Z",
               "timestamp": "2026-10-08T15:31:00+05:30"}
        normalized = normalize_rows_for_schema([row], schema, "runs")[0]
        self.assertIs(type(normalized["count"]), int)
        self.assertIs(type(normalized["wide_count"]), int)
        self.assertIs(type(normalized["enabled"]), bool)
        self.assertIs(type(normalized["label"]), str)
        self.assertEqual(normalized["timestamp"], datetime(2026, 10, 8, 10, 1))

    def test_normalizes_nested_map_and_array_values_by_schema(self):
        map_type = FakeType("map", keyType=FakeType("string"), valueType=FakeType("double"), valueContainsNull=True)
        array_type = FakeType("array", elementType=FakeType("double"), containsNull=True)
        schema = FakeSchema(field("configuration", "map", keyType=map_type.keyType,
                                  valueType=map_type.valueType, valueContainsNull=True),
                            field("values", "array", elementType=array_type.elementType, containsNull=True))
        normalized = normalize_rows_for_schema(
            [{"configuration": {"zero": 0, "missing": None}, "values": [1, None, 2.5]}], schema, "run_configurations"
        )[0]
        self.assertEqual(normalized["configuration"], {"zero": 0.0, "missing": None})
        self.assertEqual(normalized["values"], [1.0, None, 2.5])

    def test_reports_dataset_field_and_offending_value(self):
        schema = FakeSchema(field("metric_value", "double"))
        with self.assertRaisesRegex(TypeError, r"metric_observations\.metric_value.*'bad'"):
            normalize_rows_for_schema([{"metric_value": "bad"}], schema, "metric_observations")

    def test_does_not_accept_wrong_type_for_non_double_fields(self):
        schema = FakeSchema(field("count", "integer"), field("label", "string"))
        with self.assertRaisesRegex(TypeError, r"runs\.count.*1\.5"):
            normalize_rows_for_schema([{"count": 1.5, "label": "ok"}], schema, "runs")


if __name__ == "__main__":
    unittest.main()
