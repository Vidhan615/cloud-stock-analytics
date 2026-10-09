"""Private export storage. No public ACLs or presigned URLs are required."""

import io
import re
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

EXPORT_ID = re.compile(r"^[0-9a-f]{32}$")


class ExportStore:
    def __init__(self, config, client=None):
        self.bucket = config.get("S3_BUCKET")
        self.root = Path(config["EXPORT_DIR"])
        self.client = client
        if self.bucket and self.client is None:
            self.client = boto3.client("s3", region_name=config["AWS_REGION"])

    def key(self, user_id, export_id, kind="csv"):
        if not isinstance(user_id, int) or user_id < 1 or not EXPORT_ID.fullmatch(export_id):
            raise ValueError("Invalid export identity")
        if kind not in {"csv", "svg"}:
            raise ValueError("Invalid export format")
        folder = "exports" if kind == "csv" else "charts"
        return f"{folder}/{user_id}/{export_id}.{kind}"

    def put(self, user_id, export_id, content, kind="csv"):
        key = self.key(user_id, export_id, kind)
        if self.bucket:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType="text/csv" if kind == "csv" else "image/svg+xml",
                ServerSideEncryption="AES256",
            )
        else:
            path = self.root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return key

    def read(self, user_id, export_id, kind="csv"):
        key = self.key(user_id, export_id, kind)
        if self.bucket:
            try:
                payload = self.client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                    raise FileNotFoundError(key) from exc
                raise
            return io.BytesIO(payload["Body"].read())
        return io.BytesIO((self.root / key).read_bytes())

    def backup_dataset(self, content, version):
        if not re.fullmatch(r"[0-9a-zA-Z_.-]{1,80}", version):
            raise ValueError("Invalid backup version")
        key = f"datasets/{version}.csv"
        if self.bucket:
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content,
                ContentType="text/csv",
                ServerSideEncryption="AES256",
            )
        else:
            path = self.root / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return key
