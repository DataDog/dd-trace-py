#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging"]
# ///
"""Automate dd-trace-py release steps (patch, RC, final).

Intent commands (resumable — re-run until a human gate):
  patch <PR> [--branch X.Y]  Backport a merged fix into a patch release draft.
  rc [--candidate SHA]       Cut the next release-candidate draft on main.
  final X.Y                  Ship X.Y.0 from the last RC (set-version + draft).
  post-release <version>     After a maintainer publishes: verify PyPI, open next bump.

Building blocks (also used by the intent commands):
  bump-rc / bump-version     Open a version-bump PR.
  ensure-branch              Create the X.Y release branch if missing.
  draft-release              Create a DRAFT GitHub release with formatted notes.

See docs/contributing-release.rst and .agents/skills/release/SKILL.md.
The script never publishes a GitHub release; --draft is non-negotiable.

Usage:
  scripts/gh/create_gh_release.py patch 21379 --branch 4.16 [--dry-run] [--slack|--test-dm]
  scripts/gh/create_gh_release.py rc [--candidate SHA] [--dry-run] [--slack|--test-dm]
  scripts/gh/create_gh_release.py final 4.17 [--dry-run] [--slack|--test-dm]
  scripts/gh/create_gh_release.py post-release v4.16.2 [--dry-run]
  scripts/gh/create_gh_release.py bump-rc [--base-branch main] [--dry-run]
  scripts/gh/create_gh_release.py ensure-branch X.Y [--from A.B] [--dry-run]
  scripts/gh/create_gh_release.py draft-release [--target main] [--version 4.9.0rc3]
                                   [--allow-version-mismatch] [--allow-ci-failure]
                                   [--slack] [--slack-channel guild|test|prod|auto]
                                   [--no-slack-precheck] [--test-dm]
                                   [--dry-run] [--verbose]

Slack env vars (preferred names; no DD_ prefix required for this tooling):
  DDTRACE_RELEASE_GUILD_CHANNEL_URL  webhook bound to #guild-dd-trace-py
                                     (default --slack target for review pings)
  DDTRACE_RELEASE_DM_URL             required for --test-dm (personal DM webhook;
                                     secret; not committed; no SSM path yet)
  DD_RELEASE_SLACK_WEBHOOK_URL_PROD  webhook bound to #apm-python-release
  DD_RELEASE_SLACK_WEBHOOK_URL_TEST  webhook bound to your test channel
  DD_RELEASE_SLACK_WEBHOOK_URL       legacy single-channel fallback
  DD_SLACK_USER_BRETT/EMMETT         optional overrides for reviewer @-mentions;
                                     defaults are hardcoded member IDs

Legacy fallbacks (still read if the new names are unset):
  DD_RELEASE_SLACK_WEBHOOK_URL_GUILD → DDTRACE_RELEASE_GUILD_CHANNEL_URL
  DD_RELEASE_SLACK_DM_URL            → DDTRACE_RELEASE_DM_URL

If the channel env var is unset and you pass --slack-channel=guild|test|prod,
the script falls back to AWS SSM Parameter Store at:
  ci.dd-trace-py.slack-release-webhook-guild
  ci.dd-trace-py.slack-release-webhook-prod
  ci.dd-trace-py.slack-release-webhook-test
This matches the ``ci.dd-trace-py.<key>`` convention used elsewhere in this
repo for release secrets (see .gitlab/release.yml). Requires AWS credentials
to be available (e.g. via aws-vault or your usual SSO flow). The DM webhook
is env-only (do not commit it); channel webhooks stay env-or-SSM.

Guild webhook setup (if unset): create a Slack Workflow Builder webhook that
posts to #guild-dd-trace-py with a ``text`` variable (same pattern as the DM
webhook), export it as DDTRACE_RELEASE_GUILD_CHANNEL_URL, and optionally
store it at the SSM path above.

Failure recovery:
  Push succeeded but `gh pr create` failed (bump-rc):
      git push origin --delete bump-version/<version>   # then re-run bump-rc

  Draft release created but Slack post failed (draft-release):
      The draft is still on GitHub; re-run with --slack / --test-dm to
      announce the existing draft URL (no second draft is created), or
      post manually.

  ensure-branch checked out a local branch but `git push` failed:
      git checkout main && git branch -D <X.Y>   # then re-run ensure-branch

Gotchas:
  - Slack Workflow Builder webhook with renamed `text` variable: the post
    succeeds at the HTTP layer but the message body comes through empty
    or templated. The --slack-precheck handshake (default: on) catches
    most cases by sending a sentinel first.
  - Hardcoded Brett/Emmett member IDs (or DD_SLACK_USER_* overrides) that
    go stale render as broken mentions like `<@U_OLD>` with no error.
    Refresh the constants from Slack profile when reviewers change.
  - Requires `gh` CLI 2.x for `--jq` support and `aws` CLI for SSM lookup.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 — script orchestrates git/gh/aws CLIs
import sys
import tempfile
from typing import Any
from typing import Optional
import urllib.error
import urllib.request

from packaging.version import InvalidVersion
from packaging.version import Version
import tomllib


# `parents[2]` because this script lives at scripts/gh/<file>.py — three levels
# below the repo root. If the script is moved, update this index. The assert
# below catches mismatches early so a path bug fails immediately rather than
# producing confusing "no such file" errors later in the flow.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
PYPROJECT: Path = REPO_ROOT / "pyproject.toml"
assert PYPROJECT.is_file(), f"REPO_ROOT misconfigured: {PYPROJECT} does not exist"  # nosec B101 — startup invariant; OK to drop under -O

# Slack webhook env vars. Channel webhooks: guild (#guild-dd-trace-py, default
# review ping), prod (#apm-python-release), test, plus legacy single-channel.
# ``--slack-channel`` selects which; ``--test-dm`` uses the DM webhook only.
# Preferred names drop the DD_ product prefix (local release tooling, not agent
# config). Legacy DD_RELEASE_SLACK_* names remain as fallbacks.
SLACK_CHANNEL_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL"
SLACK_CHANNEL_GUILD_ENV: str = "DDTRACE_RELEASE_GUILD_CHANNEL_URL"
SLACK_CHANNEL_GUILD_ENV_LEGACY: str = "DD_RELEASE_SLACK_WEBHOOK_URL_GUILD"
SLACK_CHANNEL_TEST_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL_TEST"
SLACK_CHANNEL_PROD_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL_PROD"
SLACK_DM_ENV: str = "DDTRACE_RELEASE_DM_URL"
SLACK_DM_ENV_LEGACY: str = "DD_RELEASE_SLACK_DM_URL"
SLACK_USER_BRETT_ENV: str = "DD_SLACK_USER_BRETT"
SLACK_USER_EMMETT_ENV: str = "DD_SLACK_USER_EMMETT"
# Hardcoded approver Slack member IDs (not secrets). Env vars above remain
# optional overrides. Source: Slack workspace lookup 2026-10-29
# (brettlangdon → UD1N1U7JQ; emmett.butler@datadoghq.com → U04HER8LNAZ).
SLACK_USER_BRETT_ID: str = "UD1N1U7JQ"
SLACK_USER_EMMETT_ID: str = "U04HER8LNAZ"
# #guild-dd-trace-py — Slack workspace lookup 2026-10-29 (not a secret).
SLACK_CHANNEL_GUILD_ID: str = "C02E4BKJQBT"
SLACK_CHANNEL_GUILD_NAME: str = "guild-dd-trace-py"

# AWS SSM Parameter Store paths. Canonical storage for shared release secrets
# in this repo — see .gitlab/release.yml and scripts/upload-debug-symbols-to-
# backend.sh for the same `ci.dd-trace-py.<key>` convention. The script falls
# back to SSM when the corresponding env var is unset, so a freshly-set-up
# release engineer with AWS creds (and no other config) can run the script
# without manually exporting webhook URLs. DM webhook has no SSM path yet
# (env-only; do not commit the secret URL). Guild SSM may need to be created
# once a Workflow Builder webhook for #guild-dd-trace-py exists.
SSM_REGION: str = "us-east-1"
SSM_SLACK_WEBHOOK_GUILD: str = "ci.dd-trace-py.slack-release-webhook-guild"
SSM_SLACK_WEBHOOK_PROD: str = "ci.dd-trace-py.slack-release-webhook-prod"
SSM_SLACK_WEBHOOK_TEST: str = "ci.dd-trace-py.slack-release-webhook-test"

# Required on every `gh release create`. Prerelease / latest are asserted
# separately from the version kind — see ``build_gh_release_args``.
_GH_RELEASE_REQUIRED_FLAGS: tuple[str, ...] = ("--draft",)

GH_REPO: str = "DataDog/dd-trace-py"
BACKPORT_LABEL_RE: re.Pattern[str] = re.compile(r"^backport\s+(\d+\.\d+)$")
SYSTEM_TESTS_PIN_MAX_AGE_DAYS: int = 14
REVIEW_TEXT_TEMPLATE: str = ":headsup: The draft release for {version} is ready for review. Can I get a :lgtm:? {url}"
# Light “small print” footer for Slack / review paste — italic via Slack `_..._`.
AUTO_CREATED_FOOTER: str = (
    "_Created automatically by `scripts/gh/create_gh_release.py` — try `patch` / `rc` / `final`._"
)

# When True, ``run_external_with_stderr`` echoes stderr from reno/pandoc/etc.
# to the user's terminal. Set by ``main`` from ``--verbose``.
_VERBOSE: bool = False


class WaitingOn(Exception):
    """Raised when the driver stops at a human gate. Exit code 2."""

    def __init__(self, message: str) -> None:
        self.message: str = message
        super().__init__(message)


@dataclass
class ReleaseState:
    """Live snapshot used by intent commands to decide the next step."""

    branch: str
    version: str
    sha: str = ""
    version_published: bool = False
    draft_urls: list[str] = field(default_factory=list)
    published_url: Optional[str] = None
    open_backport_prs: list[dict[str, Any]] = field(default_factory=list)
    open_bump_prs: list[dict[str, Any]] = field(default_factory=list)
    ci_overall: str = "unknown"
    ci_failing: list[str] = field(default_factory=list)
    ci_pending: list[str] = field(default_factory=list)
    did: list[str] = field(default_factory=list)
    waiting_on: Optional[str] = None

    def report(self) -> None:
        for line in self.did:
            print(f"did: {line}")
        if self.waiting_on:
            print(f"waiting on: {self.waiting_on}")
        else:
            print("waiting on: (nothing — ready for the next command or publish)")


def run(
    cmd: list[str], check: bool = True, capture: bool = False, cwd: Optional[Path] = None
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess, echoing the command for transparency."""
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(  # nosec B603 — cmd is argv list from controlled callers, never shell-interpreted
        cmd,
        check=check,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def run_out(cmd: list[str], cwd: Optional[Path] = None) -> str:
    """Run a subprocess and return stdout (stripped)."""
    cp: subprocess.CompletedProcess[str] = run(cmd, capture=True, cwd=cwd)
    return str(cp.stdout).strip()


def read_pyproject_version(source: Path = PYPROJECT) -> str:
    with open(source, "rb") as f:
        data: dict[str, Any] = tomllib.load(f)
    return str(data["project"]["version"])


def read_pyproject_version_at(ref: str) -> str:
    """Read the pyproject.toml [project.version] at a given git ref."""
    blob: str = run_out(["git", "show", f"{ref}:pyproject.toml"], cwd=REPO_ROOT)
    data: dict[str, Any] = tomllib.loads(blob)
    return str(data["project"]["version"])


def write_pyproject_version(new_version: str, source: Path = PYPROJECT) -> None:
    """Replace the single ``version = "..."`` (or single-quoted) line in pyproject.toml.

    Preserves the original quote style if it was single-quoted; otherwise emits
    double quotes (matching the current convention in dd-trace-py).
    """
    text: str = source.read_text()
    # TOML accepts both '...' and "..." — capture the original quote so we can
    # preserve it on rewrite. Constraining the inner pattern to the same quote
    # avoids matching across quote styles (e.g. on a malformed line).
    pattern: re.Pattern[str] = re.compile(r"^(?P<lead>version\s*=\s*)(?P<q>['\"])[^'\"]+(?P=q)\s*$", re.MULTILINE)
    match: Optional[re.Match[str]] = pattern.search(text)
    if match is None:
        raise SystemExit(f"could not locate single 'version = ...' line in {source}")
    quote: str = match.group("q")
    new_line: str = f"{match.group('lead')}{quote}{new_version}{quote}"
    new_text: str
    n: int
    new_text, n = pattern.subn(new_line, text, count=1)
    if n != 1:
        raise SystemExit(f"unexpectedly matched {n} version lines in {source}")
    source.write_text(new_text)


def compute_next_rc(current: str) -> str:
    """Increment the rcN suffix by one. Errors if not an rc version."""
    v: Version = Version(current)
    if not (v.is_prerelease and v.pre is not None and v.pre[0] == "rc"):
        raise SystemExit(f"current version {current!r} is not an rc; refusing to bump")
    rc_num: int = v.pre[1]
    return f"{v.major}.{v.minor}.{v.micro}rc{rc_num + 1}"


def is_rc_version(version: str) -> bool:
    v: Version = Version(version)
    return bool(v.is_prerelease and v.pre is not None and v.pre[0] == "rc")


def is_stable_version(version: str) -> bool:
    v: Version = Version(version)
    return not v.is_prerelease and not v.is_devrelease


def normalize_version_str(version: str) -> str:
    return version[1:] if version.startswith("v") else version


def xy_branch_for_version(version: str) -> str:
    v: Version = Version(normalize_version_str(version))
    return f"{v.major}.{v.minor}"


def compute_next_patch(current: str) -> str:
    v: Version = Version(normalize_version_str(current))
    if not is_stable_version(str(v)):
        raise SystemExit(f"expected a stable patch version, got {current!r}")
    return f"{v.major}.{v.minor}.{v.micro + 1}"


def compute_next_minor_rc1(current: str) -> str:
    v: Version = Version(normalize_version_str(current))
    return f"{v.major}.{v.minor + 1}.0rc1"


def compute_final_version(xy: str) -> str:
    if not re.fullmatch(r"\d+\.\d+", xy):
        raise SystemExit(f"final target must look like X.Y, got {xy!r}")
    return f"{xy}.0"


def should_mark_latest(version: str, published_stable: list[str]) -> bool:
    """True when ``version`` is a stable release strictly above every published stable."""
    if is_rc_version(version) or not is_stable_version(version):
        return False
    candidate: Version = Version(normalize_version_str(version))
    for tag in published_stable:
        try:
            other: Version = Version(normalize_version_str(tag))
        except InvalidVersion:
            continue
        if other.is_prerelease or other.is_devrelease:
            continue
        if other >= candidate:
            return False
    return True


def build_gh_release_args(
    version: str,
    target_sha: str,
    notes_path: str,
    *,
    latest: bool,
) -> list[str]:
    """Assemble ``gh release create`` argv. Always draft; prerelease iff RC."""
    args: list[str] = [
        "gh",
        "release",
        "create",
        f"v{normalize_version_str(version)}",
        "--draft",
        "--target",
        target_sha,
        "--title",
        normalize_version_str(version),
        "--notes-file",
        notes_path,
    ]
    if is_rc_version(version):
        args.append("--prerelease")
        args.append("--latest=false")
    else:
        args.append(f"--latest={'true' if latest else 'false'}")
    missing: list[str] = [f for f in _GH_RELEASE_REQUIRED_FLAGS if f not in args]
    assert not missing, f"gh release create args missing required flags: {missing}"  # nosec B101
    if is_rc_version(version):
        assert "--prerelease" in args, "RC drafts must pass --prerelease"  # nosec B101
    else:
        assert "--prerelease" not in args, "non-RC drafts must not pass --prerelease"  # nosec B101
    return args


def draft_kind_phrase(version: str) -> str:
    """Human label for Slack: ``RC release``, ``patch release``, or ``release``."""
    ver: str = normalize_version_str(version)
    if is_rc_version(ver):
        return "RC release"
    if is_stable_version(ver):
        v: Version = Version(ver)
        if v.micro > 0:
            return "patch release"
        return "release"
    return "release"


def review_text(version: str, url: str) -> str:
    head: str = REVIEW_TEXT_TEMPLATE.format(version=normalize_version_str(version), url=url)
    return f"{head}\n{AUTO_CREATED_FOOTER}"


def load_format_release_notes() -> Any:
    """Import ``scripts/format_release_notes.py`` as a module."""
    path: Path = REPO_ROOT / "scripts" / "format_release_notes.py"
    spec = importlib.util.spec_from_file_location("dd_format_release_notes", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ensure_clean_tree() -> None:
    # Untracked files are fine; only tracked-file modifications block branch
    # operations. We stage only pyproject.toml later, so other dirty tracked
    # files would leak into the bump commit.
    out: str = run_out(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT)
    if out:
        raise SystemExit(f"working tree has uncommitted changes:\n{out}\nstash or commit before running")


def ensure_gh_auth() -> None:
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
        ["gh", "auth", "status"], capture_output=True, text=True
    )
    if cp.returncode != 0:
        raise SystemExit("gh CLI is not authenticated. Run `gh auth login` first.")


def cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_external_with_stderr(cmd: list[str], stdin_text: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    """Like ``subprocess.run(check=True)`` but optionally echoes stderr.

    Used for reno/pandoc/aws calls where warnings ("reference not found",
    rate-limit notices) often hide useful diagnostic info. With ``--verbose``,
    the stderr is forwarded to the user's terminal in real time; otherwise it's
    captured and only surfaced on failure.
    """
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 — cmd is argv list from controlled callers
        cmd,
        input=stdin_text,
        stdout=subprocess.PIPE,
        stderr=None if _VERBOSE else subprocess.PIPE,
        text=True,
        check=False,
    )
    if cp.returncode != 0:
        if cp.stderr and not _VERBOSE:
            print(cp.stderr, file=sys.stderr)
        raise SystemExit(f"{cmd[0]} failed with exit code {cp.returncode}")
    return cp


def print_tool_versions() -> None:
    """Print versions of the external tools we invoke so log-based diagnosis
    of body-shape oddities is tractable. Best-effort: missing tools produce a
    single line, not an error.
    """
    tools: list[tuple[str, list[str]]] = [
        ("git", ["git", "--version"]),
        ("gh", ["gh", "--version"]),
        ("aws", ["aws", "--version"]),
        ("reno", ["reno", "--version"]),
        ("pandoc", ["pandoc", "--version"]),
    ]
    print("---tool versions---", file=sys.stderr)
    for name, cmd in tools:
        if not cmd_exists(cmd[0]):
            print(f"  {name}: not on PATH", file=sys.stderr)
            continue
        try:
            cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 — cmd is hardcoded version-flag invocations
                cmd, capture_output=True, text=True, check=False, timeout=5
            )
            # Most tools print version info on stdout; some on stderr (gh, aws).
            line: str = (cp.stdout or cp.stderr or "").splitlines()[0] if (cp.stdout or cp.stderr) else "(no output)"
            print(f"  {name}: {line.strip()}", file=sys.stderr)
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"  {name}: error querying version ({e})", file=sys.stderr)
    print("-------------------", file=sys.stderr)


