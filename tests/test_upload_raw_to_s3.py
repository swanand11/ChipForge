from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import upload_raw_to_s3 as uploader


def test_missing_and_empty_source(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        uploader.discover_files(tmp_path / "missing")
    with pytest.raises(ValueError, match="no eligible files"):
        uploader.discover_files(tmp_path)


def test_nested_mapping_and_paths_with_spaces(tmp_path):
    source = tmp_path / "runs"
    file = source / "RUN 1" / "timing" / "setup.rpt"
    file.parent.mkdir(parents=True)
    file.write_text("slack")
    files, skipped = uploader.discover_files(source)
    assert skipped == []
    assert uploader.object_key(files[0], source, "raw/runs/") == "raw/runs/RUN 1/timing/setup.rpt"


def test_secret_exclusion(tmp_path):
    source = tmp_path / "runs"
    source.mkdir()
    (source / "report.odd").write_text("ok")
    for name in (".env", "private.pem", "secret.key", "cert.p12", "cert.pfx"):
        (source / name).write_text("secret")
    files, skipped = uploader.discover_files(source)
    assert [p.name for p in files] == ["report.odd"]
    assert len(skipped) == 5


def test_symlinks_skipped(tmp_path):
    source = tmp_path / "runs"
    source.mkdir()
    outside = tmp_path / "outside.rpt"
    outside.write_text("private")
    (source / "linked.rpt").symlink_to(outside)
    (source / "real.rpt").write_text("safe")
    files, skipped = uploader.discover_files(source)
    assert [p.name for p in files] == ["real.rpt"]
    assert len(skipped) == 1


def test_dry_run_never_creates_client_or_uploads(tmp_path, monkeypatch, capsys):
    source = tmp_path / "runs"
    source.mkdir()
    (source / "x.rpt").write_text("x")
    monkeypatch.setattr(uploader, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(uploader, "boto3", None)
    assert uploader.main(["--bucket", "valid-bucket", "--source", "runs", "--dry-run"]) == 0
    assert "raw/runs/x.rpt" in capsys.readouterr().out


def test_success_and_failure_uploads(tmp_path):
    source = tmp_path / "runs"
    source.mkdir()
    first, second = source / "one.rpt", source / "two.rpt"
    first.write_text("1")
    second.write_text("2")

    class Client:
        def __init__(self): self.calls = []
        def upload_file(self, filename, bucket, key):
            self.calls.append((filename, bucket, key))
            if key.endswith("two.rpt"):
                raise OSError("network down")

    client = Client()
    failures = uploader.upload_files([first, second], source, "bucket-name", "raw/runs/", client)
    assert len(client.calls) == 2
    assert client.calls[0][2] == "raw/runs/one.rpt"
    assert len(failures) == 1 and "network down" in failures[0][1]


def test_main_returns_nonzero_on_upload_failure(tmp_path, monkeypatch):
    source = tmp_path / "runs"
    source.mkdir()
    (source / "x.rpt").write_text("x")
    monkeypatch.setattr(uploader, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(uploader, "boto3", SimpleNamespace(client=lambda *a, **k: SimpleNamespace(upload_file=lambda *a: (_ for _ in ()).throw(OSError("failed")))))
    assert uploader.main(["--bucket", "valid-bucket", "--source", "runs"]) == 1
