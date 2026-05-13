#!/usr/bin/env bash
set -euo pipefail

REQUIREMENTS_IN="requirements.in"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo)"
REQUIREMENTS_PATH="${REPO_ROOT}/${REQUIREMENTS_IN}"
RAPID_JSON_REL="domains/apm_sdk/apps/apis/rapid_python_http_smoke_test/rapid.json"
RAPID_JSON_PATH="${REPO_ROOT}/${RAPID_JSON_REL}"

# Minimum set of env-var defaults always applied to the smoke-test
# rapid.json on each invocation.  Users may ADD additional vars or
# OVERRIDE these defaults via --env KEY=VALUE.  Removal of keys is
# not supported (idempotent additive merge).
declare -A DEFAULT_RAPID_ENV=(
  [DD_PROFILING_ENABLED]="true"
  [DD_PROFILING_MEMORY_ENABLED]="true"
  [DD_PROFILING_LOCK_ENABLED]="true"
  [DD_DYNAMIC_INSTRUMENTATION_ENABLED]="true"
  [DD_APPSEC_ENABLED]="true"
  [DD_APPSEC_SCA_ENABLED]="true"
)

usage() {
  cat <<USAGE
Usage: $0 <PIPELINE_ID_OR_COMMIT_SHA> [--env KEY=VALUE]...

Arguments:
  PIPELINE_ID_OR_COMMIT_SHA: pipeline ID, full 40-char commit SHA, or
  abbreviated commit SHA (>=4 hex chars; resolved against the S3 bucket).
  e.g. 110616709, 2c9ea00a672e91cf812b59d123a3924ac06c0564, or 2c9ea00a67

Options:
  --env KEY=VALUE   Add or override an env-var in \`extensions.containerenv.values\`
                    of the smoke-test rapid.json. Repeat for multiple env vars.
                    KEY must be uppercase alphanumeric (env-var style).
                    VALUE is taken verbatim. KEY removal is not supported.
  -h, --help        Show this help.

Defaults (always applied to rapid.json on every run; users may override):
  DD_PROFILING_ENABLED=true
  DD_PROFILING_MEMORY_ENABLED=true
  DD_PROFILING_LOCK_ENABLED=true
  DD_DYNAMIC_INSTRUMENTATION_ENABLED=true
  DD_APPSEC_ENABLED=true
  DD_APPSEC_SCA_ENABLED=true

Merge semantics:
  defaults  →  user --env (later wins on duplicate keys)

Examples:
  # Just bump the wheel + reapply defaults to rapid.json:
  $0 68b9b6b028f497111638737aac0ec68b8e8e1067

  # Bump the wheel, defaults, AND flip MEM domain on with a new profile tag
  # (avoids the manual rapid.json edit + extra commit):
  $0 68b9b6b028 \\
     --env DD_PROFILING_MEMORY_MEM_DOMAIN_ENABLED=true \\
     --env DD_PROFILING_TAGS=smoke_test:mem_on_no_cuckoo

Notes:
  - Must be invoked from inside the dd-source repo (script's writes are
    relative to dd-source's repo root).
  - The rapid.json that gets patched is hard-coded to the rapid_python_http_smoke_test
    service ($RAPID_JSON_REL).
  - All changes (requirements.in/.txt + rapid.json) ride in a single commit
    on the apm-sdk-py-smoke-tests-staging branch.
USAGE
}

ENV_OVERRIDES=()
POSITIONAL_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)
      [[ $# -ge 2 ]] || { echo "ERROR: --env requires a KEY=VALUE argument"; exit 1; }
      ENV_OVERRIDES+=("$2")
      shift 2
      ;;
    --env=*)
      ENV_OVERRIDES+=("${1#--env=}")
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --*)
      echo "ERROR: unknown flag '$1'"; usage; exit 1
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done

# Validate --env values up-front so we fail fast before doing any work.
for kv in "${ENV_OVERRIDES[@]:-}"; do
  [[ -z "$kv" ]] && continue
  if [[ "$kv" != *"="* ]]; then
    echo "ERROR: --env value '$kv' is not in KEY=VALUE form" >&2
    exit 1
  fi
  key="${kv%%=*}"
  if [[ ! "$key" =~ ^[A-Z_][A-Z0-9_]*$ ]]; then
    echo "ERROR: --env key '$key' is not a valid env-var name (must be UPPERCASE_WITH_UNDERSCORES)" >&2
    exit 1
  fi
done

set -- "${POSITIONAL_ARGS[@]:-}"

if [[ $# -lt 1 || -z "${1:-}" ]]; then
  usage
  exit 1
fi

# Fail fast if invoked from outside dd-source.  Both the requirements files
# and the smoke-test rapid.json live there; the script writes blindly into
# REPO_ROOT/<relative-path>.  Without this guard, running from dd-trace-py
# (or any other repo) silently writes to the wrong files.
if [[ -z "$REPO_ROOT" || ! -f "$REQUIREMENTS_PATH" || ! -f "$RAPID_JSON_PATH" ]]; then
  cat >&2 <<MSG
ERROR: must be invoked from within the dd-source repo.
       Expected files at:
         ${REQUIREMENTS_PATH:-<repo root unknown>}
         ${RAPID_JSON_PATH:-<repo root unknown>}
       Found REPO_ROOT=${REPO_ROOT:-<not a git repo>}
MSG
  exit 1
fi

INPUT_REF="${1%/}"
BUCKET_URL="https://dd-trace-py-builds.s3.amazonaws.com"

# The S3 bucket stores wheels under both pipeline-ID prefixes
# (e.g. 110616709/) and full 40-char commit SHA prefixes (e.g.
# 2c9ea00a67...0564/). It does NOT recognize abbreviated commit SHAs.
# To stay user-friendly, accept short SHAs and resolve them to the unique
# bucket prefix via an unauthenticated S3 ListObjects call. Pure-numeric
# inputs are treated as pipeline IDs (no resolution).
if [[ "$INPUT_REF" =~ ^[0-9]+$ ]]; then
  RESOLVED_REF="$INPUT_REF"
elif [[ "$INPUT_REF" =~ ^[0-9a-f]{40}$ ]]; then
  RESOLVED_REF="$INPUT_REF"
elif [[ "$INPUT_REF" =~ ^[0-9a-f]{4,39}$ ]]; then
  echo "Resolving short commit SHA '${INPUT_REF}' against ${BUCKET_URL} ..."
  LIST_RESP=$(curl -fsSL "${BUCKET_URL}/?prefix=${INPUT_REF}&delimiter=/&max-keys=10")
  MATCHES=$(echo "$LIST_RESP" | grep -oE '<Prefix>[0-9a-f]{40}/</Prefix>' | sed -E 's|<Prefix>([0-9a-f]{40})/</Prefix>|\1|' | sort -u)
  N_MATCHES=$(echo -n "$MATCHES" | grep -c '^' || true)
  if [[ "$N_MATCHES" -eq 0 ]]; then
    echo "ERROR: no S3 prefix matches commit '${INPUT_REF}'."
    echo "  Wheels may not be uploaded yet (check the GitLab pipeline status),"
    echo "  or the SHA prefix is wrong. You can also pass the pipeline ID directly."
    exit 1
  elif [[ "$N_MATCHES" -gt 1 ]]; then
    echo "ERROR: multiple S3 prefixes match commit '${INPUT_REF}':"
    echo "$MATCHES" | sed 's/^/  /'
    echo "Pass a longer SHA prefix to disambiguate."
    exit 1
  fi
  RESOLVED_REF="$MATCHES"
  echo "Resolved to ${RESOLVED_REF}"
else
  echo "ERROR: '${INPUT_REF}' is neither a numeric pipeline ID nor a hex commit SHA."
  exit 1
fi

BASE_URL="${BUCKET_URL}/${RESOLVED_REF}"
INDEX_URL="${BASE_URL}/index-manylinux2014.html"

git fetch origin apm-sdk-py-smoke-tests-staging
git checkout origin/apm-sdk-py-smoke-tests-staging
git checkout -B apm-sdk-py-smoke-tests-staging

echo "Fetching wheel listing from ${INDEX_URL} ..."
LISTING=$(curl -fsSL "${INDEX_URL}")

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

# Build the merged env-var map: defaults first, then user --env overrides.
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq is required"; exit 1; }

declare -A FINAL_RAPID_ENV
for key in "${!DEFAULT_RAPID_ENV[@]}"; do
  FINAL_RAPID_ENV["$key"]="${DEFAULT_RAPID_ENV[$key]}"
done
for kv in "${ENV_OVERRIDES[@]:-}"; do
  [[ -z "$kv" ]] && continue
  key="${kv%%=*}"
  value="${kv#*=}"
  FINAL_RAPID_ENV["$key"]="$value"
done

echo "Patching ${RAPID_JSON_REL} (defaults + overrides) ..."
for key in "${!FINAL_RAPID_ENV[@]}"; do
  value="${FINAL_RAPID_ENV[$key]}"
  printf '  %-44s = %s\n' "$key" "$value"
  TMP="$(mktemp)"
  jq --arg k "$key" --arg v "$value" \
    '.extensions.containerenv.values[$k] = $v' "$RAPID_JSON_PATH" > "$TMP"
  mv "$TMP" "$RAPID_JSON_PATH"
done

# Abort early if nothing actually differs from the remote branch.
DIFF_TARGETS=(requirements.in requirements.txt "$RAPID_JSON_REL")
if git diff --quiet origin/apm-sdk-py-smoke-tests-staging "${DIFF_TARGETS[@]}"; then
  echo "Nothing to commit — branch already matches requested state."
  exit 0
fi

git add requirements.in requirements.txt "$RAPID_JSON_PATH"

COMMIT_MSG="[requirements] Use experimental ddtrace wheel"
if [[ ${#ENV_OVERRIDES[@]} -gt 0 ]]; then
  COMMIT_MSG="${COMMIT_MSG} + rapid.json env overrides"
fi
git commit -m "$COMMIT_MSG" > /dev/null 2>&1
git push > /dev/null 2>&1

git checkout main

echo "Changes pushed."