def get_gh_oauth_scopes() -> set[str]:
    """Return the set of OAuth scopes on the current `gh` token.

    Uses ``gh auth status`` since the API headers aren't reliably exposed via
    `gh api`. Returns an empty set on any error so the caller decides whether
    to be strict.
    """
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
        ["gh", "auth", "status"], capture_output=True, text=True, check=False
    )
    # The output looks like "Token scopes: 'repo', 'read:org', 'workflow'".
    # Stable since gh 2.0 but we're tolerant of format drift.
    combined: str = (cp.stdout or "") + "\n" + (cp.stderr or "")
    m: Optional[re.Match[str]] = re.search(r"Token scopes?:\s*(.+)", combined)
    if not m:
        return set()
    raw: str = m.group(1)
    return {s.strip().strip("'\"") for s in raw.split(",")}


def ensure_gh_push_scope() -> None:
    """Refuse to proceed if the current ``gh`` token can't push to this repo.

    Required scopes: ``repo`` (covers contents:write + pull-requests:write),
    or fine-grained equivalent. Fails fast so we don't push a branch and then
    discover the token can't open the PR.
    """
    scopes: set[str] = get_gh_oauth_scopes()
    if not scopes:
        # Couldn't read scopes — could be a fine-grained token, custom GHE, or
        # gh format drift. Print a soft warning rather than blocking.
        print(
            "WARN: could not determine gh token scopes; if push fails, run "
            "`gh auth refresh -h github.com -s repo` and retry.",
            file=sys.stderr,
        )
        return
    has_write: bool = any(s == "repo" or s.startswith("public_repo") for s in scopes)
    if not has_write:
        raise SystemExit(
            f"gh token has scopes {sorted(scopes)} which lack write access. "
            "Run `gh auth refresh -h github.com -s repo` and retry."
        )


def fetch_ssm_parameter(name: str) -> str:
    """Read a value from AWS SSM Parameter Store. Errors with actionable
    guidance if the AWS CLI is missing or the call fails (auth, permissions,
    or non-existent parameter).
    """
    if not cmd_exists("aws"):
        raise SystemExit(
            "aws CLI is not installed; cannot fetch SSM parameter. "
            "Install via `brew install awscli` or set the corresponding env "
            "var to the literal URL."
        )
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes aws CLI, trusted PATH
        [
            "aws",
            "ssm",
            "get-parameter",
            "--region",
            SSM_REGION,
            "--name",
            name,
            "--with-decryption",
            "--query",
            "Parameter.Value",
            "--output",
            "text",
        ],
        capture_output=True,
        text=True,
    )
    if cp.returncode != 0:
        raise SystemExit(
            f"failed to fetch SSM parameter {name!r} in {SSM_REGION}: "
            f"{cp.stderr.strip() or cp.stdout.strip()}\n"
            "Make sure you're authenticated to AWS (e.g. via `aws-vault exec` "
            "or your usual SSO flow) and have ssm:GetParameter on this name."
        )
    return str(cp.stdout).strip()


# --------------------------------------------------------------------------- #
# bump-rc
# --------------------------------------------------------------------------- #


