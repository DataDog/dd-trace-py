#!/usr/bin/env python3
"""
Reproduction script for psycopg circular import issue.
Run with: ddtrace-run python reproduce_psycopg_cyclic_import_error.py
"""

import sys


def clear_psycopg_modules():
    """Clear all psycopg-related modules from sys.modules"""
    modules_to_remove = [name for name in list(sys.modules.keys()) if ("psycopg" in name and "ddtrace" not in name)]
    for module in modules_to_remove:
        del sys.modules[module]


def main():
    clear_psycopg_modules()
    import psycopg2  # noqa:F401


if __name__ == "__main__":
    main()
