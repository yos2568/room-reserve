"""Scheduler process: runs one tick per minute from the application image.

Started by the ``scheduler`` Compose service. Migrations are a release step and
are never run here (V3 section 11). A failed mail provider delays notifications
but does not stop the loop, and never affects process liveness.
"""

from __future__ import annotations

import logging
import signal
import time

import django

logger = logging.getLogger(__name__)

TICK_SECONDS = 60

_running = True


def _stop(signum, frame):
    global _running
    _running = False
    logger.info("Scheduler received signal %s; finishing the current tick.", signum)


def main() -> int:
    django.setup()

    from core.services.scheduler import run_tick

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    logger.info("Room Reserve scheduler started; tick interval %ss.", TICK_SECONDS)
    while _running:
        started = time.monotonic()
        try:
            summary = run_tick()
            if not summary.get("skipped"):
                logger.info("Tick complete: %s", summary)
        except Exception:
            # A database blip, a serialization error or an SMTP outage is recorded
            # and retried on the next tick rather than killing the process.
            logger.exception("Tick failed; will retry on the next interval.")

        elapsed = time.monotonic() - started
        remaining = max(1.0, TICK_SECONDS - elapsed)
        deadline = time.monotonic() + remaining
        while _running and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))

    logger.info("Room Reserve scheduler stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