def get_commit_ci_summary(sha: str) -> tuple[str, list[str], list[str], int]:
    """Return CI status for a commit as ``(overall_state, failing, pending, passing_count)``.

    Combines two GitHub APIs:

    - ``/commits/{sha}/status`` — the legacy "combined commit status" (used by
      GitLab CI's GitHub bridge in this repo). Returns a single ``state`` field
      and a list of per-context statuses.
    - ``/commits/{sha}/check-runs`` — the modern Checks API (used by GitHub
      Actions and some other tools). Each check-run has ``status`` and
      ``conclusion`` fields.

    Both must report green for the commit to be considered "fully passing".
    Pending or failing items show up in the ``pending``/``failing`` lists so the
    runner can see exactly what's wrong.
    """
    # Legacy combined-status API. The top-level ``state`` field is the
    # authoritative roll-up across ALL statuses (even those on later pages);
    # the ``statuses`` array on a single response is capped, so we --paginate
    # the per-context list separately to enumerate names.
    state_out: str = run_out(
        [
            "gh",
            "api",
            f"repos/DataDog/dd-trace-py/commits/{sha}/status",
            "--jq",
            ".state",
        ],
        cwd=REPO_ROOT,
    )
    overall_state: str = state_out.strip() or "pending"

    failing: list[str] = []
    pending: list[str] = []
    passing_count: int = 0

    statuses_out: str = run_out(
        [
            "gh",
            "api",
            f"repos/DataDog/dd-trace-py/commits/{sha}/statuses",
            "--paginate",
            "--jq",
            ".[] | {context, state}",
        ],
        cwd=REPO_ROOT,
    )
    # Pagination returns most-recent first; track the latest state per context
    # so older superseded statuses don't double-count.
    latest_state_by_ctx: dict[str, str] = {}
    for line in statuses_out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            s: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        ctx_name: str = s.get("context", "<unknown>")
        latest_state_by_ctx.setdefault(ctx_name, s.get("state", ""))
    for ctx_name, ctx_state in latest_state_by_ctx.items():
        if ctx_state == "success":
            passing_count += 1
        elif ctx_state == "pending":
            pending.append(ctx_name)
        elif ctx_state in ("failure", "error"):
            failing.append(ctx_name)

    # Modern check-runs API.
    runs_out: str = run_out(
        [
            "gh",
            "api",
            f"repos/DataDog/dd-trace-py/commits/{sha}/check-runs",
            "--jq",
            ".check_runs[] | {name, status, conclusion}",
        ],
        cwd=REPO_ROOT,
    )
    for line in runs_out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            run_obj: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        name: str = run_obj.get("name", "<unknown>")
        run_status: str = run_obj.get("status", "")
        conclusion: str = run_obj.get("conclusion") or ""
        if run_status != "completed":
            pending.append(f"{name} (check-run)")
            if overall_state == "success":
                overall_state = "pending"
        elif conclusion in ("success", "neutral", "skipped"):
            passing_count += 1
        else:
            # failure, timed_out, cancelled, action_required, stale, ...
            failing.append(f"{name} (check-run, {conclusion or 'unknown'})")
            if overall_state == "success":
                overall_state = "failure"

    return overall_state, failing, pending, passing_count


def ensure_ci_green(sha: str, allow_failure: bool = False) -> None:
    """Refuse to draft a release on a commit whose CI isn't green.

    Brett's ask: "validate that CI has completed and is green for the
    target-to-be-released commit". Defensive against the "I cut a release from
    a broken commit" foot-gun. ``allow_failure=True`` (from
    ``--allow-ci-failure``) downgrades to a warning since some teams accept a
    handful of flaky CI signals.
    """
    overall: str
    failing: list[str]
    pending: list[str]
    passing: int
    overall, failing, pending, passing = get_commit_ci_summary(sha)
    label: str = f"CI: state={overall}, passing={passing}, pending={len(pending)}, failing={len(failing)}"
    if overall == "success" and not failing and not pending:
        print(label)
        return
    detail: str = label
    if failing:
        detail += "\n  failing:\n    " + "\n    ".join(failing[:10])
        if len(failing) > 10:
            detail += f"\n    ... and {len(failing) - 10} more"
    if pending:
        detail += "\n  pending:\n    " + "\n    ".join(pending[:10])
        if len(pending) > 10:
            detail += f"\n    ... and {len(pending) - 10} more"
    if allow_failure:
        print(f"WARN (--allow-ci-failure): {detail}", file=sys.stderr)
        return
    raise SystemExit(
        f"refusing to draft release on a commit with non-green CI. {detail}\n"
        "Either wait for CI to complete on origin/<target>, or pass "
        "--allow-ci-failure to override (e.g. for known-flaky red checks)."
    )


