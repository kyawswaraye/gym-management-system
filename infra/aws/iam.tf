data "aws_iam_policy_document" "runtime" {
  statement {
    sid    = "ListBucket"
    effect = "Allow"
    actions = [
      "s3:ListBucket",
      "s3:GetBucketLocation",
    ]
    resources = [aws_s3_bucket.private.arn]
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
    resources = ["${aws_s3_bucket.private.arn}/*"]
  }
}

resource "aws_iam_policy" "runtime" {
  name   = "${var.bucket_name}-runtime"
  policy = data.aws_iam_policy_document.runtime.json
}

output "runtime_policy_arn" {
  value       = aws_iam_policy.runtime.arn
  description = "Attach this policy to the production API role. Do not attach AdministratorAccess."
}

output "bucket_name" {
  value = aws_s3_bucket.private.bucket
}

output "bucket_region" {
  value = var.aws_region
}
