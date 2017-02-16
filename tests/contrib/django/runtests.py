#!/usr/bin/env python
import os
import sys

import logging; logging.basicConfig(); log = logging.getLogger(__name__)
if __name__ == "__main__":
    # define django defaults
    app_to_test = "tests/contrib/django"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "app.settings")

    log.info("Using DJANGO_SETTINGS_MODULE %s", os.environ.get("DJANGO_SETTINGS_MODULE"))

    # append the project root to the PYTHONPATH:
    # this is required because we don't want to put the current file
    # in the project_root
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.join(current_dir, '..', '..')
    sys.path.append(project_root)

    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0], "test", app_to_test])
