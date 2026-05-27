# Data Quality Framework

Automated data validation pipeline with AWS S3 integration.

## Checks
- Missing value detection
- Unit consistency validation (value range checks)
- Outlier detection

## Tech Stack
- Python, Great Expectations
- AWS S3, boto3, s3fs

## Inspiration
Inspired by real unit mismatch debugging experience in atmospheric ML project,
where data quality issues caused scatter plot to collapse into sparse points
instead of the expected regression line.
