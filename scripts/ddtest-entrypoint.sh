#!/usr/bin/env bash
set -eu

# If EFFECTIVE_UID, EFFECTIVE_GID are set, then create a user with that UID and GID
# and drop privileges to that user.
if [ -n "${EFFECTIVE_UID:-}" ] && [ -n "${EFFECTIVE_GID:-}" ]; then
    groupadd -g "${EFFECTIVE_GID}" user
    useradd -u "${EFFECTIVE_UID}" -g "${EFFECTIVE_GID}" -m user
    env
    exec su - user -c "$@"
fi

exec "$@"
