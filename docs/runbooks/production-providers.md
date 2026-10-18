# Production email, password recovery, and object storage

This runbook is configuration readiness. It is not evidence that Resend, AWS,
or ClamAV are live.

## Email

Production mail is `apps.common.mail.resend.EmailBackend`. Set `RESEND_API_KEY`
and a verified-style `DEFAULT_FROM_EMAIL`. `APP_PUBLIC_URL` must be HTTPS.
Scheduler jobs remain `schedule_membership_expiry_emails` and
`deliver_membership_expiry_emails`.

Forgot password is a constant 200. Reset is 204 and issues no session.

## Object storage

Apply `infra/aws/` after an AWS account exists. The template is draft-only:
Block Public Access, SSE-S3, versioning, and staff-origin CORS. Do not add
current-object retention or noncurrent expiry until legal approval.

Runtime settings:

- `MINIO_ADDRESSING_STYLE=virtual`
- `MINIO_REGION=ap-southeast-1`
- HTTPS endpoint and public URL
- bucket and origin allowlists

Local and E2E Compose keep path-style `docker.io/pgsty/silo`.

## ClamAV

Merge `docker-compose.clamav.prod.yml` on the VPS private network. Publish no
host port. `CLAMAV_HOST` must be the Compose service name or another private
address.

## Preflight

Configuration only:

```bash
bash scripts/preflight-production-providers.sh
```

Live sockets after credentials exist:

```bash
LIVE_PROVIDER_SMOKE=1 bash scripts/preflight-production-providers.sh
```

Remaining external gates: Resend domain and API key with a live idempotent
send; AWS bucket, IAM, CORS, and a live upload and download; ClamAV production
host proof; legal retention and residency approval.
