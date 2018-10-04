#!/usr/bin/env python
import os
import sys


if __name__ == "__main__":
    # If no args are provided, we test all the django tests, otherwise the user can specify specific "test_labels"
    # to run. E.g.: tox -e 'tox_env_to_call' -- tests.contrib.django_legacy.test_config.DjangoSettingsTest.some_test
    # See: https://django.readthedocs.io/en/1.4.X/topics/testing.html#running-tests
    default_test_label = "tests/contrib/django_legacy"
    test_runner_args = sys.argv[1:] or [default_test_label]

    # append the project root to the PYTHONPATH:
    # this is required because we don't want to put the current file
    # in the project_root
    current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.join(current_dir, '..', '..')
    sys.path.append(project_root)

    from django.core.management import execute_from_command_line
    execute_from_command_line([sys.argv[0], "test", "--testrunner=runner.DiscoverRunner"] + test_runner_args)
