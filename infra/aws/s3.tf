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

resource "aws_s3_bucket" "private" {
  bucket = var.bucket_name
}

resource "aws_s3_bucket_public_access_block" "private" {
  bucket                  = aws_s3_bucket.private.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "private" {
  bucket = aws_s3_bucket.private.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "private" {
  bucket = aws_s3_bucket.private.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_cors_configuration" "private" {
  bucket = aws_s3_bucket.private.id
  cors_rule {
    allowed_headers = ["content-type", "x-amz-*"]
    allowed_methods = ["GET", "POST"]
    allowed_origins = [var.staff_origin]
    expose_headers  = ["etag"]
    max_age_seconds = 300
  }
}

# Current-object retention and noncurrent-version expiry stay unset until
# legal approval. Do not add lifecycle rules here.
