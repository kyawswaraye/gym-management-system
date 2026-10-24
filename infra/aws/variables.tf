variable "application_bucket_name" {
  type        = string
  description = "Private production object bucket. Do not commit the live name as a secret."
}

variable "backup_bucket_name" {
  type        = string
  description = "Separate private pgBackRest bucket. Must differ from application_bucket_name."
}

variable "staff_origin" {
  type        = string
  description = "Exact staff HTTPS origin allowed to use presigned POST and GET."
}

variable "aws_region" {
  type        = string
  description = "Must remain ap-southeast-1 until a new architecture decision replaces ADR 0010."
  default     = "ap-southeast-1"
}
