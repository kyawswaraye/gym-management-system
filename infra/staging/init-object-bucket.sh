#!/bin/sh
# Create one private bucket on the disposable staging object service.
# Credentials stay in the environment. This script does not print them.
set -eu
: "${OBJECT_SCHEME:?}"
: "${OBJECT_HOST:?}"
: "${OBJECT_PORT:?}"
: "${MINIO_BUCKET:?}"
: "${MINIO_ROOT_USER:?}"
: "${MINIO_ROOT_PASSWORD:?}"
case "$OBJECT_SCHEME" in
  http|https) ;;
  *) echo "object scheme is not http or https" >&2; exit 1 ;;
esac
case "$OBJECT_HOST" in
  object-store|object-backup) ;;
  *) echo "object host is not a rehearsal service" >&2; exit 1 ;;
esac
if [ -n "${OBJECT_CA_FILE:-}" ]; then
  export SSL_CERT_FILE="$OBJECT_CA_FILE"
fi
export MC_CONFIG_DIR=/tmp/mc
export MC_HOST_rehearsal="${OBJECT_SCHEME}://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@${OBJECT_HOST}:${OBJECT_PORT}"
mc mb --ignore-existing "rehearsal/${MINIO_BUCKET}"
mc anonymous set none "rehearsal/${MINIO_BUCKET}"
