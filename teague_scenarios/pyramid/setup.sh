#!/usr/bin/env bash
echo "${CONTRIB_NAME}==${CONTRIB_VERSION}" >> requirements.txt
echo "ddtrace==${DD_VERSION}" >> requirements.txt