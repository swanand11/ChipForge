from datetime import datetime, timezone, timedelta

from chipforge.silver.processing import normalize_timestamp_fields


class _DataType:
    def __init__(self, name):
        self.name = name

    def typeName(self):
        return self.name


class _Field:
    def __init__(self, name, data_type):
        self.name = name
        self.dataType = _DataType(data_type)


class _Schema:
    fields = [_Field("created_at", "timestamp"), _Field("label", "string")]


def test_normalizes_iso_utc_z_timestamp():
    rows = normalize_timestamp_fields(
        [{"created_at": "2026-10-08T10:01:00Z", "label": "2026-10-08T10:01:00Z"}],
        _Schema(),
    )
    assert rows[0]["created_at"] == datetime(2026, 10, 8, 10, 1)
    assert rows[0]["label"] == "2026-10-08T10:01:00Z"


def test_normalizes_offset_timestamp_to_naive_utc():
    rows = normalize_timestamp_fields(
        [{"created_at": "2026-10-08T15:31:00+05:30", "label": "offset"}], _Schema()
    )
    assert rows[0]["created_at"] == datetime(2026, 10, 8, 10, 1)
    assert rows[0]["created_at"].tzinfo is None


def test_preserves_null_timestamp():
    rows = normalize_timestamp_fields([{"created_at": None, "label": "null"}], _Schema())
    assert rows[0]["created_at"] is None


def test_handles_datetime_values_idempotently_and_normalizes_aware_values():
    already_naive = datetime(2026, 10, 8, 10, 1)
    aware = datetime(2026, 10, 8, 15, 31, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    rows = normalize_timestamp_fields(
        [{"created_at": already_naive, "label": "naive"}, {"created_at": aware, "label": "aware"}],
        _Schema(),
    )
    assert rows[0]["created_at"] is already_naive
    assert rows[1]["created_at"] == datetime(2026, 10, 8, 10, 1)
    assert rows[1]["created_at"].tzinfo is None