def _release_tag_is_published(tag: str) -> bool:
    """True iff a non-draft release exists for the given tag."""
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
        ["gh", "release", "view", tag, "--json", "isDraft", "--jq", ".isDraft"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    if cp.returncode != 0:
        # `gh release view` exits non-zero when the release doesn't exist.
        # That's the common case we're checking for, not an error.
        return False
    return str(cp.stdout).strip().lower() == "false"


def cmd_bump_rc(args: argparse.Namespace) -> int:
    base_branch: str = args.base_branch
    dry_run: bool = args.dry_run

    if not dry_run:
        ensure_gh_auth()
        ensure_gh_push_scope()
        ensure_clean_tree()

    run(["git", "fetch", "origin", base_branch], cwd=REPO_ROOT)
    if not dry_run:
        run(["git", "switch", base_branch], cwd=REPO_ROOT)
        run(["git", "pull", "--ff-only", "origin", base_branch], cwd=REPO_ROOT)

    current: str = read_pyproject_version()
    next_version: str = compute_next_rc(current)

    # Pre-flight: refuse to bump *forward* into a version that's already been
    # published. This catches the "stale branch" foot-gun: pyproject is at an
    # old rc, the script computes the next rc, but that next rc was already
    # published (because newer bumps have happened on origin in the meantime).
    # The canonical flow (rcN published → bump to rc(N+1)) is unaffected
    # because v(rc(N+1)) won't be published yet.
    if _release_tag_is_published(f"v{next_version}"):
        raise SystemExit(
            f"pyproject is at {current!r}; this would bump to {next_version!r}, "
            f"but v{next_version} is already a published release. The branch "
            "state is likely stale — pull the latest base branch and retry."
        )
    # Avoid `release/...` prefix: `release` already exists as a leaf branch on
    # origin, which would cause a "directory file conflict" on push.
    branch: str = f"bump-version/{next_version}"

    print(f"current version: {current}")
    print(f"next version:    {next_version}")
    print(f"new branch:      {branch}")
    print(f"base branch:     {base_branch}")

    if dry_run:
        print("(dry-run) would: create branch, edit pyproject.toml, commit, push, open PR")
        return 0

    # Pre-flight: refuse to clobber an existing branch with the same name.
    # Without this, `git push -u origin <branch>` could fast-forward a stale
    # branch from a prior failed run, yielding a confusingly-stacked PR.
    if remote_branch_exists(branch):
        raise SystemExit(
            f"branch {branch!r} already exists on origin. Delete it first "
            f"(`git push origin --delete {branch}`) or rebump."
        )
    local_check: subprocess.CompletedProcess[bytes] = subprocess.run(  # nosec B603 B607 — invokes git CLI, trusted PATH
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    if local_check.returncode == 0:
        raise SystemExit(
            f"local branch {branch!r} already exists. Delete it first (`git branch -D {branch}`) or rebump."
        )

    run(["git", "switch", "-c", branch], cwd=REPO_ROOT)
    write_pyproject_version(next_version)

    # Sanity check via the existing PEP 440 verifier.
    verifier: Path = REPO_ROOT / "scripts" / "verify-package-version"
    if verifier.is_file():
        run([str(verifier)], cwd=REPO_ROOT)

    run(["git", "add", "pyproject.toml"], cwd=REPO_ROOT)
    run(["git", "commit", "-m", f"chore: bump {next_version}"], cwd=REPO_ROOT)
    run(["git", "push", "-u", "origin", branch], cwd=REPO_ROOT)
    branch_pushed: bool = True

    pr_body: str = (
        f"Bump pyproject.toml to {next_version}.\n\n"
        f"Generated by `scripts/gh/create_gh_release.py bump-rc`.\n"
        f"Once merged, run `scripts/gh/create_gh_release.py draft-release` to create the GitHub draft."
    )
    try:
        pr_url: str = run_out(
            [
                "gh",
                "pr",
                "create",
                "--draft",
                "--base",
                base_branch,
                "--head",
                branch,
                "--label",
                "changelog/no-changelog",
                "--title",
                f"chore: bump {next_version}",
                "--body",
                pr_body,
            ],
            cwd=REPO_ROOT,
        )
    except subprocess.CalledProcessError:
        if branch_pushed:
            print(
                f"\nERROR: branch {branch!r} was pushed to origin but `gh pr create` "
                "failed. To recover, delete the orphaned branch before retrying:\n"
                f"    git push origin --delete {branch}\n"
                "Then re-run this command.",
                file=sys.stderr,
            )
        raise
    print(f"\nPR opened: {pr_url}")
    return 0


# --------------------------------------------------------------------------- #
# ensure-branch
# --------------------------------------------------------------------------- #


def remote_branch_exists(name: str) -> bool:
    out: str = run_out(["git", "ls-remote", "--heads", "origin", name], cwd=REPO_ROOT)
    return bool(out.strip())


def discover_prior_branch(target: str) -> str:
    """Highest origin/X.Y branch strictly less than `target`."""
    target_v: tuple[int, ...] = tuple(int(x) for x in target.split("."))
    out: str = run_out(["git", "ls-remote", "--heads", "origin"], cwd=REPO_ROOT)
    candidates: list[tuple[int, ...]] = []
    pattern: re.Pattern[str] = re.compile(r"refs/heads/(\d+)\.(\d+)$")
    for line in out.splitlines():
        m: Optional[re.Match[str]] = pattern.search(line)
        if not m:
            continue
        v: tuple[int, int] = (int(m.group(1)), int(m.group(2)))
        if v < target_v:
            candidates.append(v)
    if not candidates:
        raise SystemExit(f"no prior X.Y branch found before {target}; specify --from")
    prior: tuple[int, ...] = max(candidates)
    return f"{prior[0]}.{prior[1]}"


def cmd_ensure_branch(args: argparse.Namespace) -> int:
    target: str = args.target
    if not re.fullmatch(r"\d+\.\d+", target):
        raise SystemExit(f"branch name must look like X.Y, got {target!r}")

    run(["git", "fetch", "origin"], cwd=REPO_ROOT)

    if remote_branch_exists(target):
        print(f"origin/{target} already exists — nothing to do")
        # Empty-diff check vs origin/main when requested via --check-empty-diff.
        if getattr(args, "check_empty_diff", False):
            run(["git", "fetch", "origin", "main", target], cwd=REPO_ROOT)
            diff: str = run_out(["git", "diff", "--stat", "origin/main", f"origin/{target}"], cwd=REPO_ROOT)
            if diff.strip():
                raise WaitingOn(f"origin/{target} is not empty-diff vs origin/main:\n{diff}")
            print(f"origin/{target} empty-diff vs origin/main: ok")
        return 0

    prior: str = args.from_branch or discover_prior_branch(target)
    print(f"creating origin/{target} from origin/{prior}")

    if args.dry_run:
        print("(dry-run) would run:")
        print(f"  git checkout {prior} && git pull --ff-only")
        print(f"  git checkout -b {target}")
        print("  git merge main -Xtheirs")
        print(f"  git push -u origin {target}")
        return 0

    ensure_clean_tree()
    run(["git", "checkout", prior], cwd=REPO_ROOT)
    run(["git", "pull", "--ff-only", "origin", prior], cwd=REPO_ROOT)
    run(["git", "checkout", "-b", target], cwd=REPO_ROOT)
    run(["git", "merge", "main", "-Xtheirs"], cwd=REPO_ROOT)
    run(["git", "push", "-u", "origin", target], cwd=REPO_ROOT)
    print(f"\norigin/{target} created")
    return 0


# --------------------------------------------------------------------------- #
# draft-release
# --------------------------------------------------------------------------- #


def generate_raw_reno_gfm(target: str) -> str:
    """Run reno+pandoc against ``origin/<target>`` and return GFM markdown."""
    if not cmd_exists("reno"):
        raise SystemExit("reno not found on PATH; run via `hatch run` or activate the venv")
    if not cmd_exists("pandoc"):
        raise SystemExit("pandoc not found on PATH")
    reno_out: str = str(run_external_with_stderr(["reno", "report", f"--branch=origin/{target}"]).stdout)
    pandoc: subprocess.CompletedProcess[str] = run_external_with_stderr(
        ["pandoc", "-f", "rst", "-t", "gfm", "--wrap=none"], stdin_text=reno_out
    )
    return str(pandoc.stdout)


def format_notes_for_version(
    raw_gfm: str,
    earliest_version: str,
    *,
    collapse_release_candidates: bool = False,
    exclude_unreleased: bool = False,
) -> str:
    """Format reno GFM via ``scripts/format_release_notes.py``.

    When ``earliest_version`` is not yet present in the reno report (typical for
    an unpublished patch/RC), fall back to the ``Unreleased`` section only.
    """
    formatter = load_format_release_notes()
    try:
        formatted: str = formatter.format_release_notes(
            raw_gfm,
            earliest_version=earliest_version,
            exclude_unreleased=exclude_unreleased,
            collapse_release_candidates=collapse_release_candidates,
        )
    except ValueError:
        if exclude_unreleased:
            raise SystemExit(f"version {earliest_version!r} not found in reno output and Unreleased is excluded")
        formatted = formatter.format_release_notes(
            raw_gfm,
            earliest_version="Unreleased",
            exclude_unreleased=False,
            collapse_release_candidates=collapse_release_candidates,
        )
    return formatted.strip()


def generate_release_notes(
    target: str,
    version: str,
    *,
    collapse_release_candidates: bool = False,
    earliest_version: Optional[str] = None,
) -> str:
    """Produce formatted notes for a draft release on ``origin/<target>``."""
    raw: str = generate_raw_reno_gfm(target)
    earliest: str = earliest_version or f"v{normalize_version_str(version)}"
    # For RC/patch drafts, collapse is usually off; finals pass earliest=rc1 + collapse.
    return format_notes_for_version(
        raw,
        earliest,
        collapse_release_candidates=collapse_release_candidates,
    )


def existing_releases_for_version(version: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(published, drafts)`` for tag ``v{version}`` via the releases API."""
    ver: str = normalize_version_str(version)
    out: str = run_out(
        [
            "gh",
            "api",
            f"repos/{GH_REPO}/releases",
            "--paginate",
            "--jq",
            f'.[] | select(.tag_name == "v{ver}") | {{id, draft, html_url}}',
        ],
        cwd=REPO_ROOT,
    )
    published: list[dict[str, Any]] = []
    drafts: list[dict[str, Any]] = []
    if not out.strip():
        return published, drafts
    # The query may return multiple JSON objects (one per matching release);
    # iterate over each line.
    for line in out.splitlines():
        stripped: str = line.strip()
        if not stripped:
            continue
        try:
            r: dict[str, Any] = json.loads(stripped)
        except ValueError:
            continue
        (drafts if r.get("draft") else published).append(r)
    return published, drafts


def check_existing_release(version: str, dry_run: bool = False, *, reuse_drafts: bool = False) -> Optional[str]:
    """Refuse if published; optionally reuse or warn about existing drafts.

    When ``reuse_drafts`` is True and a draft already exists, return its
    ``html_url`` so the caller can re-announce without creating a duplicate.
    Otherwise warn (and sleep) before allowing another draft create.
    """
    published: list[dict[str, Any]]
    drafts: list[dict[str, Any]]
    published, drafts = existing_releases_for_version(version)
    if published:
        urls: str = ", ".join(r["html_url"] for r in published)
        raise SystemExit(
            f"a release tagged v{normalize_version_str(version)} is already PUBLISHED ({urls}). "
            "Refusing to create another draft for the same tag."
        )
    if drafts and reuse_drafts:
        existing_url: str = str(drafts[0].get("html_url") or "")
        if not existing_url:
            raise SystemExit(f"draft(s) exist for v{normalize_version_str(version)} but html_url is missing")
        return existing_url
    if drafts and not dry_run:
        draft_urls: str = "\n  ".join(r["html_url"] for r in drafts)
        print(
            f"WARN: {len(drafts)} draft(s) already exist for v{normalize_version_str(version)}:\n"
            f"  {draft_urls}\n"
            "Creating another draft will leave duplicates. Continuing in 3s "
            "(Ctrl-C to abort)..."
        )
        import time

        time.sleep(3)
    return None


def list_published_release_tags(limit: int = 200) -> list[dict[str, Any]]:
    out: str = run_out(
        [
            "gh",
            "release",
            "list",
            "--limit",
            str(limit),
            "--json",
            "tagName,isPrerelease,isDraft,isLatest,publishedAt",
        ],
        cwd=REPO_ROOT,
    )
    return json.loads(out) if out.strip() else []


def published_stable_tags() -> list[str]:
    tags: list[str] = []
    for rel in list_published_release_tags():
        if rel.get("isDraft"):
            continue
        tag: str = rel.get("tagName") or ""
        try:
            ver: Version = Version(normalize_version_str(tag))
        except InvalidVersion:
            continue
        if ver.is_prerelease or ver.is_devrelease:
            continue
        tags.append(tag)
    return tags


def find_eol_block(version: str) -> str:
    """Copy EOL from the newest published ``vX.Y.*`` release of any kind.

    Refuses with a clear error if none is found (lesson 7: never write TBD).
    """
    v: Version = Version(normalize_version_str(version))
    major: int = v.major
    minor: int = v.minor
    pat: re.Pattern[str] = re.compile(rf"^v{major}\.{minor}\.")
    candidates: list[tuple[Version, str]] = []
    for rel in list_published_release_tags():
        if rel.get("isDraft"):
            continue
        tag: str = rel.get("tagName") or ""
        if not pat.match(tag):
            continue
        if tag == f"v{normalize_version_str(version)}":
            continue
        try:
            candidates.append((Version(normalize_version_str(tag)), tag))
        except InvalidVersion:
            continue
    if not candidates:
        raise SystemExit(
            f"no published v{major}.{minor}.* release found to copy the EOL block from. "
            "Refuse to draft with TBD — paste EOL from the prior major/minor release manually "
            "or publish that line's first release first."
        )
    candidates.sort(key=lambda item: item[0], reverse=True)
    tag = candidates[0][1]
    body: str = run_out(
        ["gh", "release", "view", tag, "--json", "body", "--jq", ".body"],
        cwd=REPO_ROOT,
    )
    for line in body.splitlines():
        if line.strip().startswith("Estimated end-of-life date"):
            return line.strip()
    raise SystemExit(
        f"published release {tag} has no 'Estimated end-of-life date' line to copy. Refuse to draft with TBD."
    )


def compose_release_body(notes: str, eol_block: str) -> str:
    eol_full: str = (
        f"{eol_block}\n"
        "See [the support level definitions]"
        "(https://docs.datadoghq.com/tracing/trace_collection/compatibility/python/#releases) "
        "for more information."
        if "support level definitions" not in eol_block
        else eol_block
    )
    return f"{eol_full}\n\n{notes}\n"


def cmd_draft_release(args: argparse.Namespace) -> int:
    target: str = args.target
    dry_run: bool = args.dry_run

    if not dry_run:
        ensure_gh_auth()

    run(["git", "fetch", "origin", target], cwd=REPO_ROOT)
    target_sha: str = run_out(["git", "rev-parse", f"origin/{target}"], cwd=REPO_ROOT)
    short_sha: str = target_sha[:12]

    target_pyproject_version: str = read_pyproject_version_at(target_sha)
    version: str = args.version or target_pyproject_version
    Version(version)  # validate format

    print(f"version:    {version}")
    print(f"target:     origin/{target} ({short_sha})")
    print(f"pyproject@target: {target_pyproject_version}")

    # Pre-flight: refuse if the runner's --version disagrees with pyproject on
    # the target branch. The doc-prescribed flow is to bump pyproject first
    # ([docs/contributing-release.rst, "Set / Validate version strings on
    # branches"]); skipping that step risks shipping a release with a
    # version-string mismatch (the wheel reports one version, the GitHub tag
    # claims another). --allow-version-mismatch is the explicit escape hatch
    # for the team's current practice of cutting subsequent rcs without
    # re-bumping pyproject.
    if args.version and args.version != target_pyproject_version and not args.allow_version_mismatch:
        raise SystemExit(
            f"--version {args.version!r} disagrees with pyproject@origin/{target} "
            f"({target_pyproject_version!r}). Either bump pyproject first (the "
            "doc-prescribed flow) or pass --allow-version-mismatch to override."
        )

    announce: bool = bool(getattr(args, "slack", False) or getattr(args, "test_dm", False))
    existing_draft_url: Optional[str] = check_existing_release(version, dry_run=dry_run, reuse_drafts=announce)
    if existing_draft_url is not None:
        print(f"\nExisting draft release: {existing_draft_url}")
        print("(reusing existing draft; not creating a duplicate)")
        print(review_text(version, existing_draft_url))
        if dry_run:
            print("(dry-run) would notify Slack about existing draft")
            return 0
        notify_slack(
            version=version,
            target_sha=short_sha,
            draft_url=existing_draft_url,
            test_dm=bool(args.test_dm),
            channel=str(getattr(args, "slack_channel", "guild")),
            precheck=bool(getattr(args, "slack_precheck", True)),
        )
        return 0

    ensure_ci_green(target_sha, allow_failure=args.allow_ci_failure)

    collapse: bool = bool(getattr(args, "collapse_rcs", False))
    earliest: Optional[str] = getattr(args, "earliest_version", None)
    notes: str = generate_release_notes(
        target,
        version,
        collapse_release_candidates=collapse,
        earliest_version=earliest,
    )
    eol_block: str = find_eol_block(version)
    body: str = compose_release_body(notes, eol_block)
    latest: bool = should_mark_latest(version, published_stable_tags())

    if dry_run:
        print("\n----- DRAFT RELEASE BODY -----")
        print(body)
        print("----- END BODY -----")
        preview_args: list[str] = build_gh_release_args(version, target_sha, "<notes>", latest=latest)
        print(f"\n(dry-run) would: {' '.join(preview_args)}")
        print(review_text(version, f"(draft URL for v{version})"))
        return 0

    notes_path: str
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        notes_path = f.name

    gh_release_args: list[str] = build_gh_release_args(version, target_sha, notes_path, latest=latest)

    try:
        url: str = run_out(gh_release_args, cwd=REPO_ROOT)
    finally:
        os.unlink(notes_path)

    print(f"\nDraft release: {url}")
    print(review_text(version, url))

    if announce:
        notify_slack(
            version=version,
            target_sha=short_sha,
            draft_url=url,
            test_dm=bool(args.test_dm),
            channel=str(getattr(args, "slack_channel", "guild")),
            precheck=bool(getattr(args, "slack_precheck", True)),
        )
    return 0


# --------------------------------------------------------------------------- #
# ReleaseState + shared GitHub helpers for intent commands
# --------------------------------------------------------------------------- #


def gh_json(cmd: list[str]) -> Any:
    out: str = run_out(cmd, cwd=REPO_ROOT)
    return json.loads(out) if out.strip() else None


def gh_pr_view(pr: int | str) -> dict[str, Any]:
    data: Any = gh_json(
        [
            "gh",
            "pr",
            "view",
            str(pr),
            "--repo",
            GH_REPO,
            "--json",
            "number,title,state,mergedAt,mergeCommit,baseRefName,headRefName,labels,url,commits",
        ]
    )
    if not isinstance(data, dict):
        raise SystemExit(f"could not load PR #{pr}")
    return data


def backport_labels(pr: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    for lab in pr.get("labels") or []:
        name: str = lab.get("name") or ""
        m: Optional[re.Match[str]] = BACKPORT_LABEL_RE.match(name)
        if m:
            labels.append(m.group(1))
    return labels


def find_backport_prs(pr_number: int, branch: str) -> list[dict[str, Any]]:
    """Find open or merged backport PRs for ``pr_number`` onto ``branch``."""
    query: str = f"repo:{GH_REPO} is:pr head:backport-{pr_number}-to-{branch}"
    data: Any = gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            GH_REPO,
            "--state",
            "all",
            "--search",
            query,
            "--json",
            "number,title,state,mergedAt,url,headRefName,baseRefName,labels",
            "--limit",
            "20",
        ]
    )
    results: list[dict[str, Any]] = []
    for item in data or []:
        head: str = item.get("headRefName") or ""
        base: str = item.get("baseRefName") or ""
        if base == branch and (
            f"backport-{pr_number}-to-{branch}" in head or head.startswith(f"backport-{pr_number}-")
        ):
            results.append(item)
    return results


def list_open_backports_to(branch: str) -> list[dict[str, Any]]:
    data: Any = gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            GH_REPO,
            "--base",
            branch,
            "--state",
            "open",
            "--json",
            "number,title,url,headRefName,labels",
            "--limit",
            "50",
        ]
    )
    open_bps: list[dict[str, Any]] = []
    for item in data or []:
        head: str = item.get("headRefName") or ""
        if head.startswith("backport-") or any(
            BACKPORT_LABEL_RE.match(lab.get("name") or "") for lab in (item.get("labels") or [])
        ):
            open_bps.append(item)
    return open_bps


def find_open_bump_prs(branch: str, version: str) -> list[dict[str, Any]]:
    data: Any = gh_json(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            GH_REPO,
            "--base",
            branch,
            "--state",
            "open",
            "--search",
            f"bump {version} in:title",
            "--json",
            "number,title,url,headRefName",
            "--limit",
            "20",
        ]
    )
    matches: list[dict[str, Any]] = []
    for item in data or []:
        title: str = item.get("title") or ""
        if version in title and ("bump" in title.lower() or "set version" in title.lower()):
            matches.append(item)
    return matches


def release_exists_for_tag(tag: str) -> tuple[bool, bool, Optional[str]]:
    """Return ``(exists, is_draft, url)`` for ``tag`` (with or without leading v)."""
    tag_name: str = tag if tag.startswith("v") else f"v{tag}"
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607
        ["gh", "release", "view", tag_name, "--json", "isDraft,url", "--jq", "{isDraft,url}"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    if cp.returncode != 0:
        return False, False, None
    try:
        data: dict[str, Any] = json.loads(cp.stdout)
    except ValueError:
        return False, False, None
    return True, bool(data.get("isDraft")), data.get("url")


def any_open_rc_draft() -> list[str]:
    """URLs of open draft releases whose tag looks like an RC."""
    urls: list[str] = []
    for rel in list_published_release_tags(limit=50):
        if not rel.get("isDraft"):
            continue
        tag: str = rel.get("tagName") or ""
        try:
            if is_rc_version(normalize_version_str(tag)):
                # release list JSON may not include url; reconstruct.
                urls.append(f"https://github.com/{GH_REPO}/releases/tag/{tag}")
        except InvalidVersion:
            continue
    # Also query drafts via API (untagged drafts).
    out: str = run_out(
        [
            "gh",
            "api",
            f"repos/{GH_REPO}/releases",
            "--paginate",
            "--jq",
            ".[] | select(.draft==true) | {tag_name, html_url, name}",
        ],
        cwd=REPO_ROOT,
    )
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        tag = obj.get("tag_name") or obj.get("name") or ""
        try:
            if tag and is_rc_version(normalize_version_str(tag)):
                urls.append(obj.get("html_url") or tag)
        except InvalidVersion:
            continue
    # Dedupe preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            uniq.append(u)
    return uniq


def commit_on_branch(sha: str, branch: str) -> bool:
    cp: subprocess.CompletedProcess[bytes] = subprocess.run(  # nosec B603 B607
        ["git", "merge-base", "--is-ancestor", sha, f"origin/{branch}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
    )
    return cp.returncode == 0


def find_cherry_picked_commit(pr_number: int, branch: str) -> Optional[str]:
    """Find a commit on ``origin/branch`` with a ``cherry picked from`` trailer for the PR."""
    run(["git", "fetch", "origin", branch], cwd=REPO_ROOT)
    log_out: str = run_out(
        [
            "git",
            "log",
            f"origin/{branch}",
            "--grep",
            f"#{pr_number}",
            "--format=%H",
            "-n",
            "20",
        ],
        cwd=REPO_ROOT,
    )
    for sha in log_out.splitlines():
        sha = sha.strip()
        if not sha:
            continue
        msg: str = run_out(["git", "log", "-1", "--format=%B", sha], cwd=REPO_ROOT)
        if re.search(rf"\(#{pr_number}\)|#{pr_number}\b", msg) and "cherry picked from" in msg.lower():
            return sha
    # Fallback: look for backport PR merge commit.
    for bp in find_backport_prs(pr_number, branch):
        if bp.get("mergedAt"):
            # Resolve merge commit via gh.
            view: dict[str, Any] = gh_pr_view(bp["number"])
            mc: Any = view.get("mergeCommit") or {}
            oid: Optional[str] = mc.get("oid") if isinstance(mc, dict) else None
            if oid and commit_on_branch(oid, branch):
                return oid
    return None


def inspect_branch_state(branch: str) -> ReleaseState:
    run(["git", "fetch", "origin", branch], cwd=REPO_ROOT)
    sha: str = run_out(["git", "rev-parse", f"origin/{branch}"], cwd=REPO_ROOT)
    version: str = read_pyproject_version_at(sha)
    exists, is_draft, url = release_exists_for_tag(version)
    published: bool = exists and not is_draft
    drafts: list[str] = [url] if exists and is_draft and url else []
    overall, failing, pending, _passing = get_commit_ci_summary(sha)
    state = ReleaseState(
        branch=branch,
        version=version,
        sha=sha,
        version_published=published,
        draft_urls=drafts,
        published_url=url if published else None,
        open_backport_prs=list_open_backports_to(branch),
        open_bump_prs=find_open_bump_prs(
            branch, compute_next_patch(version) if is_stable_version(version) else version
        ),
        ci_overall=overall,
        ci_failing=failing,
        ci_pending=pending,
    )
    return state


def open_version_bump_pr(
    *,
    base_branch: str,
    new_version: str,
    title: str,
    dry_run: bool,
    body_extra: str = "",
) -> Optional[str]:
    """Create a version-bump branch/PR. Returns PR URL or None on dry-run."""
    branch: str = f"bump-version/{new_version}"
    if dry_run:
        print(f"(dry-run) would open PR {title!r} on base {base_branch} via branch {branch}")
        return None
    ensure_gh_auth()
    ensure_gh_push_scope()
    ensure_clean_tree()
    run(["git", "fetch", "origin", base_branch], cwd=REPO_ROOT)
    run(["git", "switch", base_branch], cwd=REPO_ROOT)
    run(["git", "pull", "--ff-only", "origin", base_branch], cwd=REPO_ROOT)
    if remote_branch_exists(branch):
        raise WaitingOn(f"branch {branch!r} already exists on origin — reuse/merge that bump PR or delete it")
    run(["git", "switch", "-c", branch], cwd=REPO_ROOT)
    write_pyproject_version(new_version)
    verifier: Path = REPO_ROOT / "scripts" / "verify-package-version"
    if verifier.is_file():
        run([str(verifier)], cwd=REPO_ROOT)
    run(["git", "add", "pyproject.toml"], cwd=REPO_ROOT)
    run(["git", "commit", "-m", title], cwd=REPO_ROOT)
    run(["git", "push", "-u", "origin", branch], cwd=REPO_ROOT)
    pr_body: str = f"{title}.\n\nGenerated by `scripts/gh/create_gh_release.py`.\n{body_extra}"
    pr_url: str = run_out(
        [
            "gh",
            "pr",
            "create",
            "--draft",
            "--base",
            base_branch,
            "--head",
            branch,
            "--label",
            "changelog/no-changelog",
            "--title",
            title,
            "--body",
            pr_body,
        ],
        cwd=REPO_ROOT,
    )
    return pr_url


def ensure_label_exists(name: str, dry_run: bool) -> None:
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607
        ["gh", "label", "list", "--repo", GH_REPO, "--search", name, "--json", "name"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    names: list[str] = []
    if cp.returncode == 0 and cp.stdout.strip():
        try:
            names = [x.get("name") or "" for x in json.loads(cp.stdout)]
        except ValueError:
            names = []
    if name in names:
        print(f"label {name!r} already exists")
        return
    if dry_run:
        print(f"(dry-run) would create label {name!r}")
        return
    run(
        [
            "gh",
            "label",
            "create",
            name,
            "--repo",
            GH_REPO,
            "--color",
            "0E8A16",
            "--description",
            f"Backport merged PRs to the {name.split()[-1]} release branch",
        ],
        cwd=REPO_ROOT,
    )


def check_system_tests_pin_fresh(max_age_days: int = SYSTEM_TESTS_PIN_MAX_AGE_DAYS) -> None:
    path: Path = REPO_ROOT / ".github" / "workflows" / "system-tests.yml"
    if not path.is_file():
        raise SystemExit("missing .github/workflows/system-tests.yml")
    # Use git log age of the pin file on origin/main as a freshness proxy.
    age_out: str = run_out(
        ["git", "log", "-1", "--format=%ct", "origin/main", "--", str(path.relative_to(REPO_ROOT))],
        cwd=REPO_ROOT,
    )
    if not age_out.strip():
        print("WARN: could not determine system-tests pin age", file=sys.stderr)
        return
    import time

    age_days: float = (time.time() - int(age_out.strip())) / 86400.0
    if age_days > max_age_days:
        raise WaitingOn(
            f"system-tests pin in {path.name} last touched {age_days:.0f} days ago "
            f"(limit {max_age_days}). Run scripts/update-system-tests-version.py and merge the pin PR."
        )
    print(f"system-tests pin age: {age_days:.1f} days (ok)")


def check_libdatadog_released_tag(ref: str = "origin/main") -> None:
    blob: str = run_out(["git", "show", f"{ref}:src/native/Cargo.toml"], cwd=REPO_ROOT)
    revs: set[str] = set(re.findall(r'rev\s*=\s*"(v?[0-9][^"]*)"', blob))
    sha_like: list[str] = [r for r in revs if not re.fullmatch(r"v?\d+\.\d+\.\d+", r)]
    if not revs:
        raise SystemExit("could not find libdatadog rev= pins in src/native/Cargo.toml")
    if sha_like:
        raise WaitingOn(
            "src/native/Cargo.toml libdatadog pins are not released tags: "
            + ", ".join(sorted(sha_like))
            + ". Point them at a released vX.Y.Z tag before cutting an RC."
        )
    print(f"libdatadog pins are released tags: {', '.join(sorted(revs))}")


def check_slo_breaches_on_sha(sha: str) -> None:
    overall, failing, pending, _ = get_commit_ci_summary(sha)
    slo_fail: list[str] = [n for n in failing if "slo" in n.lower() or "check-slo-breaches" in n.lower()]
    slo_pending: list[str] = [n for n in pending if "slo" in n.lower() or "check-slo-breaches" in n.lower()]
    if slo_fail or slo_pending:
        raise WaitingOn("check-slo-breaches is not green on the candidate: " + ", ".join(slo_fail + slo_pending))
    if overall != "success":
        print(f"WARN: overall CI state is {overall!r}; SLO-named checks look clear", file=sys.stderr)
    else:
        print("check-slo-breaches: ok (no failing/pending SLO-named checks)")


def print_dogfood_checklist(version: str) -> None:
    ver: str = normalize_version_str(version)
    print(
        "\nDogfooding checklist (manual — not automated yet):\n"
        f"  1. dd-source: set ddtrace=={ver} in requirements.in, "
        "`bzl run //:requirements.update`, open PR; wait for #python approval before /propagate\n"
        f"  2. dogweb: set requirements.in, `rake python:compile_requirements`, "
        f"open PR titled `[apm] update ddtrace package to {ver}`; leave /to-staging to a human\n"
        "  3. Smoke: `rapid td create -s rapid_python_http_smoke_test` and "
        "`rapid td create -s rapid_python_grpc_smoke_test`\n"
        "  4. After publish: re-run `post-release` then (later) `dogfood` / `experiment`."
    )


def print_gitlab_watch_list(version: str) -> None:
    ver: str = normalize_version_str(version)
    print(
        f"\nGitLab tag pipeline jobs to watch for v{ver} (glab not required):\n"
        "  - build wheels / publish to PyPI\n"
        "  - upload debug symbols\n"
        "  - any store_gitlab_package / release jobs listed in the current runbook\n"
        f"  Pipeline: https://gitlab.ddbuild.io/DataDog/dd-trace-py/-/pipelines?ref=v{ver}"
    )


# --------------------------------------------------------------------------- #
# Intent: patch / rc / final / post-release
# --------------------------------------------------------------------------- #


def cmd_patch(args: argparse.Namespace) -> int:
    pr_number: int = int(args.pr)
    dry_run: bool = args.dry_run
    state = ReleaseState(branch="", version="", did=[])

    ensure_gh_auth()
    pr: dict[str, Any] = gh_pr_view(pr_number)
    state.did.append(f"loaded PR #{pr_number}: {pr.get('title')} ({pr.get('state')})")

    branch: Optional[str] = args.branch
    if not branch:
        labels: list[str] = backport_labels(pr)
        if len(labels) == 1:
            branch = labels[0]
        elif len(labels) > 1:
            raise SystemExit(
                f"PR #{pr_number} has multiple backport labels ({', '.join(labels)}); pass --branch X.Y explicitly"
            )
        else:
            raise SystemExit(f"PR #{pr_number} has no 'backport X.Y' label; pass --branch X.Y")
    if branch is None:
        raise SystemExit("internal error: release branch unresolved")
    if not re.fullmatch(r"\d+\.\d+", branch):
        raise SystemExit(f"--branch must look like X.Y, got {branch!r}")
    state.branch = branch

    if pr.get("state") != "MERGED" and not pr.get("mergedAt"):
        state.waiting_on = f"review/merge of fix PR #{pr_number} ({pr.get('url')})"
        state.report()
        raise WaitingOn(state.waiting_on)

    rc_drafts: list[str] = any_open_rc_draft()
    if rc_drafts:
        raise SystemExit(
            "refusing to cut a patch while an RC draft exists (runbook lesson 3):\n  " + "\n  ".join(rc_drafts)
        )

    # Ensure backport label is present so the workflow can fire / re-fire.
    label_name: str = f"backport {branch}"
    existing_labels: set[str] = {lab.get("name") or "" for lab in (pr.get("labels") or [])}
    if label_name not in existing_labels:
        if dry_run:
            state.did.append(f"(dry-run) would add label {label_name!r} to PR #{pr_number}")
        else:
            run(
                ["gh", "pr", "edit", str(pr_number), "--repo", GH_REPO, "--add-label", label_name],
                cwd=REPO_ROOT,
            )
            state.did.append(f"added label {label_name!r} to PR #{pr_number}")

    bps: list[dict[str, Any]] = find_backport_prs(pr_number, branch)
    open_bps: list[dict[str, Any]] = [b for b in bps if b.get("state") == "OPEN"]
    merged_bps: list[dict[str, Any]] = [b for b in bps if b.get("mergedAt") or b.get("state") == "MERGED"]

    if not bps:
        if dry_run:
            state.did.append(f"(dry-run) would wait for backport workflow or run scripts/backport {pr_number} {branch}")
            state.waiting_on = (
                f"backport PR for #{pr_number} onto {branch} (label applied / workflow, or scripts/backport fallback)"
            )
            state.report()
            raise WaitingOn(state.waiting_on)
        # Try triggering via label already done; fall back to local scripts/backport.
        backport_script: Path = REPO_ROOT / "scripts" / "backport"
        if backport_script.is_file():
            print(f"no backport PR yet — running {backport_script} {pr_number} {branch}")
            run([str(backport_script), str(pr_number), branch], cwd=REPO_ROOT)
            state.did.append(f"ran scripts/backport {pr_number} {branch}")
            bps = find_backport_prs(pr_number, branch)
            open_bps = [b for b in bps if b.get("state") == "OPEN"]
            merged_bps = [b for b in bps if b.get("mergedAt") or b.get("state") == "MERGED"]
        if not bps:
            state.waiting_on = f"backport workflow to open backport-{pr_number}-to-{branch} (re-run after it appears)"
            state.report()
            raise WaitingOn(state.waiting_on)

    if open_bps and not merged_bps:
        urls: str = ", ".join(b.get("url") or str(b.get("number")) for b in open_bps)
        state.waiting_on = f"review/merge of backport PR(s): {urls}"
        state.report()
        raise WaitingOn(state.waiting_on)

    run(["git", "fetch", "origin", branch], cwd=REPO_ROOT)
    picked: Optional[str] = find_cherry_picked_commit(pr_number, branch)
    if not picked:
        state.waiting_on = f"fix #{pr_number} to land on origin/{branch} (cherry picked from trailer / backport merge)"
        state.report()
        raise WaitingOn(state.waiting_on)
    state.did.append(f"found fix on origin/{branch} at {picked[:12]}")

    branch_state: ReleaseState = inspect_branch_state(branch)
    state.version = branch_state.version
    state.sha = branch_state.sha
    state.did.append(f"origin/{branch} pyproject={branch_state.version} sha={branch_state.sha[:12]}")

    others: list[dict[str, Any]] = [
        b for b in branch_state.open_backport_prs if f"backport-{pr_number}-" not in (b.get("headRefName") or "")
    ]
    if others:
        print("other open backports onto this branch (not auto-included):")
        for b in others:
            print(f"  #{b.get('number')} {b.get('title')} {b.get('url')}")

    if branch_state.version_published or _release_tag_is_published(f"v{branch_state.version}"):
        next_ver: str = compute_next_patch(branch_state.version)
        bump_open: list[dict[str, Any]] = find_open_bump_prs(branch, next_ver)
        if bump_open:
            state.waiting_on = f"merge of bump PR {bump_open[0].get('url')}"
            state.report()
            raise WaitingOn(state.waiting_on)
        title: str = f"chore: bump version to {next_ver}"
        pr_url: Optional[str] = open_version_bump_pr(
            base_branch=branch,
            new_version=next_ver,
            title=title,
            dry_run=dry_run,
        )
        state.did.append(f"opened bump PR to {next_ver}" if pr_url else f"(dry-run) bump to {next_ver}")
        state.waiting_on = f"merge of bump PR to {next_ver}" + (f" ({pr_url})" if pr_url else "")
        state.report()
        raise WaitingOn(state.waiting_on)

    # Version on branch is the unpublished patch we're releasing.
    release_version: str = branch_state.version
    if not is_stable_version(release_version):
        raise SystemExit(f"origin/{branch} version {release_version!r} is not a stable patch version")

    if xy_branch_for_version(release_version) != branch:
        raise SystemExit(f"version {release_version} does not match target branch {branch}")

    ensure_ci_green(branch_state.sha, allow_failure=args.allow_ci_failure)

    # Draft via shared draft-release path.
    draft_ns: argparse.Namespace = argparse.Namespace(
        target=branch,
        version=release_version,
        allow_version_mismatch=False,
        allow_ci_failure=args.allow_ci_failure,
        dry_run=dry_run,
        collapse_rcs=False,
        earliest_version=f"v{release_version}",
        **_slack_flag_values(args),
    )
    rc: int = cmd_draft_release(draft_ns)
    state.did.append(f"drafted v{release_version}" if not dry_run else f"(dry-run) drafted v{release_version}")
    state.waiting_on = "maintainer publish of the draft (tag ruleset); then run post-release"
    state.report()
    if rc == 0:
        raise WaitingOn(state.waiting_on)
    return rc


def cmd_rc(args: argparse.Namespace) -> int:
    dry_run: bool = args.dry_run
    ensure_gh_auth()
    run(["git", "fetch", "origin", "main"], cwd=REPO_ROOT)
    candidate: str = args.candidate or run_out(["git", "rev-parse", "origin/main"], cwd=REPO_ROOT)
    version: str = read_pyproject_version_at(candidate)
    if not is_rc_version(version):
        raise SystemExit(f"pyproject at candidate is {version!r}; expected an rc version on main")

    print(f"candidate: {candidate[:12]}  version: {version}")
    check_system_tests_pin_fresh()
    check_libdatadog_released_tag("origin/main")
    check_slo_breaches_on_sha(candidate)
    ensure_ci_green(candidate, allow_failure=args.allow_ci_failure)

    exists, is_draft, url = release_exists_for_tag(version)
    if exists and not is_draft:
        raise SystemExit(f"v{version} is already published ({url})")
    announce: bool = bool(getattr(args, "slack", False) or getattr(args, "test_dm", False))
    if exists and is_draft and url:
        if announce:
            print(review_text(version, url))
            if not dry_run:
                notify_slack(
                    version=version,
                    target_sha=candidate[:12],
                    draft_url=url,
                    test_dm=bool(args.test_dm),
                    channel=str(getattr(args, "slack_channel", "guild")),
                    precheck=bool(getattr(args, "slack_precheck", True)),
                )
            raise WaitingOn(f"existing draft for v{version}: {url} — announced; waiting on publish")
        raise WaitingOn(f"existing draft for v{version}: {url} — review/publish or delete before re-drafting")

    xy: str = xy_branch_for_version(version)
    # ensure-branch + empty-diff check
    if remote_branch_exists(xy):
        run(["git", "fetch", "origin", xy], cwd=REPO_ROOT)
        diff: str = run_out(["git", "diff", "--stat", candidate, f"origin/{xy}"], cwd=REPO_ROOT)
        if diff.strip():
            raise WaitingOn(
                f"origin/{xy} differs from candidate {candidate[:12]}:\n{diff}\n"
                "Reconcile before drafting (empty-diff check)."
            )
        print(f"origin/{xy} matches candidate (empty diff)")
    else:
        eb_ns: argparse.Namespace = argparse.Namespace(target=xy, from_branch=None, dry_run=dry_run, verbose=False)
        cmd_ensure_branch(eb_ns)
        if not dry_run:
            run(["git", "fetch", "origin", xy], cwd=REPO_ROOT)
            diff = run_out(["git", "diff", "--stat", candidate, f"origin/{xy}"], cwd=REPO_ROOT)
            if diff.strip():
                raise WaitingOn(f"newly created origin/{xy} is not empty-diff vs candidate:\n{diff}")

    ensure_label_exists(f"backport {xy}", dry_run=dry_run)

    draft_ns: argparse.Namespace = argparse.Namespace(
        target="main" if not remote_branch_exists(xy) else xy,
        version=version,
        allow_version_mismatch=False,
        allow_ci_failure=args.allow_ci_failure,
        dry_run=dry_run,
        collapse_rcs=False,
        earliest_version=f"v{version}",
        **_slack_flag_values(args),
    )
    # Prefer the release branch as target once it exists.
    if remote_branch_exists(xy) or not dry_run:
        draft_ns.target = xy if remote_branch_exists(xy) else "main"
    # If branch was just created in dry-run, still draft against main conceptually.
    if dry_run and not remote_branch_exists(xy):
        draft_ns.target = "main"

    # Override target SHA: draft-release uses origin/target HEAD; for RC we want candidate.
    # Temporarily draft by checking version match; if candidate != HEAD, warn.
    head_sha: str = run_out(["git", "rev-parse", f"origin/{draft_ns.target}"], cwd=REPO_ROOT)
    if head_sha != candidate and not dry_run:
        print(
            f"WARN: origin/{draft_ns.target} HEAD {head_sha[:12]} != candidate {candidate[:12]}; "
            "drafting at branch HEAD. Pass a branch tip that matches --candidate.",
            file=sys.stderr,
        )

    rc: int = cmd_draft_release(draft_ns)
    print_dogfood_checklist(version)
    print(f"waiting on: maintainer publish of v{version}; then dogfood + post-release")
    if rc == 0:
        raise WaitingOn(f"maintainer publish of draft v{version}")
    return rc


def cmd_final(args: argparse.Namespace) -> int:
    xy: str = args.xy
    dry_run: bool = args.dry_run
    if not re.fullmatch(r"\d+\.\d+", xy):
        raise SystemExit(f"final target must look like X.Y, got {xy!r}")
    ensure_gh_auth()
    final_ver: str = compute_final_version(xy)
    run(["git", "fetch", "origin", xy], cwd=REPO_ROOT)
    sha: str = run_out(["git", "rev-parse", f"origin/{xy}"], cwd=REPO_ROOT)
    current: str = read_pyproject_version_at(sha)

    # Find latest published RC for this line.
    rc_tags: list[tuple[Version, str]] = []
    for rel in list_published_release_tags():
        if rel.get("isDraft"):
            continue
        tag: str = rel.get("tagName") or ""
        try:
            ver: Version = Version(normalize_version_str(tag))
        except InvalidVersion:
            continue
        if ver.major == int(xy.split(".")[0]) and ver.minor == int(xy.split(".")[1]) and is_rc_version(str(ver)):
            rc_tags.append((ver, tag))
    if not rc_tags:
        raise SystemExit(f"no published RC tags found for {xy}")
    rc_tags.sort(key=lambda item: item[0], reverse=True)
    last_rc_tag: str = rc_tags[0][1]
    print(f"last RC: {last_rc_tag}")

    # Only change since last RC must be the set-version commit (or we're about to open it).
    if current != final_ver:
        # Diff last RC tag..HEAD should be only pyproject once bump lands; before bump, open it.
        bump_open: list[dict[str, Any]] = find_open_bump_prs(xy, final_ver)
        if bump_open:
            raise WaitingOn(f"merge of set-version PR {bump_open[0].get('url')}")
        # Pre-check: changes since last RC excluding pyproject must be empty after bump;
        # before bump, allow only nothing else pending? We open the bump and wait.
        title: str = f"chore: set version to {final_ver}"
        pr_url: Optional[str] = open_version_bump_pr(
            base_branch=xy,
            new_version=final_ver,
            title=title,
            dry_run=dry_run,
            body_extra="Final release set-version; must be the only change since the last RC.",
        )
        raise WaitingOn(f"merge of set-version PR to {final_ver}" + (f" ({pr_url})" if pr_url else ""))

    # Verify only pyproject changed since last RC.
    changed: str = run_out(
        ["git", "diff", "--name-only", f"{last_rc_tag}..origin/{xy}"],
        cwd=REPO_ROOT,
    )
    files: list[str] = [f for f in changed.splitlines() if f.strip()]
    non_version: list[str] = [f for f in files if f != "pyproject.toml"]
    if non_version:
        raise SystemExit(
            f"refusing final: changes other than pyproject.toml since {last_rc_tag}:\n  " + "\n  ".join(non_version)
        )

    ensure_ci_green(sha, allow_failure=args.allow_ci_failure)
    earliest: str = f"{xy}.0rc1"
    draft_ns: argparse.Namespace = argparse.Namespace(
        target=xy,
        version=final_ver,
        allow_version_mismatch=False,
        allow_ci_failure=args.allow_ci_failure,
        dry_run=dry_run,
        collapse_rcs=True,
        earliest_version=f"v{earliest}",
        **_slack_flag_values(args),
    )
    rc: int = cmd_draft_release(draft_ns)
    print(f"waiting on: maintainer publish of v{final_ver}; then post-release")
    if rc == 0:
        raise WaitingOn(f"maintainer publish of draft v{final_ver}")
    return rc


def pypi_has_version(version: str) -> bool:
    ver: str = normalize_version_str(version)
    url: str = f"https://pypi.org/pypi/ddtrace/{ver}/json"
    try:
        req: urllib.request.Request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=15) as resp:  # nosec B310 — fixed pypi.org URL
            return int(resp.status) == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise SystemExit(f"PyPI lookup failed for {ver}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"PyPI lookup failed for {ver}: {exc}") from exc


def cmd_post_release(args: argparse.Namespace) -> int:
    version: str = normalize_version_str(args.version)
    dry_run: bool = args.dry_run
    ensure_gh_auth()

    exists, is_draft, url = release_exists_for_tag(version)
    if not exists:
        raise WaitingOn(f"GitHub release v{version} does not exist yet")
    if is_draft:
        raise WaitingOn(f"GitHub release v{version} is still a draft ({url})")
    print(f"GitHub release published: {url}")

    if not pypi_has_version(version):
        raise WaitingOn(
            f"PyPI does not list ddtrace=={version} yet "
            f"(https://pypi.org/pypi/ddtrace/{version}/json) — watch the GitLab tag pipeline"
        )
    print(f"PyPI has ddtrace=={version}")
    print_gitlab_watch_list(version)

    v: Version = Version(version)
    if is_rc_version(version) and v.pre is not None and v.pre[1] == 1:
        # First RC of a minor: bump main to next minor rc1 only after a *final*?
        # Plan: "For a new minor, it bumps main to X.(Y+1).0rc1" after post-release of final.
        # For rc1 cut: "rc1 cut: main to next minor rc1" — reading plan again:
        # "Opens the next bump PR: X.Y.(Z+1) for a patch or final. For a new minor, it bumps main to X.(Y+1).0rc1"
        # And post todo: "patch: X.Y.(Z+1); rc1 cut: main to next minor rc1"
        # So after publishing first RC of a line... actually "rc1 cut" means after cutting/publishing
        # the branch's first RC setup? The post-release for an RC typically bumps main to next rc.
        # Looking at contributing-release: after RC, bump main to next RC.
        # After final (.0), bump main to next minor rc1 AND bump release branch to .1.
        # Stable patch/final -> bump X.Y to Z+1; final (.0) also bumps main to next minor rc1.
        # RC -> bump main to next rc (existing bump-rc behavior).
        pass

    if is_rc_version(version):
        # Open bump-rc on main to next rc.
        next_ver: str = compute_next_rc(version)
        open_bps: list[dict[str, Any]] = find_open_bump_prs("main", next_ver)
        if open_bps:
            print(f"bump PR already open: {open_bps[0].get('url')}")
            return 0
        title: str = f"chore: bump {next_ver}"
        pr_url: Optional[str] = open_version_bump_pr(
            base_branch="main",
            new_version=next_ver,
            title=title,
            dry_run=dry_run,
        )
        print(f"next: merge bump to {next_ver}" + (f" ({pr_url})" if pr_url else ""))
        return 0

    # Stable: bump X.Y branch to Z+1; if micro==0 also bump main to next minor rc1.
    xy: str = f"{v.major}.{v.minor}"
    next_patch: str = compute_next_patch(version)
    bump_open: list[dict[str, Any]] = find_open_bump_prs(xy, next_patch)
    if bump_open:
        print(f"patch bump PR already open: {bump_open[0].get('url')}")
    else:
        pr_url = open_version_bump_pr(
            base_branch=xy,
            new_version=next_patch,
            title=f"chore: bump version to {next_patch}",
            dry_run=dry_run,
        )
        print(f"opened/would open bump to {next_patch}" + (f" ({pr_url})" if pr_url else ""))

    if v.micro == 0:
        next_main: str = compute_next_minor_rc1(version)
        main_open: list[dict[str, Any]] = find_open_bump_prs("main", next_main)
        if main_open:
            print(f"main bump PR already open: {main_open[0].get('url')}")
        else:
            main_url: Optional[str] = open_version_bump_pr(
                base_branch="main",
                new_version=next_main,
                title=f"chore: bump {next_main}",
                dry_run=dry_run,
            )
            print(f"opened/would open main bump to {next_main}" + (f" ({main_url})" if main_url else ""))

    print("next (later phases, not implemented here): dogfood, then experiment")
    return 0


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #


def slack_mention(env_var: str, default_user_id: str) -> str:
    """Build a Slack ``<@ID>`` mention; env override wins over hardcoded default."""
    override: str = os.environ.get(env_var, "").strip()
    user_id: str = override or default_user_id
    return f"<@{user_id}>"


def _slack_flag_values(args: argparse.Namespace) -> dict[str, Any]:
    """Slack-related Namespace fields for draft_release / notify_slack callers."""
    return {
        "slack": bool(getattr(args, "slack", False)),
        "slack_channel": str(getattr(args, "slack_channel", "guild")),
        "slack_precheck": bool(getattr(args, "slack_precheck", True)),
        "test_dm": bool(getattr(args, "test_dm", False)),
    }


def format_slack_announce(version: str, target_sha: str, draft_url: str) -> str:
    """Build the Slack mrkdwn body for a draft-ready review ping."""
    ver: str = normalize_version_str(version)
    kind: str = draft_kind_phrase(ver)
    brett: str = slack_mention(SLACK_USER_BRETT_ENV, SLACK_USER_BRETT_ID)
    emmett: str = slack_mention(SLACK_USER_EMMETT_ENV, SLACK_USER_EMMETT_ID)
    return (
        f":package: *Draft {kind}* `{ver}` is ready for review\n"
        f"\n"
        f"<{draft_url}|Open draft release / notes>  ·  target `{target_sha}`\n"
        f"\n"
        f"{brett} {emmett} — can I get a :lgtm:? Please review and "
        f"*Publish release* when ready (needs repo-owner perms; the tag is created at publish).\n"
        f"\n"
        f"{AUTO_CREATED_FOOTER}"
    )


def post_webhook(url: str, text: str) -> None:
    payload: bytes = json.dumps({"text": text}).encode("utf-8")
    req: urllib.request.Request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — url is a Slack hooks.slack.com URL from env, not user input
        body: str = resp.read().decode("utf-8", errors="replace").strip()
        # Classic incoming webhooks return "ok"; Workflow Builder webhooks return
        # JSON like {"ok":true,...}. Accept both; treat anything else as a failure.
        ok: bool = body.lower() == "ok"
        if not ok:
            try:
                ok = bool(json.loads(body).get("ok"))
            except (ValueError, AttributeError):
                ok = False
        if resp.status >= 300 or not ok:
            raise SystemExit(f"slack webhook returned {resp.status}: {body}")


def _env_url(*names: str) -> tuple[str, str]:
    """Return ``(url, env_name)`` for the first non-empty env among ``names``.

    ``names[0]`` is the preferred name used in docs/errors when none are set.
    """
    preferred: str = names[0]
    for name in names:
        value: str = os.environ.get(name, "").strip()
        if value:
            return value, name
    return "", preferred


def resolve_channel_webhook(channel: str) -> tuple[str, str]:
    """Return (url, source) for the requested channel.

    Resolution order:
      1. The corresponding env var, if set (allows override and offline use).
      2. AWS SSM Parameter Store at the canonical path for that channel.

    ``channel`` is one of "guild", "test", "prod", or "auto". "auto" only
    consults env vars (guild → prod → test → legacy) and refuses to silently
    hit SSM — picking a specific channel for SSM should be explicit.
    """
    guild_url: str
    guild_env: str
    guild_url, guild_env = _env_url(SLACK_CHANNEL_GUILD_ENV, SLACK_CHANNEL_GUILD_ENV_LEGACY)
    test_url: str = os.environ.get(SLACK_CHANNEL_TEST_ENV, "").strip()
    prod_url: str = os.environ.get(SLACK_CHANNEL_PROD_ENV, "").strip()
    legacy_url: str = os.environ.get(SLACK_CHANNEL_ENV, "").strip()

    if channel == "guild":
        if guild_url:
            return guild_url, f"{guild_env} env var"
        try:
            return (
                fetch_ssm_parameter(SSM_SLACK_WEBHOOK_GUILD),
                f"AWS SSM ({SSM_SLACK_WEBHOOK_GUILD})",
            )
        except SystemExit as exc:
            raise SystemExit(
                f"--slack-channel=guild needs {SLACK_CHANNEL_GUILD_ENV} "
                f"(or legacy {SLACK_CHANNEL_GUILD_ENV_LEGACY}) or SSM "
                f"{SSM_SLACK_WEBHOOK_GUILD} (webhook posting to "
                f"#{SLACK_CHANNEL_GUILD_NAME} / <#{SLACK_CHANNEL_GUILD_ID}>). "
                "Create a Workflow Builder webhook with a `text` variable bound "
                f"to #{SLACK_CHANNEL_GUILD_NAME}, then export the URL or store it "
                f"in SSM. Underlying error: {exc}"
            ) from exc
    if channel == "test":
        if test_url:
            return test_url, f"{SLACK_CHANNEL_TEST_ENV} env var"
        return fetch_ssm_parameter(SSM_SLACK_WEBHOOK_TEST), f"AWS SSM ({SSM_SLACK_WEBHOOK_TEST})"
    if channel == "prod":
        if prod_url:
            return prod_url, f"{SLACK_CHANNEL_PROD_ENV} env var"
        return fetch_ssm_parameter(SSM_SLACK_WEBHOOK_PROD), f"AWS SSM ({SSM_SLACK_WEBHOOK_PROD})"
    # auto: env vars only; require explicit --slack-channel for SSM.
    if guild_url:
        return guild_url, f"{guild_env} env var"
    if prod_url:
        return prod_url, f"{SLACK_CHANNEL_PROD_ENV} env var"
    if test_url:
        return test_url, f"{SLACK_CHANNEL_TEST_ENV} env var"
    if legacy_url:
        return legacy_url, f"{SLACK_CHANNEL_ENV} env var"
    raise SystemExit(
        "--slack with --slack-channel=auto requires one of "
        f"{SLACK_CHANNEL_GUILD_ENV}, {SLACK_CHANNEL_PROD_ENV}, "
        f"{SLACK_CHANNEL_TEST_ENV}, or {SLACK_CHANNEL_ENV} to be set. "
        "Pass --slack-channel=guild|test|prod to fetch from AWS SSM instead "
        f"(guild default for review pings → #{SLACK_CHANNEL_GUILD_NAME})."
    )


def notify_slack(
    version: str, target_sha: str, draft_url: str, test_dm: bool, channel: str, precheck: bool = True
) -> None:
    text: str = format_slack_announce(version, target_sha, draft_url)
    dm_url: str
    dm_env: str
    dm_url, dm_env = _env_url(SLACK_DM_ENV, SLACK_DM_ENV_LEGACY)

    if test_dm:
        if not dm_url:
            raise SystemExit(
                f"--test-dm requires {SLACK_DM_ENV} to be set "
                f"(or legacy {SLACK_DM_ENV_LEGACY}; secret DM webhook; "
                "not committed — export it locally or add an SSM path later)"
            )
        if precheck:
            post_webhook(dm_url, "[ping] release-script connectivity check (DM)")
        post_webhook(dm_url, f"[TEST] {text}")
        print(f"sent test DM via {dm_env}")
        return

    channel_url: str
    channel_env: str
    channel_url, channel_env = resolve_channel_webhook(channel)
    if precheck:
        # Sentinel post: catches misconfigured Workflow Builder webhooks (renamed
        # `text` variable, deleted workflow, revoked URL) before we send the real
        # announcement that's harder to retract.
        post_webhook(channel_url, "[ping] release-script connectivity check")
    post_webhook(channel_url, text)
    print(f"posted to release channel via {channel_env}")
    if dm_url:
        post_webhook(dm_url, f"(runner copy) {text}")
        print(f"sent runner-copy DM via {dm_env}")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def _add_common_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--verbose",
        action="store_true",
        help="echo stderr from external tools (reno, pandoc, gh) and print tool versions at start",
    )


def _add_slack_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--slack",
        action="store_true",
        help=(
            f"post a review ping to the channel selected by --slack-channel "
            f"(default: guild → #{SLACK_CHANNEL_GUILD_NAME})"
        ),
    )
    sp.add_argument(
        "--slack-channel",
        choices=["guild", "test", "prod", "auto"],
        default="guild",
        help=(
            "which channel webhook to use: guild reads "
            f"{SLACK_CHANNEL_GUILD_ENV} (#{SLACK_CHANNEL_GUILD_NAME}), "
            f"test reads {SLACK_CHANNEL_TEST_ENV}, prod reads {SLACK_CHANNEL_PROD_ENV} "
            f"(#apm-python-release), auto prefers guild then prod then test then "
            f"the legacy {SLACK_CHANNEL_ENV} (default: guild)"
        ),
    )
    sp.add_argument(
        "--no-slack-precheck",
        dest="slack_precheck",
        action="store_false",
        default=True,
        help=(
            "skip the connectivity-check ping that's sent to Slack before the "
            "real announcement (default: precheck enabled)"
        ),
    )
    sp.add_argument("--test-dm", action="store_true", help="DM the runner only (no channel post)")


def build_parser() -> argparse.ArgumentParser:
    p: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="create_gh_release.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub: Any = p.add_subparsers(dest="cmd", required=True)

    patch_p: argparse.ArgumentParser = sub.add_parser(
        "patch", help="resumable: backport a merged fix into a patch release draft"
    )
    patch_p.add_argument("pr", help="fix PR number on main")
    patch_p.add_argument("--branch", help="release branch X.Y (default: sole backport X.Y label on the PR)")
    patch_p.add_argument("--allow-ci-failure", action="store_true")
    patch_p.add_argument("--dry-run", action="store_true")
    _add_slack_flags(patch_p)
    _add_common_flags(patch_p)
    patch_p.set_defaults(func=cmd_patch)

    rc_p: argparse.ArgumentParser = sub.add_parser("rc", help="resumable: cut the next RC draft")
    rc_p.add_argument("--candidate", help="SHA to release (default: origin/main HEAD)")
    rc_p.add_argument("--allow-ci-failure", action="store_true")
    rc_p.add_argument("--dry-run", action="store_true")
    _add_slack_flags(rc_p)
    _add_common_flags(rc_p)
    rc_p.set_defaults(func=cmd_rc)

    final_p: argparse.ArgumentParser = sub.add_parser("final", help="resumable: ship X.Y.0 from the last RC")
    final_p.add_argument("xy", help="release line like 4.17")
    final_p.add_argument("--allow-ci-failure", action="store_true")
    final_p.add_argument("--dry-run", action="store_true")
    _add_slack_flags(final_p)
    _add_common_flags(final_p)
    final_p.set_defaults(func=cmd_final)

    post_p: argparse.ArgumentParser = sub.add_parser(
        "post-release", help="after publish: verify PyPI + open the next bump PR"
    )
    post_p.add_argument("version", help="published version (vX.Y.Z or X.Y.Z)")
    post_p.add_argument("--dry-run", action="store_true")
    _add_common_flags(post_p)
    post_p.set_defaults(func=cmd_post_release)

    bp: argparse.ArgumentParser = sub.add_parser("bump-rc", help="open the rc(N+1) bump PR")
    bp.add_argument("--base-branch", default="main", help="base branch to PR against (default: main)")
    bp.add_argument("--dry-run", action="store_true")
    _add_common_flags(bp)
    bp.set_defaults(func=cmd_bump_rc)

    eb: argparse.ArgumentParser = sub.add_parser("ensure-branch", help="create X.Y release branch if missing")
    eb.add_argument("target", help="branch name like 4.9")
    eb.add_argument("--from", dest="from_branch", help="prior X.Y branch to fork from (default: highest below target)")
    eb.add_argument(
        "--check-empty-diff",
        action="store_true",
        help="when the branch already exists, require empty diff vs origin/main",
    )
    eb.add_argument("--dry-run", action="store_true")
    _add_common_flags(eb)
    eb.set_defaults(func=cmd_ensure_branch)

    dr: argparse.ArgumentParser = sub.add_parser(
        "draft-release", help="create a draft GitHub release with formatted reno notes"
    )
    dr.add_argument("--target", default="main", help="branch whose HEAD is the release target (default: main)")
    dr.add_argument("--version", help="explicit version (default: read from pyproject at target)")
    dr.add_argument(
        "--earliest-version",
        dest="earliest_version",
        help="passed to format_release_notes.py (default: v<version>)",
    )
    dr.add_argument(
        "--collapse-rcs",
        action="store_true",
        help="pass --collapse-release-candidates to format_release_notes.py (finals)",
    )
    dr.add_argument(
        "--allow-version-mismatch",
        action="store_true",
        help=(
            "skip the pre-flight that refuses when --version disagrees with the "
            "pyproject version at --target. Use only when intentionally cutting "
            "a release without bumping pyproject first."
        ),
    )
    dr.add_argument(
        "--allow-ci-failure",
        action="store_true",
        help=(
            "downgrade the CI-green pre-flight to a warning. Use sparingly; the "
            "default behavior refuses to draft on a commit with red or pending CI."
        ),
    )
    _add_slack_flags(dr)
    dr.add_argument("--dry-run", action="store_true")
    _add_common_flags(dr)
    dr.set_defaults(func=cmd_draft_release)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    global _VERBOSE
    parser: argparse.ArgumentParser = build_parser()
    args: argparse.Namespace = parser.parse_args(argv)
    _VERBOSE = bool(getattr(args, "verbose", False))
    if _VERBOSE:
        print_tool_versions()
    try:
        return int(args.func(args))
    except WaitingOn as gate:
        print(f"\nWAITING ON: {gate.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
