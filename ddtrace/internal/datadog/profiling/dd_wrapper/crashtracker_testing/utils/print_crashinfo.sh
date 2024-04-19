#!/bin/bash
lz4 -d --stdout "$@" | jq
