#!/usr/bin/env python
import os
import sys


if __name__ == "__main__":
    app_to_test = "tests/contrib/django"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0], "test", app_to_test])
