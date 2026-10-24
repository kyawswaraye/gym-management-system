# AWS S3 production template

This directory is a draft. It does not create live buckets and it is not
evidence that retention or residency is approved.

The template creates two separate private buckets in `ap-southeast-1`:

- an application object bucket with Block Public Access, SSE-S3, versioning,
  staff-origin CORS, and an application-only IAM policy;
- a pgBackRest bucket with Block Public Access, SSE-S3, versioning, no browser
  CORS, and a backup-only IAM policy.

The template creates two IAM users without console passwords or access keys and
attaches only the corresponding application or backup policy. Create one
programmatic access key for each identity after apply, install the credentials
in owner-only host secret files, and never commit them. Current-object retention
and noncurrent expiry remain unset until legal approval.

Do not commit live account IDs, access keys, or bucket names that are secrets.
Apply only from an operator workstation after the AWS account exists.

Example:

```bash
terraform init
terraform plan \
  -var='application_bucket_name=<globally-unique-app-bucket>' \
  -var='backup_bucket_name=<globally-unique-backup-bucket>' \
  -var='staff_origin=https://app.gympilotmm.com'
terraform apply \
  -var='application_bucket_name=<globally-unique-app-bucket>' \
  -var='backup_bucket_name=<globally-unique-backup-bucket>' \
  -var='staff_origin=https://app.gympilotmm.com'
```
