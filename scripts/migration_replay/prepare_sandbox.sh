#!/usr/bin/env bash
# Build an isolated dd-trace-py sandbox at the pre-3.15 base, overlay current
# profiling migration docs/tooling (#19273 + #20565), and optionally scrub the
# blind arm (delete version layer + guard generic docs).
#
# Usage:
#   scripts/migration_replay/prepare_sandbox.sh --arm assisted --out /tmp/replay-assisted
#   scripts/migration_replay/prepare_sandbox.sh --arm blind --out /tmp/replay-blind
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HARNESS_REPO="$(cd "${SCRIPT_DIR}/../.." && pwd)"

# ---------------------------------------------------------------------------
# Documented pins (find-base, 2026-10-05)
# ---------------------------------------------------------------------------
# Verified on a full (non-shallow) clone of DataDog/dd-trace-py:
#   git rev-parse --is-shallow-repository  → false
#   git log origin/main --reverse -S 'cp315' -- .gitlab/package.yml
#
# First commit on main that introduces cp315 wheel packaging / py-315 unblock
# work is #17959:
#   FIRST_315_SHA = 8a9b5083ed7b6fea5f390bc1c3867f50b7807449
#   subject: ci(py-315): unblock cp315 wheels — bump base images to 2026.05.13-1
#   date:    2026-05-20 17:02:42 +0000
#
# Replay base is that commit's parent (no cp315 rows in .gitlab/package.yml):
BASE_SHA="${BASE_SHA:-2147f61066d98d27ad96a5db717e052be7d0c824}"
FIRST_315_SHA="${FIRST_315_SHA:-8a9b5083ed7b6fea5f390bc1c3867f50b7807449}"
#
# Earlier main commits mention 3.15 (testrunner 3.15-dev #15995/#17710,
# integration-bump template #17791, incompatibility marker #15556) but do not
# introduce cp315 packaging. They remain in the sandbox history.
#
# Overlay tips (do not use older 19273 commits for docs/tooling):
OVERLAY_TOOLING_SHA="${OVERLAY_TOOLING_SHA:-2b68ceab9d260a0720dd3a86b04cb4b6a52ca1eb}"  # vlad/315-profiling-dev-tooling
OVERLAY_DELTA_SHA="${OVERLAY_DELTA_SHA:-419aa0acd7}"  # vlad/315-profiling-cpython-delta tip (short ok if unique)

ARM=""
OUT_DIR=""
SOURCE_REPO="${SOURCE_REPO:-}"
SKIP_COMMIT=0

usage() {
  cat <<'EOF'
Usage: prepare_sandbox.sh --arm assisted|blind --out DIR [--source-repo PATH]

Environment overrides:
  BASE_SHA, FIRST_315_SHA, OVERLAY_TOOLING_SHA, OVERLAY_DELTA_SHA, SOURCE_REPO
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arm) ARM="$2"; shift 2 ;;
    --out) OUT_DIR="$2"; shift 2 ;;
    --source-repo) SOURCE_REPO="$2"; shift 2 ;;
    --skip-commit) SKIP_COMMIT=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$ARM" != "assisted" && "$ARM" != "blind" ]]; then
  echo "ERROR: --arm must be assisted or blind" >&2
  exit 2
fi
if [[ -z "$OUT_DIR" ]]; then
  echo "ERROR: --out DIR is required" >&2
  exit 2
fi

# Prefer a full clone that contains BASE_SHA (harness worktree is often shallow).
if [[ -z "$SOURCE_REPO" ]]; then
  for candidate in \
    "/Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py" \
    "/Users/vlad.scherbich/go/src/github.com/DataDog/dd-trace-py-19273-tooling" \
    "${HARNESS_REPO}"
  do
    if [[ -d "${candidate}/.git" ]] || [[ -f "${candidate}/.git" ]]; then
      if git -C "$candidate" cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
        # Prefer non-shallow when multiple candidates work.
        if git -C "$candidate" rev-parse --is-shallow-repository 2>/dev/null | grep -q false; then
          SOURCE_REPO="$candidate"
          break
        fi
        if [[ -z "$SOURCE_REPO" ]]; then
          SOURCE_REPO="$candidate"
        fi
      fi
    fi
  done
fi
if [[ -z "$SOURCE_REPO" ]]; then
  echo "ERROR: no SOURCE_REPO containing BASE_SHA=${BASE_SHA}." >&2
  echo "Pass --source-repo pointing at a full (non-shallow) dd-trace-py clone." >&2
  exit 1
fi
if ! git -C "$SOURCE_REPO" rev-parse --verify "${BASE_SHA}^{commit}" >/dev/null; then
  echo "ERROR: BASE_SHA ${BASE_SHA} missing in ${SOURCE_REPO}" >&2
  exit 1
fi
if git -C "$SOURCE_REPO" rev-parse --is-shallow-repository 2>/dev/null | grep -q true; then
  if ! git -C "$SOURCE_REPO" cat-file -e "${BASE_SHA}^{commit}" 2>/dev/null; then
    echo "ERROR: SOURCE_REPO is shallow and lacks BASE_SHA. Unshallow or use a full clone." >&2
    exit 1
  fi
  echo "WARN: SOURCE_REPO is shallow but contains BASE_SHA; proceeding." >&2
