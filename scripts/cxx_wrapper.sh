#!/bin/sh
# Check if DD_CXX_OLD is unset or empty and assign default value if so
if [ -z "$DD_CXX_OLD" -o ! -f "$DD_CXX_OLD" ]; then
    DD_CXX_OLD="c++"
fi

# If $DD_SCCACHE_PATH exists, wrap the compiler with sccache, else don't
if [ -n "$DD_SCCACHE_PATH" -a -f "$DD_SCCACHE_PATH" ]; then
    exec "$DD_SCCACHE_PATH" "$DD_CXX_OLD" "$@"
else
    exec "$DD_CXX_OLD" "$@"
fi
