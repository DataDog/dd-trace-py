#!/usr/bin/env python3
import logging

from gevent import monkey

from tests.appsec.iast.fixtures.integration.print_str import print_str  # noqa: E402


monkey.patch_all()

logging.basicConfig(level=logging.DEBUG)


def main():
    print_str()


if __name__ == "__main__":
    main()
