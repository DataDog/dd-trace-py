#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

import os
import site
import sys


_CRASHTRACKING_SITE_PACKAGES_ENV = "_DD_CRASHTRACKING_SITE_PACKAGES"


def _bootstrap_receiver_site_packages() -> None:
    raw_paths = os.environ.get(_CRASHTRACKING_SITE_PACKAGES_ENV)
    if not raw_paths:
        return

    processed = {os.path.realpath(path) for path in sys.path if path and os.path.isdir(path)}
    for path in raw_paths.split(os.pathsep):
        if not path:
            continue

        real_path = os.path.realpath(path)
        if real_path in processed or not os.path.isdir(real_path):
            continue

        processed.add(real_path)
        site.addsitedir(real_path)


_bootstrap_receiver_site_packages()

try:
    from ddtrace.internal.native._native import crashtracker_receiver
except ImportError:

    def crashtracker_receiver() -> None:
        print("Crashtracker receiver not available.")


def main():
    crashtracker_receiver()


if __name__ == "__main__":
    main()
