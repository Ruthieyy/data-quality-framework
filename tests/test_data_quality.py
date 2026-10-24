"""Tests for the two people-CSV rules, input selection, and exit codes."""

from pathlib import Path

import pandas as pd
import pytest

import data_quality_check as dq


class FakeBody:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload


class FakeS3:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls: list[tuple[str, str]] = []

    def get_object(self, Bucket: str, Key: str):
        self.calls.append((Bucket, Key))
        return {"Body": FakeBody(self.payload)}


class ExplodingS3:
    def get_object(self, Bucket: str, Key: str):
        raise AssertionError(f"S3 should not be called for {Bucket}/{Key}")


def test_repository_does_not_keep_the_removed_ge_api_or_hardcoded_bucket():
    source = Path(dq.__file__).read_text(encoding="utf-8")
    assert "great_expectations" not in source
    assert "from_pandas" not in source
    assert "yu-data-lake-test" not in source


def test_name_not_null_passes():
    frame = pd.DataFrame({"name": ["Ada", "Grace"], "age": [36, 40]})
    result = dq.check_name_not_null(frame)
    assert result.passed
    assert result.name == "name_not_null"


def test_name_null_fails_with_index():
    frame = pd.DataFrame({"name": ["Ada", None, "Grace"], "age": [1, 2, 3]})
    result = dq.check_name_not_null(frame)
    assert not result.passed
    assert "data row index 1" in result.detail


def test_blank_name_is_not_treated_as_null():
    """Documented limitation: the rule is null-only, so whitespace passes."""
    frame = pd.DataFrame({"name": ["  "], "age": [30]})
    assert dq.check_name_not_null(frame).passed


def test_missing_name_column_fails():
    frame = pd.DataFrame({"age": [30]})
    result = dq.check_name_not_null(frame)
    assert not result.passed
    assert "missing" in result.detail


def test_age_boundaries_pass_and_outside_fails():
    passing = pd.DataFrame({"name": ["A", "B", "C"], "age": [0, 120, 30.5]})
    assert dq.check_age_in_range(passing).passed

    failing = pd.DataFrame({"name": ["A", "B"], "age": [-0.1, 120.1]})
    result = dq.check_age_in_range(failing)
    assert not result.passed
    assert "0 (-0.1)" in result.detail
    assert "1 (120.1)" in result.detail


def test_null_and_non_numeric_age_fail():
    frame = pd.DataFrame({"name": ["A", "B"], "age": [None, "nope"]})
    result = dq.check_age_in_range(frame)
    assert not result.passed
    assert "0 (null)" in result.detail
    assert "1 (nope)" in result.detail


def test_missing_age_column_fails():
    frame = pd.DataFrame({"name": ["Ada"]})
    result = dq.check_age_in_range(frame)
    assert not result.passed
    assert result.name == "age_between_0_and_120"
    assert "missing" in result.detail


def test_failure_list_is_capped():
    frame = pd.DataFrame({"name": ["A"] * 25, "age": [-1] * 25})
    result = dq.check_age_in_range(frame)
    assert "(+5 more)" in result.detail
    assert result.detail.count("(-1)") == dq.MAX_REPORTED_ROWS


def test_empty_frame_with_columns_passes_both_rules():
    frame = pd.DataFrame({"name": pd.Series(dtype="object"), "age": pd.Series(dtype="float")})
    results = dq.run_checks(frame)
    assert [result.passed for result in results] == [True, True]


def test_report_lists_each_check_and_the_summary():
    frame = pd.DataFrame({"name": ["Ada", None], "age": [30, 30]})
    report = dq.format_report("sample.csv", len(frame), dq.run_checks(frame))
    assert "Source: sample.csv" in report
    assert "Rows: 2" in report
    assert "FAIL  name_not_null:" in report
    assert "PASS  age_between_0_and_120:" in report
    assert "Result: FAILED (1/2 checks passed)" in report


def test_resolve_local_flag_ignores_s3_env():
    args = dq.build_parser().parse_args(["--local", "people.csv", "--bucket", "ignored"])
    source = dq.resolve_source(args, {dq.BUCKET_ENV: "b", dq.KEY_ENV: "k"})
    assert source == dq.LocalSource("people.csv")


def test_resolve_partial_cli_fills_from_env():
    args = dq.build_parser().parse_args(["--bucket", "lake"])
    source = dq.resolve_source(args, {dq.KEY_ENV: "incoming/people.csv"})
    assert source == dq.S3Source("lake", "incoming/people.csv")


def test_resolve_env_only_paths():
    local_args = dq.build_parser().parse_args([])
    assert dq.resolve_source(local_args, {dq.LOCAL_ENV: "in.csv"}) == dq.LocalSource("in.csv")

    s3_args = dq.build_parser().parse_args([])
    assert dq.resolve_source(s3_args, {dq.BUCKET_ENV: "b", dq.KEY_ENV: "k"}) == dq.S3Source("b", "k")


