#!/usr/bin/env bash
set -euo pipefail

REQUIREMENTS_IN="requirements.in"
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo)"
REQUIREMENTS_PATH="${REPO_ROOT}/${REQUIREMENTS_IN}"
DEFAULT_BRANCH="apm-sdk-py-smoke-tests-staging"
DEFAULT_RAPID_JSON_REL="domains/apm_sdk/apps/apis/rapid_python_http_smoke_test/rapid.json"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PATCH_BAZEL_HELPER="${SCRIPT_DIR}/_patch_build_bazel_pyversion.py"

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
Usage: $0 <PIPELINE_ID_OR_COMMIT_SHA> [--env KEY=VALUE]... [--python-version 3.X]

Arguments:
  PIPELINE_ID_OR_COMMIT_SHA: pipeline ID, full 40-char commit SHA, or
  abbreviated commit SHA (>=4 hex chars; resolved against the S3 bucket).
  e.g. 110616709, 2c9ea00a672e91cf812b59d123a3924ac06c0564, or 2c9ea00a67

Options:
  --branch <name>         Branch to checkout, commit, and push to.
                          Default: ${DEFAULT_BRANCH}
  --rapid-json <path>     Path (relative to dd-source repo root) of the
                          rapid.json to patch with env-var values.
                          Default: ${DEFAULT_RAPID_JSON_REL}
  --no-push               Stage commit locally but skip \`git push\`. Useful
                          when you want a human to inspect the commit first.
  --env KEY=VALUE         Add or override an env-var in \`extensions.containerenv.values\`
                          of the target rapid.json. Repeat for multiple env vars.
                          KEY must be uppercase alphanumeric (env-var style).
                          VALUE is taken verbatim. KEY removal is not supported.
  --python-version 3.X    Pin the smoke test to a single Python version (e.g.
                          3.9, 3.10, 3.11, 3.13). When set:
                            1. requirements.in lines are filtered to that
                               version's wheels only (no cross-version fan-out).
                            2. DD_SERVICE is added to rapid.json env vars as
                               \`<service_name>_py3X\` so the Datadog UI
                               can distinguish per-version profiles.
                            3. BUILD.bazel for the target is patched so the
                               Bazel runtime actually picks Python X.Y (the
                               version_set/python_version knob lives in Bazel,
                               not rapid.json).
                          Omit the flag and behavior is unchanged: script
                          doesn't touch BUILD.bazel, doesn't inject DD_SERVICE,
                          and writes the full multi-cpXY marker fan-out into
                          requirements.in.
  -h, --help              Show this help.

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
  - The rapid.json that gets patched defaults to the rapid_python_http_smoke_test
    service. Use --rapid-json to target a different service.
  - All changes (requirements.in/.txt + rapid.json) ride in a single commit
    on the branch given by --branch (default: ${DEFAULT_BRANCH}).
USAGE
}

ENV_OVERRIDES=()
POSITIONAL_ARGS=()
PY_VERSION=""
BRANCH="$DEFAULT_BRANCH"
RAPID_JSON_REL="$DEFAULT_RAPID_JSON_REL"
PUSH=true

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
    --python-version)
      [[ $# -ge 2 ]] || { echo "ERROR: --python-version requires a version argument"; exit 1; }
      PY_VERSION="$2"
      shift 2
      ;;
    --python-version=*)
      PY_VERSION="${1#--python-version=}"
      shift
      ;;
    --branch)
      [[ $# -ge 2 ]] || { echo "ERROR: --branch requires a name argument"; exit 1; }
      BRANCH="$2"
      shift 2
      ;;
    --branch=*)
      BRANCH="${1#--branch=}"
      shift
      ;;
    --rapid-json)
      [[ $# -ge 2 ]] || { echo "ERROR: --rapid-json requires a path argument"; exit 1; }
      RAPID_JSON_REL="$2"
      shift 2
      ;;
    --rapid-json=*)
      RAPID_JSON_REL="${1#--rapid-json=}"
      shift
      ;;
    --no-push)
      PUSH=false
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

# Derive service-name + dir + BUILD.bazel path from the (possibly user-supplied)
# rapid.json path. RAPID_SERVICE_NAME is used to label DD_SERVICE when
# --python-version is set; RAPID_BUILD_BAZEL_REL exists only for that path.
RAPID_JSON_PATH="${REPO_ROOT}/${RAPID_JSON_REL}"
RAPID_SERVICE_DIR="$(dirname "$RAPID_JSON_REL")"
RAPID_SERVICE_NAME="$(basename "$RAPID_SERVICE_DIR")"
RAPID_BUILD_BAZEL_REL="${RAPID_SERVICE_DIR}/BUILD.bazel"
RAPID_BUILD_BAZEL_PATH="${REPO_ROOT}/${RAPID_BUILD_BAZEL_REL}"

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

# Validate --python-version format up-front. Accept only `3.X` or `3.XY` with X/Y
# digits. The script doesn't try to validate that the version exists or that
# wheels are published for it — those errors surface naturally downstream.
PY_VERSION_NODOT=""
if [[ -n "$PY_VERSION" ]]; then
  if [[ ! "$PY_VERSION" =~ ^3\.[0-9]+$ ]]; then
    echo "ERROR: --python-version '$PY_VERSION' must look like '3.12' or '3.9'" >&2
    exit 1
  fi
  PY_VERSION_NODOT="${PY_VERSION//./}"   # e.g. 3.12 -> 312
fi

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

# Extra preconditions only enforced when --python-version is in play.
if [[ -n "$PY_VERSION" ]]; then
  if [[ ! -f "$RAPID_BUILD_BAZEL_PATH" ]]; then
    echo "ERROR: --python-version requires ${RAPID_BUILD_BAZEL_REL} to exist (not found)" >&2
    exit 1
  fi
  if [[ ! -x "$PATCH_BAZEL_HELPER" ]]; then
    echo "ERROR: BUILD.bazel patcher not found or not executable at ${PATCH_BAZEL_HELPER}" >&2
    exit 1
  fi
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

git fetch origin "$BRANCH"
git checkout "origin/${BRANCH}"
git checkout -B "$BRANCH"

# Merge latest origin/main so this commit regenerates requirements.txt against
# main's current dep set. Without this step the smoke branch drifts behind main
# and bzl repin produces a lockfile inconsistent with main, leaving stale
# conflict markers behind when a human attempts the merge later.
echo "Merging origin/main into ${BRANCH} ..."
git fetch origin main
if ! git merge --no-edit origin/main; then
  # requirements.in/.txt are managed by this script + bzl repin — for those,
  # keep the smoke branch's version; the repin below rewrites requirements.txt
  # from requirements.in anyway. Abort on conflicts in any other file.
  UNRESOLVED=$(git diff --name-only --diff-filter=U | grep -vE '^requirements\.(in|txt)$' || true)
  if [[ -n "$UNRESOLVED" ]]; then
    git merge --abort
    echo "ERROR: merge conflicts in non-regenerable files:" >&2
    echo "$UNRESOLVED" >&2
    echo "Resolve manually on ${BRANCH}, push, then rerun." >&2
    exit 1
  fi
  CONFLICTED=$(git diff --name-only --diff-filter=U)
  if [[ -n "$CONFLICTED" ]]; then
    echo "Auto-resolving requirements.in/.txt conflicts with --ours (repin will rewrite requirements.txt)."
    echo "$CONFLICTED" | xargs git checkout --ours --
    echo "$CONFLICTED" | xargs git add --
  fi
  git -c core.editor=true commit --no-edit
fi

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

  # --python-version filter: drop wheels for other Pythons so requirements.in
  # ends up with a single Python's wheel set only.
  if [[ -n "$PY_VERSION" && "$PYVER" != "$PY_VERSION" ]]; then
    continue
  fi

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

if [[ -z "$REPLACEMENT_LINES" ]]; then
  if [[ -n "$PY_VERSION" ]]; then
    echo "ERROR: no wheels matched --python-version ${PY_VERSION} at ${INDEX_URL}" >&2
  else
    echo "ERROR: parsed no wheel lines from ${INDEX_URL}" >&2
  fi
  exit 1
fi

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
# Inject DD_SERVICE suffixed by Python version so per-version deployments land
# under distinct service tags in the Datadog UI. Done BEFORE the user --env
# loop so the user can still override with --env DD_SERVICE=... if needed.
if [[ -n "$PY_VERSION" ]]; then
  FINAL_RAPID_ENV["DD_SERVICE"]="${RAPID_SERVICE_NAME}_py${PY_VERSION_NODOT}"
fi
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

# Pin BUILD.bazel's Python version when --python-version is set. Done before
# the diff check so the rewrite participates in "nothing to commit" detection.
if [[ -n "$PY_VERSION" ]]; then
  echo "Patching ${RAPID_BUILD_BAZEL_REL} → python_version=${PY_VERSION} ..."
  "$PATCH_BAZEL_HELPER" "$RAPID_BUILD_BAZEL_PATH" "$RAPID_SERVICE_NAME" "$PY_VERSION"
fi

# Abort early if nothing actually differs from the remote branch.
DIFF_TARGETS=(requirements.in requirements.txt "$RAPID_JSON_REL")
if [[ -n "$PY_VERSION" ]]; then
  DIFF_TARGETS+=("$RAPID_BUILD_BAZEL_REL")
fi
if git diff --quiet "origin/${BRANCH}" "${DIFF_TARGETS[@]}"; then
  echo "Nothing to commit — branch already matches requested state."
  exit 0
fi

git add requirements.in requirements.txt "$RAPID_JSON_PATH"
if [[ -n "$PY_VERSION" ]]; then
  git add "$RAPID_BUILD_BAZEL_PATH"
fi

COMMIT_MSG="[requirements] Use experimental ddtrace wheel"
if [[ ${#ENV_OVERRIDES[@]} -gt 0 ]]; then
  COMMIT_MSG="${COMMIT_MSG} + rapid.json env overrides"
fi
if [[ -n "$PY_VERSION" ]]; then
  COMMIT_MSG="${COMMIT_MSG} + pin Python ${PY_VERSION}"
fi
git commit -m "$COMMIT_MSG" > /dev/null 2>&1

if [[ "$PUSH" == "true" ]]; then
  git push > /dev/null 2>&1
  echo "Changes pushed to ${BRANCH}."
else
  echo "Changes committed locally on ${BRANCH} but NOT pushed (--no-push)."
  echo "To push: git push origin ${BRANCH}"
fi

git checkout main