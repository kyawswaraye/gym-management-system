terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

check "separate_buckets" {
  assert {
    condition     = var.application_bucket_name != var.backup_bucket_name
    error_message = "The application and backup buckets must be different."
  }
}

resource "aws_s3_bucket" "application" {
  bucket = var.application_bucket_name
}

resource "aws_s3_bucket_public_access_block" "application" {
  bucket                  = aws_s3_bucket.application.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "application" {
  bucket = aws_s3_bucket.application.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "application" {
  bucket = aws_s3_bucket.application.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_cors_configuration" "application" {
  bucket = aws_s3_bucket.application.id
  cors_rule {
    allowed_headers = ["content-type", "x-amz-*"]
    allowed_methods = ["GET", "POST"]
    allowed_origins = [var.staff_origin]
    expose_headers  = ["etag"]
    max_age_seconds = 300
  }
}

resource "aws_s3_bucket" "backup" {
  bucket = var.backup_bucket_name
}

resource "aws_s3_bucket_public_access_block" "backup" {
  bucket                  = aws_s3_bucket.backup.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "backup" {
  bucket = aws_s3_bucket.backup.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "backup" {
  bucket = aws_s3_bucket.backup.id
  versioning_configuration {
    status = "Enabled"
  }
}

# Current-object retention and noncurrent-version expiry stay unset until
# legal approval. Do not add lifecycle rules here.
