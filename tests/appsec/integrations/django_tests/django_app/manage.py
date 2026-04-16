#!/usr/bin/env python
import ddtrace.auto  # noqa: F401  # isort: skip
import sys

from ddtrace.internal.settings import env


def main():
    """Run administrative tasks."""
    env.setdefault("DJANGO_SETTINGS_MODULE", "tests.appsec.integrations.django_tests.django_app.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError("Couldn't import Django. Are you sure it's installed?") from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
