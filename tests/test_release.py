import zipfile

import yaml

from cloudfolio import ROOT
from scripts.package_release import package


class TemplateLoader(yaml.SafeLoader):
    pass


def intrinsic(loader, tag, node):
    if isinstance(node, yaml.ScalarNode):
        value = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node)
    else:
        value = loader.construct_mapping(node)
    return {tag: value}


TemplateLoader.add_multi_constructor("!", intrinsic)


def test_cloud_boundaries_remain_private():
    stack = yaml.load((ROOT / "infra/cloudformation.yaml").read_text(), Loader=TemplateLoader)
    resources = stack["Resources"]
    db = resources["Database"]["Properties"]
    assert db["PubliclyAccessible"] is False
    assert db["StorageEncrypted"] is True
    assert db["ManageMasterUserPassword"] is True
    ingress = resources["DBSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert len(ingress) == 1 and "CidrIp" not in ingress[0]
    assert ingress[0]["SourceSecurityGroupId"] == {"Ref": "AppSecurityGroup"}
    app_ingress = resources["AppSecurityGroup"]["Properties"]["SecurityGroupIngress"]
    assert all(
        rule["FromPort"] == 80 and rule.get("SourceSecurityGroupId") == {"Ref": "ALBSecurityGroup"}
        for rule in app_ingress
    )
    bucket = resources["DataBucket"]["Properties"]
    assert all(bucket["PublicAccessBlockConfiguration"].values())
    assert bucket["VersioningConfiguration"]["Status"] == "Enabled"
    assert resources["Instance"]["Properties"]["MetadataOptions"]["HttpTokens"] == "required"


def test_release_never_contains_runtime_data_or_reference_documents(tmp_path):
    target = tmp_path / "app.zip"
    package(target)
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert "wsgi.py" in names and "requirements.lock" in names
        assert "data/sample_prices.csv" in names
        assert all(
            name.split("/")[0]
            in {"cloudfolio", "data", "wsgi.py", "requirements.txt", "requirements.lock", "LICENSE"}
            for name in names
        )
        assert not any(
            name.endswith((".pdf", ".db", ".pyc")) or "session.key" in name or ".env" in name
            for name in names
        )
