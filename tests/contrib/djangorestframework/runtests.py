#!/usr/bin/env python
import os
import sys


if __name__ == '__main__':
    # define django defaults
    app_to_test = 'tests/contrib/djangorestframework'

    # project_root is the path of dd-trace-py (ex: ~/go/src/DataDog/dd-trace-py/)
    # We need to append the project_root path to the PYTHONPATH
    # in order to specify all our modules import from the project_root.
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.join(current_dir, '..', '..')
    sys.path.append(project_root)

    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0], 'test', app_to_test])
