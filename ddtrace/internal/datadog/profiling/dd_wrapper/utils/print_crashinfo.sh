#!/bin/bash
lz4 -d --stdout files/crash-info.json | jq
