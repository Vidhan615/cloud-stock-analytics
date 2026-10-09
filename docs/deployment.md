# AWS deployment runbook

The repository publishes a read-only GitHub Pages preview. **These AWS resources have not been provisioned by repository publishing.** The template is linted and its application/storage contracts are tested; cloud bootstrap and a real RDS/S3 deployment still need an AWS-account smoke test.

## Prerequisites

- AWS CLI v2 with an intended account and `ap-south-1` credentials. Check `aws sts get-caller-identity` first.
- Permissions to create the CloudFormation resources and IAM role, upload the artifact, use Systems Manager and read stack outputs. Keep AWS credentials outside this repository.
- An existing **private** S3 artifact bucket in that region. The example assumes ordinary SSE-S3; customer-managed KMS encryption needs additional scoped decrypt permissions.
- A domain you control and an **issued ACM certificate in ap-south-1** covering it. An optional public Route 53 hosted zone ID allows the stack to create the alias; otherwise configure DNS yourself.
- A reviewed AWS budget. ALB, EC2, public IPv4, RDS, disks, Secrets Manager, logs, retained snapshots and S3 can incur charges. This is not an always-free deployment; consult [AWS Pricing Calculator](https://calculator.aws/) using your region and expected running time.

The Ubuntu 24.04 AMI resolves from Canonical's official public SSM parameter. See [Canonical's CloudFormation guidance](https://documentation.ubuntu.com/aws/aws-how-to/instances/build-cloudformation-templates/). RDS selects the currently supported default MySQL engine version; review that version and availability in your account before deploying.

## Package and upload

Run project checks first, then package using your virtual environment's Python:

```bash
python scripts/package_release.py
aws s3 cp build/cloudfolio.zip s3://YOUR-PRIVATE-ARTIFACT-BUCKET/releases/cloudfolio.zip --region ap-south-1 --sse AES256
```

Keep the printed SHA256. The instance downloads exactly this bucket/key using its role and verifies that digest before extraction. Use an immutable release key for upgrades; editing an existing instance's user data does not rerun its bootstrap automatically.

## Create the stack

Replace every example parameter with your reviewed values:

```bash
aws cloudformation deploy \
  --region ap-south-1 \
  --stack-name cloudfolio-demo \
  --template-file infra/cloudformation.yaml \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    ArtifactBucket=YOUR-PRIVATE-ARTIFACT-BUCKET \
    ArtifactKey=releases/cloudfolio.zip \
    ArtifactSha256=YOUR-64-CHARACTER-SHA256 \
    CertificateArn=YOUR-REGIONAL-ACM-CERTIFICATE-ARN \
    AppDomain=cloudfolio.YOUR-DOMAIN \
    HostedZoneId=YOUR-OPTIONAL-ROUTE53-ZONE-ID
```

These shell examples use bash line continuation. In PowerShell, use a single line or PowerShell's backtick continuation. Omit `HostedZoneId` when configuring DNS outside Route 53.

The stack creates two public ALB subnets and two private database subnets. EC2 is in a public subnet for outbound package/SSM access, but its only inbound rule is nginx port 80 from the ALB security group. SSH and Gunicorn port 8000 are not publicly allowed. RDS accepts port 3306 only from the app security group; it has no public endpoint. HTTPS terminates at the ALB; the restricted ALB-to-nginx hop uses HTTP. Gunicorn binds to loopback.

Secrets Manager generates the session key and RDS manages the database credential. The template never places plaintext passwords in user data. S3 denies public access and non-TLS API requests, uses encryption/versioning, expires exports/charts after 30 days and dataset copies after 90 days. The weekly dataset-copy job is separate from RDS's seven-day automated database backups.

## Verify application readiness

CloudFormation resource creation is **not proof that bootstrap succeeded**: the template does not use a creation signal. Read the outputs, wait for a healthy ALB target, then verify the application explicitly:

```bash
aws cloudformation describe-stacks --stack-name cloudfolio-demo --region ap-south-1 --query 'Stacks[0].Outputs'
curl --fail https://cloudfolio.YOUR-DOMAIN/health/ready
```

Use the certificate's domain, not the raw ALB DNS name, to avoid a hostname mismatch. Register a test account, add a holding, restart the application and confirm persistence. Export a CSV/chart and verify that another account cannot fetch its authenticated download URL. Confirm S3 objects stay private, the backup timer runs, and CloudWatch receives application logs. Repeat these checks against a restore before calling the system production-ready.

If the target is unhealthy, connect with Systems Manager using the `ApplicationInstanceId` output and inspect:

```bash
sudo tail -100 /var/log/cloud-init-output.log
sudo systemctl status cloudfolio nginx
sudo journalctl -u cloudfolio -n 100
sudo tail -100 /var/log/cloudfolio/app.log
sudo systemctl list-timers cloudfolio-backup.timer
```

Check artifact permissions/digest, package installation, secret retrieval, RDS reachability, certificate bundle and schema initialization. Do not paste secrets into logs or issue reports. A CloudWatch alarm watches unhealthy targets, but has no notification destination until an operator configures one. Logs retain 14 days; there is no application-log rotation on EC2 in this prototype, so monitor disk usage or add a rotation policy for sustained operation.

## Limits and teardown

This stack deliberately uses one EC2 instance and single-AZ RDS. It lacks rolling deployment, autoscaling, distributed login throttling and a tested failover/restore procedure. It bootstraps with the managed master database secret; see [the security notes](api.md) before any public launch with real data. Additional AWS spending is required for high availability.

RDS deletion protection defaults to true. When intentionally removing the demonstration stack, first preserve any data you need, update `DBDeletionProtection=false`, then delete the stack. RDS takes a final snapshot and S3/session-secret resources are retained by policy. They, the artifact bucket and any domain resources need separate reviewed cleanup to stop ongoing charges. Stack deletion alone does not remove every billed item. Do not delete retained data you still need.