@pytest.mark.parametrize(
    "env",
    [
        {},
        {dq.BUCKET_ENV: "only-bucket"},
        {dq.LOCAL_ENV: "in.csv", dq.BUCKET_ENV: "b", dq.KEY_ENV: "k"},
        {dq.LOCAL_ENV: "   "},
    ],
)
def test_resolve_rejects_missing_or_ambiguous_input(env):
    args = dq.build_parser().parse_args([])
    with pytest.raises(dq.ConfigError):
        dq.resolve_source(args, env)


def test_main_local_sample_passes(capsys):
    code = dq.main(["--local", "sample_data/people.csv"], env={})
    captured = capsys.readouterr()
    assert code == 0
    assert captured.err == ""
    assert "Result: PASSED (2/2 checks passed)" in captured.out
    assert "Source: sample_data/people.csv" in captured.out


def test_main_invalid_sample_exits_nonzero_and_prints_failures(capsys):
    code = dq.main(["--local", "sample_data/people_invalid.csv"], env={})
    captured = capsys.readouterr()
    assert code == 1
    assert "FAIL  name_not_null:" in captured.out
    assert "FAIL  age_between_0_and_120:" in captured.out
    assert "Result: FAILED (0/2 checks passed)" in captured.out
    assert "data row index 1" in captured.out
    assert "2 (-1)" in captured.out
    assert "3 (121)" in captured.out
    assert "4 (not_a_number)" in captured.out
    assert "5 (null)" in captured.out


def test_main_header_only_passes_and_empty_file_is_a_read_error(tmp_path, capsys):
    header_only = tmp_path / "header.csv"
    header_only.write_text("name,age\n", encoding="utf-8")
    assert dq.main(["--local", str(header_only)], env={}) == 0
    assert "Rows: 0" in capsys.readouterr().out

    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    assert dq.main(["--local", str(empty)], env={}) == 3
    assert "no CSV data" in capsys.readouterr().err


def test_main_missing_file_and_missing_args(tmp_path, capsys):
    missing = tmp_path / "nope.csv"
    assert dq.main(["--local", str(missing)], env={}) == 3
    assert "not found" in capsys.readouterr().err

    assert dq.main([], env={}) == 2
    assert "Provide --local" in capsys.readouterr().err


def test_main_local_flag_does_not_touch_s3(tmp_path, capsys):
    path = tmp_path / "ok.csv"
    path.write_text("name,age\nAda,36\n", encoding="utf-8")
    code = dq.main(
        ["--local", str(path), "--bucket", "should-not-read", "--key", "nope.csv"],
        env={dq.BUCKET_ENV: "also-ignored", dq.KEY_ENV: "also.csv"},
        s3_client=ExplodingS3(),
    )
    assert code == 0
    assert f"Source: {path}" in capsys.readouterr().out


def test_main_reads_s3_with_injected_client(capsys):
    payload = b"name,age\nAda,36\n"
    client = FakeS3(payload)
    code = dq.main(
        ["--bucket", "demo-bucket", "--key", "raw/people.csv"],
        env={},
        s3_client=client,
    )
    captured = capsys.readouterr()
    assert code == 0
    assert client.calls == [("demo-bucket", "raw/people.csv")]
    assert "Source: s3://demo-bucket/raw/people.csv" in captured.out
    assert "Result: PASSED" in captured.out


def test_main_s3_failure_exits_nonzero(capsys):
    client = FakeS3(b"name,age\nAda,200\n")
    code = dq.main([], env={dq.BUCKET_ENV: "lake", dq.KEY_ENV: "people.csv"}, s3_client=client)
    assert code == 1
    assert client.calls == [("lake", "people.csv")]
    assert "Result: FAILED" in capsys.readouterr().out


def test_main_s3_read_error(capsys):
    class BrokenS3:
        def get_object(self, Bucket: str, Key: str):
            raise RuntimeError("access denied")

    code = dq.main(["--bucket", "lake", "--key", "missing.csv"], env={}, s3_client=BrokenS3())
    captured = capsys.readouterr()
    assert code == 3
    assert "access denied" in captured.err
    assert captured.out == ""


def test_load_s3_rejects_empty_object():
    with pytest.raises(dq.ReadError, match="empty"):
        dq.load_s3("lake", "empty.csv", s3_client=FakeS3(b"  \n"))


def test_default_s3_client_is_boto3(monkeypatch):
    created: dict[str, str] = {}

    class FakeClient:
        def get_object(self, Bucket: str, Key: str):
            created["target"] = f"{Bucket}/{Key}"
            return {"Body": FakeBody(b"name,age\nAda,1\n")}

    def client(service_name: str):
        assert service_name == "s3"
        return FakeClient()

    import boto3

    monkeypatch.setattr(boto3, "client", client)
    frame = dq.load_s3("my-bucket", "path/file.csv")
    assert created["target"] == "my-bucket/path/file.csv"
    assert frame.loc[0, "name"] == "Ada"
