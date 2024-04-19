#!/bin/bash
jq '.payload |= map((.message |= fromjson) | (.stack_trace |= fromjson))' $@
