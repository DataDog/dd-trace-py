#!/usr/bin/env python3
# -*- encoding: utf-8 -*-

try:
    from ddtrace.internal.native._native import crashtracker_receiver
except ImportError:

    def crashtracker_receiver() -> None:
        print("Crashtracker receiver not available.")


def main():
    crashtracker_receiver()


if __name__ == "__main__":
    main()