fi

resolve_sha() {
  local repo="$1" ref="$2"
  git -C "$repo" rev-parse --verify "${ref}^{commit}"
}

# Overlay tips live on feature branches — prefer the harness worktree, then SOURCE_REPO.
OVERLAY_REPO="$HARNESS_REPO"
if ! git -C "$OVERLAY_REPO" cat-file -e "${OVERLAY_TOOLING_SHA}^{commit}" 2>/dev/null; then
  OVERLAY_REPO="$SOURCE_REPO"
fi
TOOLING_SHA="$(resolve_sha "$OVERLAY_REPO" "$OVERLAY_TOOLING_SHA")"
DELTA_SHA="$(resolve_sha "$OVERLAY_REPO" "$OVERLAY_DELTA_SHA")"
BASE_SHA="$(resolve_sha "$SOURCE_REPO" "$BASE_SHA")"

echo "prepare_sandbox:"
echo "  arm=${ARM}"
echo "  out=${OUT_DIR}"
echo "  base=${BASE_SHA}"
echo "  first_315=${FIRST_315_SHA}  (#17959)"
echo "  overlay_tooling=${TOOLING_SHA}"
echo "  overlay_delta=${DELTA_SHA}"
echo "  source_repo=${SOURCE_REPO}"

if [[ -e "$OUT_DIR" ]]; then
  echo "ERROR: --out already exists: ${OUT_DIR}" >&2
  exit 1
fi

# Depth-1 checkout of exactly BASE_SHA, then drop remotes (no future history, no push).
mkdir -p "$OUT_DIR"
git -C "$OUT_DIR" init -q
git -C "$OUT_DIR" remote add source "$SOURCE_REPO"
git -C "$OUT_DIR" fetch --depth 1 source "${BASE_SHA}"
git -C "$OUT_DIR" checkout --detach FETCH_HEAD
git -C "$OUT_DIR" remote remove source
# Ensure no remotes remain.
while read -r r; do
  [[ -n "$r" ]] && git -C "$OUT_DIR" remote remove "$r"
done < <(git -C "$OUT_DIR" remote)

# Sanity: base must not already ship cp315 package rows.
if git -C "$OUT_DIR" show HEAD:.gitlab/package.yml 2>/dev/null | grep -q 'cp315'; then
  echo "ERROR: base checkout already contains cp315 in .gitlab/package.yml — wrong BASE_SHA" >&2
  exit 1
fi

overlay_file() {
  local sha="$1" path="$2"
  local dest="${OUT_DIR}/${path}"
  mkdir -p "$(dirname "$dest")"
  if git -C "$OVERLAY_REPO" cat-file -e "${sha}:${path}" 2>/dev/null; then
    git -C "$OVERLAY_REPO" show "${sha}:${path}" >"$dest"
  elif git -C "$SOURCE_REPO" cat-file -e "${sha}:${path}" 2>/dev/null; then
    git -C "$SOURCE_REPO" show "${sha}:${path}" >"$dest"
  else
    echo "ERROR: missing ${sha}:${path}" >&2
    exit 1
  fi
  case "$path" in
    scripts/run-profiling-tests) chmod +x "$dest" ;;
  esac
}

# #19273 tooling/docs (tip after genericize + Copilot fail-closed fixes)
TOOLING_FILES=(
  .claude/skills/compare-cpython-versions/SKILL.md
  .claude/skills/find-cpython-usage/SKILL.md
  .claude/skills/migrate-profiling-new-cpython/SKILL.md
  .cursor/rules/profiling-new-cpython.mdc
  AGENTS.md
  docs/contributing-profiling-new-cpython.rst
  docs/contributing-testing.rst
  docs/contributing.rst
  docs/cpython-diffs/analysis_314_to_315.md
  docs/cpython-diffs/py315_pr_catalog.md
  scripts/profiles/compatibility_baselines.json
  scripts/profiles/profiling_versions.json
  scripts/py315-stack/PROFILING_STACK.md
  scripts/run-profiling-tests
  scripts/verify_profiler_compatibility.py
  tests/internal/test_profiler_compat_fail_closed.py
)

# #20565 cpython_delta (+ its skills/tests). Skills overlap with tooling tip;
# write delta copies after tooling so compare/find skills match #20565.
DELTA_FILES=(
  .claude/skills/compare-cpython-versions/SKILL.md
  .claude/skills/find-cpython-usage/SKILL.md
  scripts/cpython_delta/__init__.py
  scripts/cpython_delta/common.py
  scripts/cpython_delta/diff.py
  scripts/cpython_delta/inventory.py
  tests/internal/test_cpython_delta_backtest.py
  tests/internal/test_cpython_delta_diff.py
)

echo "Overlaying #19273 tip (${TOOLING_SHA})..."
for f in "${TOOLING_FILES[@]}"; do
  overlay_file "$TOOLING_SHA" "$f"
