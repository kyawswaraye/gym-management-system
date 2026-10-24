data "aws_iam_policy_document" "application_runtime" {
  statement {
    sid    = "ListBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:ListBucketMultipartUploads",
    ]
    resources = [aws_s3_bucket.application.arn]
  }

  statement {
    sid    = "ObjectReadWriteDelete"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]
    resources = ["${aws_s3_bucket.application.arn}/*"]
  }
}

resource "aws_iam_policy" "application_runtime" {
  name   = "${var.application_bucket_name}-runtime"
  policy = data.aws_iam_policy_document.application_runtime.json
}

resource "aws_iam_user" "application_runtime" {
  name = "gympilotmm-production-app"
}

resource "aws_iam_user_policy_attachment" "application_runtime" {
  user       = aws_iam_user.application_runtime.name
  policy_arn = aws_iam_policy.application_runtime.arn
}

data "aws_iam_policy_document" "backup_runtime" {
  statement {
    sid    = "ListBackupBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
      "s3:ListBucketMultipartUploads",
    ]
    resources = [aws_s3_bucket.backup.arn]
  }

  statement {
    sid    = "BackupObjectReadWriteDelete"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
      "s3:ListMultipartUploadParts",
    ]
    resources = ["${aws_s3_bucket.backup.arn}/*"]
  }
}

resource "aws_iam_policy" "backup_runtime" {
  name   = "${var.backup_bucket_name}-runtime"
  policy = data.aws_iam_policy_document.backup_runtime.json
}

resource "aws_iam_user" "backup_runtime" {
  name = "gympilotmm-production-backup"
}

resource "aws_iam_user_policy_attachment" "backup_runtime" {
  user       = aws_iam_user.backup_runtime.name
  policy_arn = aws_iam_policy.backup_runtime.arn
}

output "application_runtime_policy_arn" {
  value       = aws_iam_policy.application_runtime.arn
  description = "Attach this policy to the production API role. Do not attach AdministratorAccess."
}

output "backup_runtime_policy_arn" {
  value       = aws_iam_policy.backup_runtime.arn
  description = "Attach this policy only to the pgBackRest backup identity."
}

output "application_runtime_user" {
  value       = aws_iam_user.application_runtime.name
  description = "Create a programmatic access key only; do not enable console access."
}

output "backup_runtime_user" {
  value       = aws_iam_user.backup_runtime.name
  description = "Create a programmatic access key only; do not enable console access."
}

output "application_bucket_name" {
  value = aws_s3_bucket.application.bucket
}

output "backup_bucket_name" {
  value = aws_s3_bucket.backup.bucket
}

output "bucket_region" {
  value = var.aws_region
}
