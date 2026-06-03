#!/usr/bin/env python3
import logging


logging.basicConfig(level=logging.DEBUG)

from tests.appsec.iast.fixtures.integration.print_str import print_str  # noqa: E402


def main():
    print_str()


if __name__ == "__main__":
    main()
