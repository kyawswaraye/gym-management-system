# AWS S3 production template

This directory is a draft. It does not create a live bucket and it is not
evidence that retention or residency is approved.

The template creates a private bucket in `ap-southeast-1` with Block Public
Access, SSE-S3, versioning, staff-origin CORS, and an attachable
least-privilege IAM policy. Current-object retention and noncurrent expiry
remain unset until legal approval.

Do not commit live account IDs, access keys, or bucket names that are secrets.
Apply only from an operator workstation after the AWS account exists.
