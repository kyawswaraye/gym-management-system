"""Gunicorn settings for the private API container.

The bind address is the container network namespace. Compose must not publish
this port. The Next.js BFF and Caddy's member-route allowlist are the callers.
Sync workers keep one database connection each. WEB_CONCURRENCY stays inside
the planning budget documented in the Phase 8 increment 1 runbook. That budget
is not a load test.
"""

from __future__ import annotations

import os

bind = "0.0.0.0:8000"
worker_class = "sync"
threads = 1
workers = int(os.environ.get("WEB_CONCURRENCY", "2"))
if workers < 1 or workers > 4:
    raise RuntimeError("WEB_CONCURRENCY must be from 1 through 4")

timeout = 30
graceful_timeout = 20
keepalive = 5
max_requests = 1000
max_requests_jitter = 100
worker_tmp_dir = "/dev/shm"
# Gunicorn's default access line includes the raw request target. Django writes
# the structured completion event instead, using the route template.
accesslog = None
errorlog = "-"
# Leave proxy rewriting off. Django reads X-Forwarded-Proto from the private
# BFF hop and from Caddy on the member allowlist. This port is not published.
forwarded_allow_ips = ""
limit_request_line = 4094
limit_request_fields = 100
limit_request_field_size = 8190
