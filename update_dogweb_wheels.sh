#!/usr/bin/env bash
set -euo pipefail

REQUIREMENTS_IN="requirements.in"
REPO_ROOT="$(git rev-parse --show-toplevel)"
REQUIREMENTS_PATH="${REPO_ROOT}/${REQUIREMENTS_IN}"


if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <INDEX_URL>"
  echo "  INDEX_URL: base URL where ddtrace wheels are hosted"
  echo "  e.g. https://dd-trace-py-builds.s3.amazonaws.com/102921391/index-manylinux2014.html"
  exit 1
fi

INDEX_URL="${1%/}"

git fetch origin apm-sdk-py-smoke-tests-staging > /dev/null 2>&1
git checkout origin/apm-sdk-py-smoke-tests-staging > /dev/null 2>&1
git checkout -B apm-sdk-py-smoke-tests-staging > /dev/null 2>&1

# If INDEX_URL points to an HTML file, fetch it but use its parent as the base for wheel URLs
if [[ "$INDEX_URL" == *.html ]]; then
  FETCH_URL="$INDEX_URL"
  BASE_URL="${INDEX_URL%/*}"
else
  FETCH_URL="${INDEX_URL}/"
  BASE_URL="$INDEX_URL"
fi

echo "Fetching wheel listing from ${FETCH_URL} ..."
LISTING=$(curl -fsSL "${FETCH_URL}")

WHEELS=$(echo "$LISTING" | grep -oE 'ddtrace-[^"<>]+\.whl' | sort -u)

if [[ -z "$WHEELS" ]]; then
  echo "ERROR: No ddtrace wheels found at ${INDEX_URL}"
  exit 1
fi

echo "Found wheels:"
echo "$WHEELS"

REPLACEMENT_LINES=""

while IFS= read -r wheel; do
  # Parse: ddtrace-VERSION-cpXY-cpXY-PLATFORM.whl
  VERSION=$(echo "$wheel" | sed -E 's/ddtrace-([^-]+)-.*/\1/')
  CPVER=$(echo "$wheel" | sed -E 's/ddtrace-[^-]+-([^-]+)-.*/\1/')
  PLATFORM=$(echo "$wheel" | sed -E 's/ddtrace-[^-]+-[^-]+-[^-]+-(.*)\.whl/\1/')

  # Convert cpXY to python version X.Y
  PYVER=$(echo "$CPVER" | sed -E 's/cp([0-9])([0-9]+)/\1.\2/')

  # Map platform tag to environment markers
  if [[ "$PLATFORM" == *manylinux*x86_64* ]]; then
    MARKERS="python_version == \"${PYVER}\" and platform_machine == \"x86_64\" and sys_platform == \"linux\""
  elif [[ "$PLATFORM" == *manylinux*aarch64* ]]; then
    MARKERS="python_version == \"${PYVER}\" and platform_machine == \"aarch64\" and sys_platform == \"linux\""
  elif [[ "$PLATFORM" == *macosx*arm64* ]]; then
    MARKERS="python_version == \"${PYVER}\" and platform_machine == \"arm64\" and sys_platform == \"darwin\""
  elif [[ "$PLATFORM" == *macosx*x86_64* ]]; then
    MARKERS="python_version == \"${PYVER}\" and platform_machine == \"x86_64\" and sys_platform == \"darwin\""
  elif [[ "$PLATFORM" == *musllinux*x86_64* ]]; then
    MARKERS="python_version == \"${PYVER}\" and platform_machine == \"x86_64\" and sys_platform == \"linux\""
  elif [[ "$PLATFORM" == *musllinux*aarch64* ]]; then
    MARKERS="python_version == \"${PYVER}\" and platform_machine == \"aarch64\" and sys_platform == \"linux\""
  else
    echo "WARNING: Unknown platform tag '${PLATFORM}' in ${wheel}, skipping"
    continue
  fi

  LINE="ddtrace @ ${BASE_URL}/${wheel} ; ${MARKERS}"
  if [[ -n "$REPLACEMENT_LINES" ]]; then
    REPLACEMENT_LINES="${REPLACEMENT_LINES}"$'\n'"${LINE}"
  else
    REPLACEMENT_LINES="${LINE}"
  fi
done <<< "$WHEELS"

echo ""
echo "Replacing ddtrace line in ${REQUIREMENTS_PATH} ..."

# Remove existing ddtrace lines (but not ddtrace-api or ddtrace_api)
# Then insert the new lines where the old ddtrace line was
TMPFILE=$(mktemp)
INSERTED=false
while IFS= read -r line; do
  if echo "$line" | grep -qE '^ddtrace([= @])' && ! echo "$line" | grep -qE '^ddtrace[-_]'; then
    if [[ "$INSERTED" == false ]]; then
      echo "$REPLACEMENT_LINES" >> "$TMPFILE"
      INSERTED=true
    fi
  else
    echo "$line" >> "$TMPFILE"
  fi
done < "$REQUIREMENTS_PATH"

mv "$TMPFILE" "$REQUIREMENTS_PATH"

echo "Updated ${REQUIREMENTS_IN}. New ddtrace lines:"
grep '^ddtrace @' "$REQUIREMENTS_PATH" || grep '^ddtrace==' "$REQUIREMENTS_PATH"
echo ""

echo "Running repin ..."
cd "$REPO_ROOT"
bzl run //:requirements.update

# Check if any changes were made to requirements.in / requirements.txt
if git diff --quiet origin/apm-sdk-py-smoke-tests-staging requirements.in requirements.txt; then
  echo "Branch already uses that version of ddtrace, aborting."
  exit 0
fi

git add requirements.in requirements.txt
git commit -m "[requirements] Use experimental ddtrace wheel" > /dev/null 2>&1
git push > /dev/null 2>&1

git checkout main

echo "Changes pushed."