done

echo "Overlaying #20565 tip (${DELTA_SHA})..."
for f in "${DELTA_FILES[@]}"; do
  overlay_file "$DELTA_SHA" "$f"
done

# Record pins inside the sandbox for scorers / humans.
mkdir -p "${OUT_DIR}/scripts/migration_replay"
cat >"${OUT_DIR}/scripts/migration_replay/SANDBOX_PINS.txt" <<EOF
arm=${ARM}
base_sha=${BASE_SHA}
first_315_sha=${FIRST_315_SHA}
overlay_tooling_sha=${TOOLING_SHA}
overlay_delta_sha=${DELTA_SHA}
prepared_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF

if [[ "$ARM" == "blind" ]]; then
  echo "Blind arm: wiping version layer + resetting 3.15 registry entry..."
  rm -f \
    "${OUT_DIR}/docs/cpython-diffs/py315_pr_catalog.md" \
    "${OUT_DIR}/docs/cpython-diffs/analysis_314_to_315.md" \
    "${OUT_DIR}/scripts/py315-stack/PROFILING_STACK.md"

  python3 - "$OUT_DIR/scripts/profiles/profiling_versions.json" <<'PY'
import json, sys
from pathlib import Path
path = Path(sys.argv[1])
data = json.loads(path.read_text())
template = data.get("checklist_template")
if template is None:
    raise SystemExit("checklist_template missing from profiling_versions.json")
# Keep 3.14 intact (previous migration context). Reset 3.15 to a scaffold.
v315 = {
    "hex": "0x030f0000",
    "major": 3,
    "minor": 15,
    "uwsgi_supported": False,
    "min_wall_time_samples": 2,
    "expected_sample_types": ["wall-time", "cpu-time"],
    "image_tags": ["python/3.15"],
    "layout_contracts": [],
    # asyncio_hook deliberately omitted / null — agent must rediscover.
    "asyncio_hook": None,
    "checklist": json.loads(json.dumps(template)),
}
versions = data.get("versions")
if not isinstance(versions, dict):
    raise SystemExit("versions must be a mapping")
versions["3.15"] = v315
# Blind arm should not default the agent into "3.15 already done".
data["default_python"] = "3.14"
path.write_text(json.dumps(data, indent=4) + "\n")
print(f"reset 3.15 registry entry in {path}")
PY

  echo "Running scrub.yaml guard on generic docs..."
  # No PyYAML dependency: read the two list blocks from scrub.yaml.
  python3 - "$SCRIPT_DIR/scrub.yaml" "$OUT_DIR" <<'PY'
import sys
from pathlib import Path

def parse_scrub_lists(text: str) -> tuple[list[str], list[str]]:
    paths: list[str] = []
    patterns: list[str] = []
    section: str | None = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if line.startswith("guard_paths:"):
            section = "paths"
            continue
        if line.startswith("forbidden_patterns:"):
            section = "patterns"
            continue
        if not line.startswith(" ") and line.endswith(":"):
            section = None
            continue
        if section and line.strip().startswith("- "):
            item = line.strip()[2:].strip()
            if (item.startswith('"') and item.endswith('"')) or (
                item.startswith("'") and item.endswith("'")
            ):
                item = item[1:-1]
            if section == "paths":
                paths.append(item)
            else:
                patterns.append(item)
    return paths, patterns

scrub_path = Path(sys.argv[1])
root = Path(sys.argv[2])
paths, patterns = parse_scrub_lists(scrub_path.read_text())
hits: list[str] = []
for rel in paths:
    p = root / rel
    if not p.is_file():
        hits.append(f"MISSING_GUARD_PATH {rel}")
        continue
    text = p.read_text(errors="replace")
    for pat in patterns:
        if pat and pat in text:
            for i, line in enumerate(text.splitlines(), 1):
                if pat in line:
                    hits.append(f"{rel}:{i}: {pat!r} → {line.strip()[:120]}")
if hits:
    print("SCRUB GUARD FAILED — answer-key patterns in generic docs:", file=sys.stderr)
    for h in hits:
        print(f"  {h}", file=sys.stderr)
    sys.exit(1)
print(f"scrub guard OK ({len(paths)} files, {len(patterns)} patterns)")
PY
fi

# Commit overlay as the sandbox's first commit (identity local to sandbox only).
if [[ "$SKIP_COMMIT" -eq 0 ]]; then
  git -C "$OUT_DIR" add -A
  # Sandbox commits are disposable; use a local identity without touching user gitconfig.
  git -C "$OUT_DIR" -c user.name="migration-replay" -c user.email="migration-replay@localhost" \
    -c commit.gpgsign=false commit --no-gpg-sign -m "migration-replay: overlay ${ARM} arm (base ${BASE_SHA:0:12})"
fi

echo "Sandbox ready: ${OUT_DIR}"
echo "  HEAD=$(git -C "$OUT_DIR" rev-parse HEAD)"
echo "  remotes=$(git -C "$OUT_DIR" remote | tr '\n' ' ' | sed 's/[[:space:]]*$//')"
