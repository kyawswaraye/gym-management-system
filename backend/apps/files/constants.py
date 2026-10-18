from __future__ import annotations

PROFILE_PHOTO_PURPOSE = "PROFILE_PHOTO"
EXPENSE_RECEIPT_PURPOSE = "EXPENSE_RECEIPT"
MEMBER_OWNER = "MEMBER"
GYM_OWNER = "GYM"
ALLOWED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
MAX_UPLOAD_BYTES = 5_242_880
MAX_IMAGE_PIXELS = 20_000_000
POST_TTL_SECONDS = 300
GET_TTL_SECONDS = 60
CLAIM_STALE_SECONDS = 120
STORAGE_TIMEOUT_SECONDS = 5
SCAN_TIMEOUT_SECONDS = 15
DECODE_TIMEOUT_SECONDS = 10
QUARANTINE_HOURS = 24
ATTACHMENT_NAMES = {
    "image/jpeg": "profile-photo.jpg",
    "image/png": "profile-photo.png",
    "image/webp": "profile-photo.webp",
}
EXPENSE_RECEIPT_NAMES = {
    "image/jpeg": "expense-receipt.jpg",
    "image/png": "expense-receipt.png",
    "image/webp": "expense-receipt.webp",
}
