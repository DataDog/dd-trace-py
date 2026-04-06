#!/usr/bin/env python

import sys
from ddtrace.internal.settings import env


if __name__ == "__main__":
    env.setdefault("DJANGO_SETTINGS_MODULE", "proj.settings")

    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)
