"""Gunicorn configuration.

A failed mail provider must not make process liveness fail (V3 section 11), so
nothing here performs SMTP work at startup; delivery belongs to the outbox
worker, which reports failures to staff.
"""

import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "0.0.0.0:8000")
workers = int(os.environ.get("GUNICORN_WORKERS", min(4, multiprocessing.cpu_count() * 2 + 1)))
threads = int(os.environ.get("GUNICORN_THREADS", 4))
worker_class = "gthread"

# Enough headroom for a short bounded lock wait plus reconciliation, without
# letting a stuck worker hold a connection forever.
timeout = int(os.environ.get("GUNICORN_TIMEOUT", 60))
graceful_timeout = 30
keepalive = 5

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("GUNICORN_LOG_LEVEL", "info")
# Do not log the query string by default: it can carry personal identifiers.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(M)sms'
forwarded_allow_ips = os.environ.get("GUNICORN_FORWARDED_ALLOW_IPS", "127.0.0.1")
