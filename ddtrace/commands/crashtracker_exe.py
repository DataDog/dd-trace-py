#!/usr/bin/env python
# -*- encoding: utf-8 -*-

from ddtrace.internal.native._native import crashtracker_receiver

def main():
    crashtracker_receiver()

if __name__ == "__main__":
    main()
