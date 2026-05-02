from pathlib import Path

import boto3


def load_aws_keys_from_csv(path: str | None) -> dict:
    if not path:
        return {}

    csv_path = Path(path)
    if not csv_path.exists():
        raise FileNotFoundError(f"AWS key CSV not found: {csv_path}")

    values = {}
    for line in csv_path.read_text(encoding="utf-8-sig").splitlines():
        if ";" in line:
            key, value = line.split(";", 1)
            values[key.strip().lower()] = value.strip()

    access_key = values.get("access key id")
    secret_key = values.get("secret access key")
    if not access_key or not secret_key:
        raise ValueError("AWS key CSV must include 'Access key ID' and 'Secret access key'.")

    return {
        "aws_access_key_id": access_key,
        "aws_secret_access_key": secret_key,
    }


def upload_db_to_s3(db_path: Path, bucket: str, prefix: str, aws_key_csv: str | None) -> str:
    key = f"{prefix}/{db_path.name}" if prefix else db_path.name
    boto3.client("s3", **load_aws_keys_from_csv(aws_key_csv)).upload_file(
        str(db_path), bucket, key
    )
    return f"s3://{bucket}/{key}"
