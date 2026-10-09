import io
import json
from unittest.mock import Mock, patch

import boto3
import pymysql
import pytest
from botocore.response import StreamingBody
from botocore.stub import Stubber

from cloudfolio.database import RDSConnector, make_engine
from cloudfolio.storage import ExportStore


def test_private_s3_export_contract(tmp_path):
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    store = ExportStore({"S3_BUCKET": "test-bucket", "EXPORT_DIR": str(tmp_path)}, client)
    identifier = "a" * 32
    key = f"exports/7/{identifier}.csv"
    with Stubber(client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "test-bucket",
                "Key": key,
                "Body": b"csv",
                "ContentType": "text/csv",
                "ServerSideEncryption": "AES256",
            },
        )
        stub.add_response(
            "get_object",
            {"Body": StreamingBody(io.BytesIO(b"csv"), 3)},
            {"Bucket": "test-bucket", "Key": key},
        )
        assert store.put(7, identifier, b"csv") == key
        assert store.read(7, identifier).read() == b"csv"
        stub.assert_no_pending_responses()


def test_s3_lifecycle_missing_object(tmp_path):
    client = boto3.client(
        "s3", region_name="ap-south-1", aws_access_key_id="test", aws_secret_access_key="test"
    )
    store = ExportStore({"S3_BUCKET": "test-bucket", "EXPORT_DIR": str(tmp_path)}, client)
    with Stubber(client) as stub:
        stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        with pytest.raises(FileNotFoundError):
            store.read(1, "a" * 32)


@pytest.mark.parametrize(
    "user,identifier,kind", [(0, "a" * 32, "csv"), (1, "../secret", "csv"), (1, "a" * 32, "html")]
)
def test_storage_rejects_path_traversal(tmp_path, user, identifier, kind):
    store = ExportStore({"EXPORT_DIR": str(tmp_path)})
    with pytest.raises(ValueError):
        store.key(user, identifier, kind)


def test_rotation_retries_once_and_verifies_rds_tls():
    client = Mock()
    client.get_secret_value.side_effect = [
        {"SecretString": json.dumps({"username": "app", "password": "old"})},
        {"SecretString": json.dumps({"username": "app", "password": "rotated"})},
    ]
    config = {
        "AWS_REGION": "ap-south-1",
        "DB_SECRET_ARN": "test-secret",
        "DB_HOST": "private.db.test",
        "DB_NAME": "cloudfolio",
        "DB_TLS_CA": "/trusted.pem",
    }
    connector = RDSConnector(config, client)
    connection = Mock()
    with patch(
        "cloudfolio.database.pymysql.connect",
        side_effect=[pymysql.err.OperationalError(1045, "denied"), connection],
    ) as connect:
        assert connector.connect() is connection
        assert connect.call_count == 2
        assert connect.call_args.kwargs["password"] == "rotated"
        assert connect.call_args.kwargs["ssl_verify_cert"] is True
        assert connect.call_args.kwargs["ssl_verify_identity"] is True
        assert connect.call_args.kwargs["ssl_ca"] == "/trusted.pem"
    connector.credentials()
    assert client.get_secret_value.call_count == 2


def test_rds_connection_errors_do_not_trigger_secret_rotation():
    client = Mock()
    client.get_secret_value.return_value = {"SecretString": '{"username":"app","password":"test"}'}
    config = {
        "AWS_REGION": "ap-south-1",
        "DB_SECRET_ARN": "test-secret",
        "DB_HOST": "db.test",
        "DB_NAME": "cloudfolio",
        "DB_TLS_CA": "/trusted.pem",
    }
    with patch(
        "cloudfolio.database.pymysql.connect",
        side_effect=pymysql.err.OperationalError(2003, "offline"),
    ):
        with pytest.raises(pymysql.err.OperationalError):
            RDSConnector(config, client).connect()
    assert client.get_secret_value.call_count == 1


def test_rds_mode_requires_ca():
    with pytest.raises(ValueError, match="trusted"):
        make_engine({"DB_SECRET_ARN": "secret", "DB_HOST": "host"})
