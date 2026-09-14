#!/usr/bin/env python
"""Django management entry point. Set DJANGO_SETTINGS_MODULE to pick an environment."""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "roomreserve.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover - environment guard
        raise ImportError(
            "Django is not importable. Activate the virtual environment and install "
            "requirements/dev.txt before running management commands."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
