#!/bin/sh
# Check if DD_CC_OLD is unset or empty and assign default value if so
if [ -z "$DD_CC_OLD" -o ! -f "$DD_CC_OLD" ]; then
    DD_CC_OLD="cc"
fi

# If $DD_SCCACHE_PATH exists, wrap the compiler with sccache, else don't
if [ -n "$DD_SCCACHE_PATH" -a -f "$DD_SCCACHE_PATH" ]; then
    exec "$DD_SCCACHE_PATH" "$DD_CC_OLD" "$@"
else
    exec "$DD_CC_OLD" "$@"
fi
