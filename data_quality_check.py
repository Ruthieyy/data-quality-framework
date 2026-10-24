#!/usr/bin/env python3
"""Validate a small people CSV from disk or S3.

Rules, and only these rules:

- ``name`` exists and is not null
- ``age`` exists, is numeric, and is between 0 and 120 inclusive

A failing rule prints a report and exits 1 so a downstream step can stop.
"""

from __future__ import annotations

import argparse
import io
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

import pandas as pd

NAME_COLUMN = "name"
AGE_COLUMN = "age"
AGE_MIN = 0
AGE_MAX = 120
MAX_REPORTED_ROWS = 20

LOCAL_ENV = "DQ_LOCAL_PATH"
BUCKET_ENV = "DQ_S3_BUCKET"
KEY_ENV = "DQ_S3_KEY"


class ConfigError(Exception):
    """CLI arguments and environment variables do not select one input."""


class ReadError(Exception):
    """The CSV could not be read or parsed."""


@dataclass(frozen=True)
class LocalSource:
    path: str


@dataclass(frozen=True)
class S3Source:
    bucket: str
    key: str


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


def _present(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _sample(items: list[str], total: int) -> str:
    shown = ", ".join(items[:MAX_REPORTED_ROWS])
    extra = total - MAX_REPORTED_ROWS
    if extra > 0:
        return f"{shown} (+{extra} more)"
    return shown


def check_name_not_null(df: pd.DataFrame) -> CheckResult:
    """Fail when ``name`` is missing or any value is null."""
    if NAME_COLUMN not in df.columns:
        return CheckResult("name_not_null", False, f"column '{NAME_COLUMN}' is missing")

    null_indexes = df.index[df[NAME_COLUMN].isna()].tolist()
    if null_indexes:
        rendered = [str(idx) for idx in null_indexes]
        return CheckResult(
            "name_not_null",
            False,
            f"{len(null_indexes)} null value(s) in '{NAME_COLUMN}' "
            f"at data row index {_sample(rendered, len(null_indexes))}",
        )
    return CheckResult(
        "name_not_null",
        True,
        f"'{NAME_COLUMN}' has no nulls ({len(df)} row(s))",
    )


def check_age_in_range(
    df: pd.DataFrame,
    minimum: float = AGE_MIN,
    maximum: float = AGE_MAX,
) -> CheckResult:
    """Fail when ``age`` is missing, null, non-numeric, or outside the inclusive range."""
    check_name = f"age_between_{minimum:g}_and_{maximum:g}"
    if AGE_COLUMN not in df.columns:
        return CheckResult(check_name, False, f"column '{AGE_COLUMN}' is missing")

    numeric = pd.to_numeric(df[AGE_COLUMN], errors="coerce")
    invalid = numeric.isna() | (numeric < minimum) | (numeric > maximum)
    bad_indexes = df.index[invalid].tolist()
    if not bad_indexes:
        return CheckResult(
            check_name,
            True,
            f"'{AGE_COLUMN}' is numeric and within [{minimum:g}, {maximum:g}] ({len(df)} row(s))",
        )

    samples: list[str] = []
    for idx in bad_indexes[:MAX_REPORTED_ROWS]:
        raw = df.at[idx, AGE_COLUMN]
        shown = "null" if pd.isna(raw) else str(raw)
        samples.append(f"{idx} ({shown})")
    detail = (
        f"{len(bad_indexes)} value(s) in '{AGE_COLUMN}' are null, non-numeric, "
        f"or outside [{minimum:g}, {maximum:g}]: {_sample(samples, len(bad_indexes))}"
    )
    return CheckResult(check_name, False, detail)


def run_checks(df: pd.DataFrame) -> list[CheckResult]:
    return [check_name_not_null(df), check_age_in_range(df)]


def format_report(source: str, row_count: int, results: list[CheckResult]) -> str:
    passed = sum(1 for result in results if result.passed)
    status = "PASSED" if passed == len(results) else "FAILED"
    lines = [
        "Data quality report",
        f"Source: {source}",
        f"Rows: {row_count}",
        "",
    ]
    for result in results:
        label = "PASS" if result.passed else "FAIL"
        lines.append(f"{label}  {result.name}: {result.detail}")
    lines.append("")
    lines.append(f"Result: {status} ({passed}/{len(results)} checks passed)")
    return "\n".join(lines)


def describe_source(source: LocalSource | S3Source) -> str:
    if isinstance(source, LocalSource):
        return source.path
    return f"s3://{source.bucket}/{source.key}"


def resolve_source(args: argparse.Namespace, env: Mapping[str, str]) -> LocalSource | S3Source:
    """Pick a local path or an S3 object.

    ``--local`` wins over any S3 settings. If ``--bucket`` or ``--key`` is set,
    the other half may come from the environment, and ``DQ_LOCAL_PATH`` is ignored.
    With no flags, use exactly one of ``DQ_LOCAL_PATH`` or both S3 variables.
    """
    cli_local = _present(args.local)
    cli_bucket = _present(args.bucket)
    cli_key = _present(args.key)

    if cli_local:
        return LocalSource(cli_local)

    if cli_bucket or cli_key:
        bucket = cli_bucket or _present(env.get(BUCKET_ENV))
        key = cli_key or _present(env.get(KEY_ENV))
        missing = []
        if not bucket:
            missing.append(f"--bucket or {BUCKET_ENV}")
        if not key:
            missing.append(f"--key or {KEY_ENV}")
        if missing:
            raise ConfigError("S3 input needs both a bucket and a key; missing " + " and ".join(missing))
        return S3Source(bucket, key)

    env_local = _present(env.get(LOCAL_ENV))
    env_bucket = _present(env.get(BUCKET_ENV))
    env_key = _present(env.get(KEY_ENV))
    has_s3 = bool(env_bucket or env_key)

    if env_local and has_s3:
        raise ConfigError(
            f"Both {LOCAL_ENV} and S3 settings are set. "
            "Pass --local or --bucket/--key to choose one input."
        )
    if env_local:
        return LocalSource(env_local)
    if env_bucket and env_key:
        return S3Source(env_bucket, env_key)
    if has_s3:
        raise ConfigError(f"S3 input needs both {BUCKET_ENV} and {KEY_ENV}.")
    raise ConfigError(
        "Provide --local PATH, or --bucket and --key. "
        f"Environment fallbacks: {LOCAL_ENV}, {BUCKET_ENV}, {KEY_ENV}."
    )


def load_local(path: str) -> pd.DataFrame:
    if not os.path.isfile(path):
        raise ReadError(f"local file not found: {path}")
    try:
        return pd.read_csv(path, encoding="utf-8")
    except pd.errors.EmptyDataError as exc:
        raise ReadError(f"local file has no CSV data: {path}") from exc
    except Exception as exc:
        raise ReadError(f"could not read local file {path}: {exc}") from exc


def load_s3(bucket: str, key: str, s3_client=None) -> pd.DataFrame:
    """Read one object with boto3. ``s3_client`` is injectable for tests."""
    client = s3_client if s3_client is not None else _default_s3_client()
    location = f"s3://{bucket}/{key}"
    try:
        response = client.get_object(Bucket=bucket, Key=key)
        body = response["Body"].read()
    except Exception as exc:
        raise ReadError(f"could not read {location}: {exc}") from exc

    if not isinstance(body, (bytes, bytearray)) or body.strip() == b"":
        raise ReadError(f"{location} is empty or not bytes")
    try:
        return pd.read_csv(io.BytesIO(body), encoding="utf-8")
    except pd.errors.EmptyDataError as exc:
        raise ReadError(f"{location} has no CSV data") from exc
    except Exception as exc:
        raise ReadError(f"could not parse {location} as CSV: {exc}") from exc


def _default_s3_client():
    try:
        import boto3
    except ImportError as exc:
        raise ReadError("boto3 is required to read from S3; pip install -r requirements.txt") from exc
    return boto3.client("s3")


def read_source(source: LocalSource | S3Source, s3_client=None) -> pd.DataFrame:
    if isinstance(source, LocalSource):
        return load_local(source.path)
    return load_s3(source.bucket, source.key, s3_client=s3_client)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a people CSV from a local file or S3. "
            "Checks that name is not null and that age is numeric and within 0..120 inclusive. "
            "Exits 0 when every check passes and 1 when any check fails."
        )
    )
    parser.add_argument("--local", help=f"Path to a local CSV. Overrides S3 settings. Falls back to {LOCAL_ENV}.")
    parser.add_argument("--bucket", help=f"S3 bucket. Falls back to {BUCKET_ENV}.")
    parser.add_argument("--key", help=f"S3 object key. Falls back to {KEY_ENV}.")
    return parser


def main(
    argv: list[str] | None = None,
    env: Mapping[str, str] | None = None,
    s3_client=None,
) -> int:
    """Run the checks. Returns 0 pass, 1 failed checks, 2 bad config, 3 unreadable input."""
    args = build_parser().parse_args(argv)
    try:
        source = resolve_source(args, os.environ if env is None else env)
    except ConfigError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2

    try:
        frame = read_source(source, s3_client=s3_client)
    except ReadError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 3

    results = run_checks(frame)
    print(format_report(describe_source(source), len(frame), results))
    return 0 if all(result.passed for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
