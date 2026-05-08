#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging"]
# ///
"""Automate dd-trace-py release-candidate steps.

Subcommands:
  bump-rc        Open a PR on `main` (or another base) bumping pyproject.toml
                 from rcN to rc(N+1).
  ensure-branch  Create the X.Y release branch if (and only if) it doesn't
                 already exist on origin, using the merge -Xtheirs recipe
                 from docs/contributing-release.rst.
  draft-release  Generate release notes via reno+pandoc, prepend the EOL
                 block, and create a DRAFT GitHub release with --prerelease.
                 Optionally posts a Slack announcement.

See docs/contributing-release.rst for the manual flow this script automates.
The script never publishes a GitHub release; --draft is non-negotiable.

Usage:
  scripts/gh/create_gh_release_rc.py bump-rc       [--base-branch main] [--dry-run] [--verbose]
  scripts/gh/create_gh_release_rc.py ensure-branch X.Y [--from A.B] [--dry-run] [--verbose]
  scripts/gh/create_gh_release_rc.py draft-release [--target main] [--version 4.9.0rc3]
                                   [--allow-version-mismatch] [--allow-ci-failure]
                                   [--slack] [--slack-channel test|prod|auto]
                                   [--no-slack-precheck] [--test-dm]
                                   [--dry-run] [--verbose]

Slack env vars (set whichever you'll use):
  DD_RELEASE_SLACK_WEBHOOK_URL_PROD  webhook bound to #apm-python-release
  DD_RELEASE_SLACK_WEBHOOK_URL_TEST  webhook bound to your test channel
  DD_RELEASE_SLACK_WEBHOOK_URL       legacy single-channel fallback
  DD_RELEASE_SLACK_DM_URL            optional, used by --test-dm
  DD_SLACK_USER_BRETT/EMMETT         Slack member IDs for real @-mentions

If the channel env var is unset and you pass --slack-channel=test|prod, the
script falls back to AWS SSM Parameter Store at:
  ci.dd-trace-py.slack-release-webhook-prod
  ci.dd-trace-py.slack-release-webhook-test
This matches the ``ci.dd-trace-py.<key>`` convention used elsewhere in this
repo for release secrets (see .gitlab/release.yml). Requires AWS credentials
to be available (e.g. via aws-vault or your usual SSO flow).

Failure recovery:
  Push succeeded but `gh pr create` failed (bump-rc):
      git push origin --delete bump-version/<version>   # then re-run bump-rc

  Draft release created but Slack post failed (draft-release):
      The draft is still on GitHub; just re-run with --slack to retry the
      announcement, or post manually.

  ensure-branch checked out a local branch but `git push` failed:
      git checkout main && git branch -D <X.Y>   # then re-run ensure-branch

Gotchas:
  - Slack Workflow Builder webhook with renamed `text` variable: the post
    succeeds at the HTTP layer but the message body comes through empty
    or templated. The --slack-precheck handshake (default: on) catches
    most cases by sending a sentinel first.
  - DD_SLACK_USER_BRETT/EMMETT with rotated/stale IDs render as broken
    mentions like `<@U_OLD>` with no error. Refresh from Slack profile
    when reviewers change.
  - Requires `gh` CLI 2.x for `--jq` support and `aws` CLI for SSM lookup.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Optional
import urllib.request

from packaging.version import Version
import tomllib


# `parents[2]` because this script lives at scripts/gh/<file>.py — three levels
# below the repo root. If the script is moved, update this index. The assert
# below catches mismatches early so a path bug fails immediately rather than
# producing confusing "no such file" errors later in the flow.
REPO_ROOT: Path = Path(__file__).resolve().parents[2]
PYPROJECT: Path = REPO_ROOT / "pyproject.toml"
assert PYPROJECT.is_file(), f"REPO_ROOT misconfigured: {PYPROJECT} does not exist"

# Slack webhook env vars. The script supports two channel webhooks (test +
# prod) and one optional DM webhook. ``--slack-channel`` selects which channel
# webhook is used; ``DD_RELEASE_SLACK_WEBHOOK_URL`` is the legacy fallback that
# is read when neither TEST nor PROD is set, so existing setups keep working.
SLACK_CHANNEL_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL"
SLACK_CHANNEL_TEST_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL_TEST"
SLACK_CHANNEL_PROD_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL_PROD"
SLACK_DM_ENV: str = "DD_RELEASE_SLACK_DM_URL"
SLACK_USER_BRETT_ENV: str = "DD_SLACK_USER_BRETT"
SLACK_USER_EMMETT_ENV: str = "DD_SLACK_USER_EMMETT"

# AWS SSM Parameter Store paths. Canonical storage for shared release secrets
# in this repo — see .gitlab/release.yml and scripts/upload-debug-symbols-to-
# backend.sh for the same `ci.dd-trace-py.<key>` convention. The script falls
# back to SSM when the corresponding env var is unset, so a freshly-set-up
# release engineer with AWS creds (and no other config) can run the script
# without manually exporting webhook URLs.
SSM_REGION: str = "us-east-1"
SSM_SLACK_WEBHOOK_PROD: str = "ci.dd-trace-py.slack-release-webhook-prod"
SSM_SLACK_WEBHOOK_TEST: str = "ci.dd-trace-py.slack-release-webhook-test"

# Required flags on every `gh release create` invocation. The script's only
# safety contract with the runner is "this never publishes" — these flags are
# what enforce that contract. ``cmd_draft_release`` asserts both are present in
# the args list before invoking gh, and a unit test pins this down.
_GH_RELEASE_REQUIRED_FLAGS: tuple[str, ...] = ("--draft", "--prerelease")

# Hardcoded fallback for the canonical release-note section order. Mirrors
# ``sections`` in releasenotes/config.yaml. We prefer to parse that YAML at
# runtime (see ``load_section_order``) so the team can add new sections without
# touching this file; this list is the safety net if the YAML is missing or
# malformed.
_FALLBACK_SECTION_ORDER: list[str] = [
    "Upgrade Notes",
    "Deprecation Notes",
    "New Features",
    "Known Issues",
    "API Changes",
    "Security Issues",
    "Bug Fixes",
    "Other Changes",
]

# When True, ``run_external_with_stderr`` echoes stderr from reno/pandoc/etc.
# to the user's terminal. Set by ``main`` from ``--verbose``.
_VERBOSE: bool = False


def run(
    cmd: list[str], check: bool = True, capture: bool = False, cwd: Optional[Path] = None
) -> subprocess.CompletedProcess:
    """Run a subprocess, echoing the command for transparency."""
    print(f"$ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(
        cmd,
        check=check,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
    )


def run_out(cmd: list[str], cwd: Optional[Path] = None) -> str:
    """Run a subprocess and return stdout (stripped)."""
    cp = run(cmd, capture=True, cwd=cwd)
    return cp.stdout.strip()


def read_pyproject_version(source: Path = PYPROJECT) -> str:
    with open(source, "rb") as f:
        data: dict = tomllib.load(f)
    return data["project"]["version"]


def read_pyproject_version_at(ref: str) -> str:
    """Read the pyproject.toml [project.version] at a given git ref."""
    blob: str = run_out(["git", "show", f"{ref}:pyproject.toml"], cwd=REPO_ROOT)
    data: dict = tomllib.loads(blob)
    return data["project"]["version"]


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


def ensure_clean_tree() -> None:
    # Untracked files are fine; only tracked-file modifications block branch
    # operations. We stage only pyproject.toml later, so other dirty tracked
    # files would leak into the bump commit.
    out: str = run_out(["git", "status", "--porcelain", "--untracked-files=no"], cwd=REPO_ROOT)
    if out:
        raise SystemExit(f"working tree has uncommitted changes:\n{out}\nstash or commit before running")


def ensure_gh_auth() -> None:
    cp: subprocess.CompletedProcess = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if cp.returncode != 0:
        raise SystemExit("gh CLI is not authenticated. Run `gh auth login` first.")


def cmd_exists(name: str) -> bool:
    return shutil.which(name) is not None


def load_section_order(config_path: Optional[Path] = None) -> list[str]:
    """Parse the canonical section order from ``releasenotes/config.yaml``.

    Looks for entries shaped ``- [<key>, <Display Name>]`` in the ``sections:``
    block. We avoid a YAML dependency by using a small regex parser; the file's
    relevant portion is shaped uniformly enough that this is robust. Falls back
    to ``_FALLBACK_SECTION_ORDER`` if the file is missing or contains no
    parseable section entries.
    """
    if config_path is None:
        config_path = REPO_ROOT / "releasenotes" / "config.yaml"
    try:
        text: str = config_path.read_text()
    except OSError:
        return list(_FALLBACK_SECTION_ORDER)
    # Limit parsing to the ``sections:`` block (until the next top-level key,
    # which is any line that starts at column 0 and isn't a comment). The block
    # is small (~10 entries) so the windowed regex is plenty.
    in_block: bool = False
    names: list[str] = []
    pattern: re.Pattern[str] = re.compile(r"^\s*-\s*\[\s*[^,\]]+\s*,\s*(.+?)\s*\]\s*$")
    for line in text.splitlines():
        if not in_block:
            if line.strip().startswith("sections:"):
                in_block = True
            continue
        # End of block: a non-blank line that doesn't start with whitespace
        # and isn't a comment.
        if line and not line[0].isspace() and not line.lstrip().startswith("#"):
            break
        m: Optional[re.Match[str]] = pattern.match(line)
        if m:
            names.append(m.group(1).strip())
    return names if names else list(_FALLBACK_SECTION_ORDER)


def run_external_with_stderr(cmd: list[str], stdin_text: Optional[str] = None) -> subprocess.CompletedProcess:
    """Like ``subprocess.run(check=True)`` but optionally echoes stderr.

    Used for reno/pandoc/aws calls where warnings ("reference not found",
    rate-limit notices) often hide useful diagnostic info. With ``--verbose``,
    the stderr is forwarded to the user's terminal in real time; otherwise it's
    captured and only surfaced on failure.
    """
    cp: subprocess.CompletedProcess = subprocess.run(
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
            cp: subprocess.CompletedProcess = subprocess.run(
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
    cp: subprocess.CompletedProcess = subprocess.run(
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
    cp: subprocess.CompletedProcess = subprocess.run(
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
    return cp.stdout.strip()


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
            s: dict = json.loads(line)
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
            run_obj: dict = json.loads(line)
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
    cp: subprocess.CompletedProcess = subprocess.run(
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
    return cp.stdout.strip().lower() == "false"


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
    local_check: subprocess.CompletedProcess = subprocess.run(
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
        f"Generated by `scripts/gh/create_gh_release_rc.py bump-rc`.\n"
        f"Once merged, run `scripts/gh/create_gh_release_rc.py draft-release` to create the GitHub draft."
    )
    try:
        pr_url: str = run_out(
            [
                "gh",
                "pr",
                "create",
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


def generate_unreleased_notes(target: str, target_version: str) -> str:
    """Run reno+pandoc and return all notes pertaining to ``target_version``.

    Reno groups release notes by the pyproject.toml version at each commit. After
    a bump-rc PR lands, reno splits the upcoming rc's notes into two sections:
    ``## Unreleased`` (notes added in commits where pyproject was already on the
    new rc) and ``## v<target_version>`` (notes added in commits where pyproject
    still showed the previous rc but which logically belong to this rc cycle).
    Both sections belong to the upcoming release, so we concatenate everything
    from the first ``## `` heading until we hit a versioned heading whose tag
    is *not* the target — that boundary marks the start of an already-published
    version and is where we stop.
    """
    if not cmd_exists("reno"):
        raise SystemExit("reno not found on PATH; run via `hatch run` or activate the venv")
    if not cmd_exists("pandoc"):
        raise SystemExit("pandoc not found on PATH")

    reno_out: str = run_external_with_stderr(["reno", "report", f"--branch=origin/{target}"]).stdout
    pandoc: subprocess.CompletedProcess = run_external_with_stderr(
        ["pandoc", "-f", "rst", "-t", "gfm", "--wrap=none"], stdin_text=reno_out
    )
    return slice_and_merge_notes(pandoc.stdout, target_version)


def slice_and_merge_notes(md: str, target_version: str, section_order: Optional[list[str]] = None) -> str:
    """Pure-function version of the slice + merge logic for unit testing.

    Takes pandoc gfm output and a target version, returns the body that should
    appear in the GitHub release. Splits at ``## `` boundaries, takes the slice
    that belongs to ``target_version``, strips the redundant in-body ``## v<...>``
    heading, and merges duplicate subsections via ``_merge_subsections``.

    ``section_order`` defaults to whatever ``load_section_order`` reads from
    ``releasenotes/config.yaml``; tests can override it for determinism.
    """
    if section_order is None:
        section_order = load_section_order()
    md = _strip_code_fenced_lines(md)
    headings: list[re.Match[str]] = list(re.finditer(r"^## (.+?)\s*$", md, re.MULTILINE))
    if not headings:
        raise SystemExit(
            "no version sections found in reno output — there are no release notes "
            "between the previous tag and the target. If this is intentional (e.g. "
            "a metadata-only rc bump), pass an empty body manually via the GitHub "
            "UI; otherwise add release-note yaml files under releasenotes/notes/."
        )

    start: int = headings[0].end()
    end: int = len(md)
    target_tag: str = f"v{target_version}"
    # Match ``v<target>`` exactly OR ``v<target>-<n>`` for reno's sub-cycle
    # markers (e.g. ``v4.9.0rc2-2``). Both belong to the target rc and must
    # NOT be treated as a stop boundary.
    target_or_subcycle: re.Pattern[str] = re.compile(rf"^{re.escape(target_tag)}(-\d+)?$")
    for h in headings[1:]:
        title: str = h.group(1).strip()
        # A versioned heading that doesn't match target_version (or its
        # sub-cycle marker) means we've walked past this rc into
        # prior-published territory; stop.
        if title.startswith("v") and not target_or_subcycle.match(title):
            end = h.start()
            break
    body: str = md[start:end]
    # Strip the in-body "## v<target>" heading and any "## v<target>-<n>"
    # sub-cycle markers that reno may emit; the whole body is for this rc, so
    # these headings are redundant noise.
    body = re.sub(rf"^## {re.escape(target_tag)}(-\d+)?\s*\n+", "", body, flags=re.MULTILINE)
    return _merge_subsections(body.strip(), section_order)


def _strip_code_fenced_lines(md: str) -> str:
    """Replace lines inside fenced code blocks with empty placeholders.

    Pandoc gfm output uses ``` fences; release notes can contain code samples
    that begin with ``## ``. We must NOT treat those as section headings during
    slicing. Strips fenced regions to empty lines (preserves line numbers /
    file shape so error messages line up if we ever surface them).
    """
    out: list[str] = []
    in_fence: bool = False
    for line in md.splitlines(keepends=True):
        stripped: str = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            out.append(line)
            continue
        out.append("\n" if in_fence else line)
    return "".join(out)


def _merge_subsections(body: str, section_order: Optional[list[str]] = None) -> str:
    """Dedupe and reorder ``### `` subsections.

    When reno's pre-release collapse produces split versions (one ``## Unreleased``
    plus one ``## v<target>``), our slicer concatenates the two regions, which
    can leave duplicate subsection headings — e.g. two ``### Bug Fixes`` blocks
    interleaved with other subsections. This function groups items by subsection
    heading and emits them in the canonical order from ``releasenotes/config.yaml``.

    ``section_order`` defaults to ``_FALLBACK_SECTION_ORDER`` when called
    directly (e.g. from tests); ``slice_and_merge_notes`` passes the live order
    loaded from the YAML.
    """
    if section_order is None:
        section_order = _FALLBACK_SECTION_ORDER
    parts: list[str] = re.split(r"^### (.+?)\s*$", body, flags=re.MULTILINE)
    if len(parts) < 3:
        return body  # no subsections; nothing to merge

    preamble: str = parts[0].strip()
    items_by_section: dict[str, list[str]] = {}
    for i in range(1, len(parts), 2):
        heading: str = parts[i].strip()
        content: str = parts[i + 1].strip() if i + 1 < len(parts) else ""
        if content:
            items_by_section.setdefault(heading, []).append(content)

    chunks: list[str] = [preamble] if preamble else []
    seen: set[str] = set()
    for name in section_order:
        if name in items_by_section:
            seen.add(name)
            chunks.append(f"### {name}\n\n" + "\n\n".join(items_by_section[name]))
    for name, items in items_by_section.items():
        if name not in seen:
            chunks.append(f"### {name}\n\n" + "\n\n".join(items))
    return "\n\n".join(chunks)


def check_existing_release(version: str, dry_run: bool = False) -> None:
    """Refuse to draft if a release with this tag is already published.

    GitHub allows multiple drafts to share a (future) tag, but disallows two
    *published* releases sharing one. If a draft already exists, warn so the
    runner notices instead of silently piling up phantom drafts.
    """
    out: str = run_out(
        [
            "gh",
            "api",
            "repos/DataDog/dd-trace-py/releases",
            "--paginate",
            "--jq",
            f'.[] | select(.tag_name == "v{version}") | {{id, draft, html_url}}',
        ],
        cwd=REPO_ROOT,
    )
    if not out.strip():
        return
    # The query may return multiple JSON objects (one per matching release);
    # iterate over each line.
    published: list[dict] = []
    drafts: list[dict] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r: dict = json.loads(line)
        except ValueError:
            continue
        (drafts if r.get("draft") else published).append(r)
    if published:
        urls: str = ", ".join(r["html_url"] for r in published)
        raise SystemExit(
            f"a release tagged v{version} is already PUBLISHED ({urls}). "
            "Refusing to create another draft for the same tag."
        )
    if drafts and not dry_run:
        draft_urls: str = "\n  ".join(r["html_url"] for r in drafts)
        print(
            f"WARN: {len(drafts)} draft(s) already exist for v{version}:\n  {draft_urls}\n"
            "Creating another draft will leave duplicates. Continuing in 3s "
            "(Ctrl-C to abort)..."
        )
        import time

        time.sleep(3)


def find_previous_rc_eol_block(version: str) -> Optional[str]:
    """Look up the prior rcN-1 (or earlier rc on the same major.minor) and return its
    'Estimated end-of-life date,...' paragraph.
    """
    v: Version = Version(version)
    major: int = v.major
    minor: int = v.minor
    out: str = run_out(
        ["gh", "release", "list", "--limit", "100", "--json", "tagName,isPrerelease"],
        cwd=REPO_ROOT,
    )
    releases: list[dict] = json.loads(out)
    pat: re.Pattern[str] = re.compile(rf"^v{major}\.{minor}\.\d+rc\d+$")
    candidates: list[str] = [r["tagName"] for r in releases if pat.match(r["tagName"])]

    # Sort by rcN descending; same major.minor.
    def rc_key(tag: str) -> int:
        m: Optional[re.Match[str]] = re.search(r"rc(\d+)$", tag)
        return int(m.group(1)) if m else -1

    candidates.sort(key=rc_key, reverse=True)
    for tag in candidates:
        if tag == f"v{version}":
            continue  # skip the version we're about to draft
        body: str = run_out(
            ["gh", "release", "view", tag, "--json", "body", "--jq", ".body"],
            cwd=REPO_ROOT,
        )
        for line in body.splitlines():
            if line.strip().startswith("Estimated end-of-life date"):
                return line.strip()
    return None


def compose_release_body(version: str, notes: str, eol_block: Optional[str]) -> str:
    if eol_block is None:
        # Templated fallback: month-of-now + 18 months. Doc says "accurate to within
        # three months", so a templated value with a TODO is acceptable for review.
        eol_block = (
            "Estimated end-of-life date, accurate to within three months: TBD-YYYY\n"
            "See [the support level definitions]"
            "(https://docs.datadoghq.com/tracing/trace_collection/compatibility/python/#releases) "
            "for more information."
        )
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

    check_existing_release(version, dry_run=dry_run)
    ensure_ci_green(target_sha, allow_failure=args.allow_ci_failure)

    notes: str = generate_unreleased_notes(target, version)
    eol_block: Optional[str] = find_previous_rc_eol_block(version)
    body: str = compose_release_body(version, notes, eol_block)

    if dry_run:
        print("\n----- DRAFT RELEASE BODY -----")
        print(body)
        print("----- END BODY -----")
        print("\n(dry-run) would: gh release create v{0} --draft --prerelease --target {1}".format(version, target_sha))
        return 0

    notes_path: str
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(body)
        notes_path = f.name

    gh_release_args: list[str] = [
        "gh",
        "release",
        "create",
        f"v{version}",
        "--draft",
        "--prerelease",
        "--target",
        target_sha,
        "--title",
        version,
        "--notes-file",
        notes_path,
    ]
    # Never-publish contract: assert the safety flags are present before we
    # invoke gh. A future refactor that drops one of these would otherwise
    # silently start publishing real releases on first run.
    missing: list[str] = [f for f in _GH_RELEASE_REQUIRED_FLAGS if f not in gh_release_args]
    assert not missing, f"gh release create args missing required flags: {missing}"

    try:
        url: str = run_out(gh_release_args, cwd=REPO_ROOT)
    finally:
        os.unlink(notes_path)

    print(f"\nDraft release: {url}")

    if args.slack or args.test_dm:
        notify_slack(
            version=version,
            target_sha=short_sha,
            draft_url=url,
            test_dm=args.test_dm,
            channel=args.slack_channel,
            precheck=args.slack_precheck,
        )
    return 0


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #


def slack_mention(env_var: str, fallback: str) -> str:
    user_id: str = os.environ.get(env_var, "").strip()
    return f"<@{user_id}>" if user_id else fallback


def post_webhook(url: str, text: str) -> None:
    payload: bytes = json.dumps({"text": text}).encode("utf-8")
    req: urllib.request.Request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
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


def resolve_channel_webhook(channel: str) -> tuple[str, str]:
    """Return (url, source) for the requested channel.

    Resolution order:
      1. The corresponding env var, if set (allows override and offline use).
      2. AWS SSM Parameter Store at the canonical path for that channel.

    ``channel`` is one of "test", "prod", or "auto". "auto" only consults the
    env vars (any of TEST/PROD/legacy) and refuses to silently default to a
    specific SSM channel — picking prod-vs-test should be explicit.
    """
    test_url: str = os.environ.get(SLACK_CHANNEL_TEST_ENV, "").strip()
    prod_url: str = os.environ.get(SLACK_CHANNEL_PROD_ENV, "").strip()
    legacy_url: str = os.environ.get(SLACK_CHANNEL_ENV, "").strip()

    if channel == "test":
        if test_url:
            return test_url, f"{SLACK_CHANNEL_TEST_ENV} env var"
        return fetch_ssm_parameter(SSM_SLACK_WEBHOOK_TEST), f"AWS SSM ({SSM_SLACK_WEBHOOK_TEST})"
    if channel == "prod":
        if prod_url:
            return prod_url, f"{SLACK_CHANNEL_PROD_ENV} env var"
        return fetch_ssm_parameter(SSM_SLACK_WEBHOOK_PROD), f"AWS SSM ({SSM_SLACK_WEBHOOK_PROD})"
    # auto: env vars only; require explicit --slack-channel for SSM.
    if prod_url:
        return prod_url, f"{SLACK_CHANNEL_PROD_ENV} env var"
    if test_url:
        return test_url, f"{SLACK_CHANNEL_TEST_ENV} env var"
    if legacy_url:
        return legacy_url, f"{SLACK_CHANNEL_ENV} env var"
    raise SystemExit(
        "--slack with --slack-channel=auto requires one of "
        f"{SLACK_CHANNEL_PROD_ENV}, {SLACK_CHANNEL_TEST_ENV}, or {SLACK_CHANNEL_ENV} "
        "to be set. Pass --slack-channel=test|prod to fetch from AWS SSM instead."
    )


def notify_slack(
    version: str, target_sha: str, draft_url: str, test_dm: bool, channel: str, precheck: bool = True
) -> None:
    brett: str = slack_mention(SLACK_USER_BRETT_ENV, "@brettlangdon")
    emmett: str = slack_mention(SLACK_USER_EMMETT_ENV, "@emmettbutler")
    text: str = (
        f":package: Draft RC release ready for review: {draft_url}\n"
        f"Version: `{version}` (target: `{target_sha}`)\n"
        f"Reviewers: {brett} {emmett} — please review and *click Publish release*. "
        f"Publishing requires repo-owner perms (the tag is created at publish time, "
        f"and tag creation is gated by branch protection)."
    )

    dm_url: str = os.environ.get(SLACK_DM_ENV, "").strip()

    if test_dm:
        if not dm_url:
            raise SystemExit(f"--test-dm requires {SLACK_DM_ENV} to be set")
        if precheck:
            post_webhook(dm_url, "[ping] release-script connectivity check (DM)")
        post_webhook(dm_url, f"[TEST] {text}")
        print(f"sent test DM via {SLACK_DM_ENV}")
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
        print(f"sent runner-copy DM via {SLACK_DM_ENV}")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def _add_common_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--verbose",
        action="store_true",
        help="echo stderr from external tools (reno, pandoc, gh) and print tool versions at start",
    )


def build_parser() -> argparse.ArgumentParser:
    p: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="release.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub: argparse._SubParsersAction = p.add_subparsers(dest="cmd", required=True)

    bp: argparse.ArgumentParser = sub.add_parser("bump-rc", help="open the rc(N+1) bump PR")
    bp.add_argument("--base-branch", default="main", help="base branch to PR against (default: main)")
    bp.add_argument("--dry-run", action="store_true")
    _add_common_flags(bp)
    bp.set_defaults(func=cmd_bump_rc)

    eb: argparse.ArgumentParser = sub.add_parser("ensure-branch", help="create X.Y release branch if missing")
    eb.add_argument("target", help="branch name like 4.9")
    eb.add_argument("--from", dest="from_branch", help="prior X.Y branch to fork from (default: highest below target)")
    eb.add_argument("--dry-run", action="store_true")
    _add_common_flags(eb)
    eb.set_defaults(func=cmd_ensure_branch)

    dr: argparse.ArgumentParser = sub.add_parser(
        "draft-release", help="create a draft GitHub release with reno-generated notes"
    )
    dr.add_argument("--target", default="main", help="branch whose HEAD is the release target (default: main)")
    dr.add_argument("--version", help="explicit version (default: read from pyproject at target)")
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
    dr.add_argument("--slack", action="store_true", help="post to release channel selected by --slack-channel")
    dr.add_argument(
        "--slack-channel",
        choices=["test", "prod", "auto"],
        default="auto",
        help=(
            "which channel webhook to use: test reads "
            f"{SLACK_CHANNEL_TEST_ENV}, prod reads {SLACK_CHANNEL_PROD_ENV}, "
            f"auto prefers prod then test then the legacy {SLACK_CHANNEL_ENV} (default: auto)"
        ),
    )
    dr.add_argument(
        "--no-slack-precheck",
        dest="slack_precheck",
        action="store_false",
        default=True,
        help=(
            "skip the connectivity-check ping that's sent to Slack before the "
            "real announcement (default: precheck enabled)"
        ),
    )
    dr.add_argument("--test-dm", action="store_true", help="DM the runner only (no channel post)")
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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
