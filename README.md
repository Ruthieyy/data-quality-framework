# Data Quality Check

A small gate for a people CSV. It reads a local file or one S3 object, runs two column rules, prints a report, and exits non-zero when a rule fails so a later pipeline step can stop.

## Rules

| Check | Pass condition |
| --- | --- |
| `name_not_null` | Column `name` exists and no value is null |
| `age_between_0_and_120` | Column `age` exists, every value is numeric, and every value is between 0 and 120 inclusive |

Null ages, non-numeric ages, and ages outside that range fail `age_between_0_and_120`. Data row indexes in the report are the zero-based pandas index (the header is not a data row).

These are the only checks. There is no statistical outlier detection and no unit-consistency check.

## Why pandas, not Great Expectations

The first version called `great_expectations.from_pandas` and then `expect_*` on the frame. That helper was the 0.x `PandasDataset` API. It was removed in Great Expectations 1.0 (`AttributeError: module 'great_expectations' has no attribute 'from_pandas'`).

Great Expectations 1.x can express the same two rules (`ExpectColumnValuesToNotBeNull`, `ExpectColumnValuesToBeBetween`) through a Data Context, a pandas data source, a batch, and an expectation suite. That is the right shape once there are many rules, shared suites, or data docs. For these two rules it adds a project layout and a runtime dependency around a null check and a numeric range.

This script keeps the rules in plain pandas so they stay visible and the command runs anywhere the pinned requirements install. boto3 reads S3 with one `GetObject` call. Credentials are the standard boto3 chain (environment, shared config, or a role). Nothing in the repo hardcodes a bucket.

## Setup

Python 3.12 was used to run the tests. Create a virtual environment and install the pinned dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

Passing demo:

```bash
python data_quality_check.py --local sample_data/people.csv
```

```text
Data quality report
Source: sample_data/people.csv
Rows: 4

PASS  name_not_null: 'name' has no nulls (4 row(s))
PASS  age_between_0_and_120: 'age' is numeric and within [0, 120] (4 row(s))

Result: PASSED (2/2 checks passed)
```

Failing demo (exit code 1):

```bash
python data_quality_check.py --local sample_data/people_invalid.csv
```

```text
Data quality report
Source: sample_data/people_invalid.csv
Rows: 6

FAIL  name_not_null: 1 null value(s) in 'name' at data row index 1
FAIL  age_between_0_and_120: 4 value(s) in 'age' are null, non-numeric, or outside [0, 120]: 2 (-1), 3 (121), 4 (not_a_number), 5 (null)

Result: FAILED (0/2 checks passed)
```

S3, with the bucket and key supplied on the command line:

```bash
python data_quality_check.py --bucket my-bucket --key path/people.csv
```

Environment variables are used when the matching flag is omitted:

| Variable | Used as |
| --- | --- |
| `DQ_LOCAL_PATH` | Local CSV path |
| `DQ_S3_BUCKET` | S3 bucket |
| `DQ_S3_KEY` | S3 object key |

`--local` wins over any S3 flags or variables. If you pass `--bucket` or `--key`, the script reads S3 and does not fall back to `DQ_LOCAL_PATH`; the missing half may come from the environment. With no flags, set either `DQ_LOCAL_PATH` or both S3 variables, not both kinds at once.

## Exit codes

| Code | Meaning |
| --- | --- |
| 0 | Every check passed |
| 1 | One or more checks failed |
| 2 | Missing or conflicting input settings |
| 3 | The file or S3 object could not be read |

The report goes to stdout. Configuration and read errors go to stderr and do not print a passing report.

## Tests

From the repository root:

```bash
pytest
```

The tests cover both rules (including boundaries, nulls, non-numeric ages, and missing columns), exit codes, local and S3 input selection, and a fake S3 client. They do not call AWS.

## Limitations / Next steps

- Only `name` and `age` are validated. Extra columns are ignored. There is no row-count minimum, freshness check, or schema registry.
- A whitespace-only name passes, because the rule treats only nulls as missing. A follow-up rule can reject blank names after `strip`.
- A header-only CSV has zero rows and passes both rules. A zero-byte file is a read error (exit 3). Add an explicit minimum row count if an empty extract should stop the pipeline.
- The age bounds are fixed domain limits, not a statistical outlier method. There is no IQR, z-score, or median absolute deviation check.
- There is no unit-consistency check (for example, rejecting a column that mixes meters and kilometers). That would be a separate rule with a declared unit per column.
- The whole object is loaded into memory. That fits a small extract. A large file belongs in a warehouse check (Athena or Glue Data Quality) or a chunked reader.
- Duplicate column names are not handled and can raise before a report is printed.
- S3 reads are a single `GetObject` with no retry budget. In a pipeline, invoke this script as a step and branch on the exit code.
- When the rule list grows past a handful of columns, move these two rules onto Great Expectations 1.x expectations and a checkpoint. Do not restore `ge.from_pandas`.

## Background

The original note on this repo came from a unit-mismatch bug in an atmospheric ML project: bad values made a scatter plot collapse into a few points instead of a regression line. This repository does not implement that unit check. The age range is the same kind of idea at a much smaller scale: stop values the downstream job should not trust.
