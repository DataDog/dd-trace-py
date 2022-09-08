#!/usr/bin/env bash

export DD_AGENT_MAJOR_VERSION=7
export DD_INSTALL_ONLY=1

echo "=============================="
echo "DD_API_KEY=${DD_API_KEY}"
echo "DD_SITE=${DD_SITE}"
echo "DD_SERVICE=${DD_SERVICE}"
echo "=============================="

bash -c "$(curl -L https://s3.amazonaws.com/dd-agent/scripts/install_script.sh)"