#!/usr/bin/env python3
"""Upload ChipForge raw run artifacts to Amazon S3."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Iterable

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError, PartialCredentialsError
except ImportError:  # Keep --help usable before optional dependency installation.
    boto3 = None  # type: ignore[assignment]
    BotoCoreError = ClientError = NoCredentialsError = PartialCredentialsError = Exception  # type: ignore[misc,assignment]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".venv", "venv", "env", "ENV", "__pycache__", ".tox", ".nox", "node_modules", ".idea"}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}
LOGGER = logging.getLogger("upload_raw_to_s3")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload files under data/raw/runs to S3 while preserving their paths.",
        epilog=("PowerShell: $env:CHIPFORGE_BUCKET='my-bucket'; "
                "python scripts/upload_raw_to_s3.py  (clear with Remove-Item Env:CHIPFORGE_BUCKET)"),
    )
    parser.add_argument("--bucket", default=os.environ.get("CHIPFORGE_BUCKET", "chipforge-raw-swanand11"), help="S3 bucket (default: chipforge-raw-swanand11; override with CHIPFORGE_BUCKET)")
    parser.add_argument("--source", default="data/raw/runs", help="source directory; relative paths are project-root relative")
    parser.add_argument("--prefix", default="raw/runs/", help="destination key prefix")
    parser.add_argument("--region", default="ap-south-1", help="AWS region (default: ap-south-1)")
    parser.add_argument("--dry-run", action="store_true", help="show planned uploads without contacting S3")
    parser.add_argument("--verbose", action="store_true", help="enable detailed logging")
    return parser.parse_args(argv)


def resolve_source(source: str) -> Path:
    path = Path(source).expanduser()
    return (path if path.is_absolute() else PROJECT_ROOT / path).resolve()


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def discover_files(source: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Find eligible regular files without following links outside source."""
    if not source.is_dir():
        raise ValueError(f"Source directory does not exist or is not a directory: {source}")
    root = source.resolve()
    selected: list[Path] = []
    skipped: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        kept_dirs: list[str] = []
        for name in dirs:
            candidate = current_path / name
            if name in EXCLUDED_DIRS:
                skipped.append((candidate, "excluded environment/repository directory"))
            elif candidate.is_symlink():
                skipped.append((candidate, "symbolic link directory"))
            else:
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in names:
            path = current_path / name
            if path.is_symlink():
                skipped.append((path, "symbolic link"))
                continue
            if name == ".env" or path.suffix.lower() in SECRET_SUFFIXES:
                skipped.append((path, "secret/private-key file"))
                continue
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                skipped.append((path, f"unreadable path: {exc}"))
                continue
            if not _within(resolved, root):
                skipped.append((path, "resolves outside source directory"))
                continue
            if not resolved.is_file() or resolved in seen:
                continue
            seen.add(resolved)
            selected.append(path)
    selected.sort(key=lambda p: p.relative_to(root).as_posix())
    skipped.sort(key=lambda item: str(item[0]))
    if not selected:
        raise ValueError(f"Source directory contains no eligible files: {source}")
    return selected, skipped


def object_key(path: Path, source: Path, prefix: str) -> str:
    """Build an S3 key from a file's path below source."""
    relative = path.relative_to(source).as_posix()
    return f"{prefix.strip('/')}/{relative}" if prefix.strip("/") else relative


def _error_reason(exc: Exception) -> str:
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        return "AWS credentials are missing or incomplete; configure the standard AWS credential provider chain."
    if isinstance(exc, ClientError):
        response = exc.response
        code = response.get("Error", {}).get("Code", "Unknown")
        message = response.get("Error", {}).get("Message", str(exc))
        if code in {"AccessDenied", "Forbidden", "AllAccessDisabled"}:
            return f"Insufficient S3 permissions ({code}): {message}"
        if code in {"InvalidBucketName", "NoSuchBucket"}:
            return f"Invalid or unavailable bucket ({code}): {message}"
        if code in {"PermanentRedirect", "AuthorizationHeaderMalformed", "IncorrectEndpoint"}:
            return f"Bucket region may be incorrect ({code}): {message}"
        return f"AWS error ({code}): {message}"
    if isinstance(exc, BotoCoreError):
        return f"AWS/network error: {exc}"
    return str(exc)


def upload_files(files: Iterable[Path], source: Path, bucket: str, prefix: str, client: object) -> list[tuple[Path, str]]:
    failures: list[tuple[Path, str]] = []
    for index, path in enumerate(files, start=1):
        key = object_key(path, source, prefix)
        try:
            client.upload_file(str(path), bucket, key)  # type: ignore[attr-defined]
            LOGGER.info("Uploaded %s -> s3://%s/%s", path, bucket, key)
        except Exception as exc:  # Continue through per-file AWS and I/O errors.
            reason = _error_reason(exc)
            failures.append((path, reason))
            LOGGER.error("Failed %s (%d): %s", path, index, reason)
        print(f"Progress: {index} files processed")
    return failures


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(levelname)s: %(message)s")
    if not args.bucket:
        LOGGER.error("Specify --bucket or set CHIPFORGE_BUCKET.")
        return 2
    if not args.bucket.strip() or "/" in args.bucket or len(args.bucket) < 3 or len(args.bucket) > 63:
        LOGGER.error("Invalid bucket name: %r", args.bucket)
        return 2
    source = resolve_source(args.source)
    try:
        files, skipped = discover_files(source)
    except ValueError as exc:
        LOGGER.error("%s", exc)
        return 2
    prefix = args.prefix.strip("/")
    prefix = f"{prefix}/" if prefix else ""
    print(f"Source directory: {source}\nDestination: s3://{args.bucket}/{prefix}\nFiles discovered: {len(files) + len(skipped)}\nFiles selected: {len(files)}\nFiles skipped: {len(skipped)}")
    for path, reason in skipped:
        LOGGER.warning("Skipped %s: %s", path, reason)
    for path in files:
        print(f"{path} -> s3://{args.bucket}/{object_key(path, source, prefix)}")
    if args.dry_run:
        print("Dry run complete; no AWS requests made.")
        return 0
    if boto3 is None:
        LOGGER.error("Boto3 is required. Install it with: python -m pip install boto3")
        return 2
    try:
        client = boto3.client("s3", region_name=args.region)
    except Exception as exc:
        LOGGER.error("Could not initialize AWS S3 client: %s", _error_reason(exc))
        return 1
    failures = upload_files(files, source, args.bucket, prefix, client)
    print(f"Upload complete: {len(files) - len(failures)} succeeded, {len(failures)} failed.")
    if failures:
        for path, reason in failures:
            print(f"FAILED: {path}: {reason}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
