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
  post-release <version>     After a maintainer publishes: wait for PyPI, open next bump.
  wait-pypi <version>        Poll until PyPI lists the version (tag-pipeline follow-up).

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
                                   [--wait-pypi|--no-wait-pypi]
                                   [--pypi-wait-timeout SECS] [--pypi-poll-interval SECS]
  scripts/gh/create_gh_release.py wait-pypi v4.16.2 [--dry-run]
                                   [--wait-pypi|--no-wait-pypi]
                                   [--pypi-wait-timeout SECS] [--pypi-poll-interval SECS]
  scripts/gh/create_gh_release.py bump-rc [--base-branch main] [--dry-run]
  scripts/gh/create_gh_release.py ensure-branch X.Y [--from A.B] [--dry-run]
  scripts/gh/create_gh_release.py draft-release [--target main] [--version 4.9.0rc3]
                                   [--allow-version-mismatch] [--allow-ci-failure]
                                   [--wait-ci|--no-wait-ci] [--ci-wait-timeout SECS]
                                   [--slack] [--slack-channel prod|guild|test|auto]
                                   [--slack-precheck] [--test-dm]
                                   [--dry-run] [--verbose]

CI wait (patch / rc / final / draft-release):
  Wet runs poll GitHub statuses/checks until green (default --wait-ci, 75m
  timeout, ~45s poll). On green the same invocation continues and creates the
  draft. On red CI it exits with an error and prints classified failing
  contexts (Needs Retry vs product vs rollup) plus Mosaic/GitHub URLs when
  available — waiting will not help once CI has failed. On timeout it exits
  WAITING ON with the last status (re-run to resume). --dry-run never waits.
  --no-wait-ci exits WAITING ON immediately when CI is still pending.

PyPI wait (post-release / wait-pypi):
  GitHub Publish creates the tag; wheels upload only if the GitLab *tag*
  pipeline succeeds (release_pypi_prod is on_success). Branch CI green before
  drafting is necessary but not sufficient. Wet post-release / wait-pypi poll
  PyPI until ddtrace==version exists (default --wait-pypi, 90m / 60s). On
  timeout: WAITING ON with the tag-pipeline URL and Retry guidance.
  --dry-run never polls; --no-wait-pypi one-shots then WAITING ON if missing.

Slack env vars (preferred names; no DD_ prefix required for this tooling):
  DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL
                                     Preferred: Incoming Webhook for #apm-python-release
                                     (hooks.slack.com/services/...). Interim: Workflow
                                     Builder trigger (hooks.slack.com/triggers/...) —
                                     script POSTs structured fields (not mrkdwn).
  DDTRACE_RELEASE_DM_URL             required for --test-dm (personal DM webhook;
                                     secret; not committed; no SSM path yet)
  DDTRACE_RELEASE_GUILD_CHANNEL_URL  optional escape hatch for --slack-channel=guild
                                     (#guild-dd-trace-py)
  DD_RELEASE_SLACK_WEBHOOK_URL_PROD  legacy alias for the apm-python-release webhook
  DD_RELEASE_SLACK_WEBHOOK_URL_TEST  webhook bound to your test channel
  DD_RELEASE_SLACK_WEBHOOK_URL       legacy single-channel fallback
  DD_SLACK_RELEASERS_SUBTEAM_ID      optional Slack usergroup ID (S…) for
                                     @dd-trace-py-releasers in Incoming Webhook
                                     mrkdwn (``<!subteam^ID|handle>``). Without
                                     it, mrkdwn uses ``@dd-trace-py-releasers``
                                     as plain text. Workflow path: put a static
                                     User Group mention in the rebuilt template.
  DDTRACE_RELEASE_SLACK_WORKFLOW_STRUCTURED
                                     set to 1 to force the structured Workflow
                                     payload even when the URL is not triggers/
                                     (rare; normally auto-detected)

Local persistence (no SSM / no export each session): put the vars in
gitignored ``scripts/gh/.env.local`` (see ``scripts/gh/.env.local.example``).
``main`` loads that file if present; already-set env vars win. Do not commit
``.env.local``.

If the channel env var is unset and you pass --slack-channel=prod|guild|test,
the script falls back to AWS SSM Parameter Store at:
  ci.dd-trace-py.slack-release-webhook-prod
  ci.dd-trace-py.slack-release-webhook-guild
  ci.dd-trace-py.slack-release-webhook-test
This matches the ``ci.dd-trace-py.<key>`` convention used elsewhere in this
repo for release secrets (see .gitlab/release.yml). Requires AWS credentials
to be available (e.g. via aws-vault or your usual SSO flow). The DM webhook
is env-only (do not commit it); channel webhooks stay env-or-SSM.

Prod webhook setup (preferred): create a Slack **Incoming Webhook**
(hooks.slack.com/services/...) for #apm-python-release. That path posts
``{"text": "<mrkdwn>"}`` and renders bold/links/mentions. Put the URL in
``scripts/gh/.env.local`` as DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL
and optionally SSM ``ci.dd-trace-py.slack-release-webhook-prod``.

Interim (Workflow ``triggers/``): if the URL is hooks.slack.com/triggers/,
the script POSTs flat string fields (version, kind, draft_url, …) instead of
an mrkdwn blob. Rebuild the Workflow to insert those variables (see
``scripts/gh/SLACK_WORKFLOW_INTERIM.md``). Freeform mrkdwn on triggers/ is
refused — it shows as literals.

Failure recovery:
  Push succeeded but `gh pr create` failed (bump-rc):
      git push origin --delete bump-version/<version>   # then re-run bump-rc

  Draft release created but Slack post failed (draft-release):
      The draft is still on GitHub; re-run with --slack / --test-dm to
      announce the existing draft URL (no second draft is created), or
      post manually. Same path for Slack-only after a wet draft: e.g.
      draft-release --target 4.16 --slack
      (or re-run patch/rc/final with --slack). Existing draft is reused.

  Dry-run with --slack / --test-dm:
      Chooses the preview from the configured webhook URL (env / .env.local):
      ``triggers/`` → structured Workflow JSON + channel-rendered mock;
      ``services/`` → Incoming Webhook mrkdwn. Does not call the webhook.

  GitHub release published but PyPI missing (tag pipeline red):
      Open the tag pipeline (print_gitlab_watch_list / WAITING ON text),
      Retry failed jobs (e.g. contrib/molten), wait for release_pypi_prod,
      then re-run `post-release vX.Y.Z` or `wait-pypi vX.Y.Z`. Do not treat
      branch CI green as done.

  ensure-branch checked out a local branch but `git push` failed:
      git checkout main && git branch -D <X.Y>   # then re-run ensure-branch

Bump-first (patch / draft-release) — step 1 of the release run:
  Tip ``pyproject.toml`` must equal the release version **before** CI wait,
  draft notes, Slack, or Publish. When tip is still the previous patch/RC,
  the script opens (or reuses) a **ready-for-review** bump PR into the
  release branch and exits WAITING ON merge — it never creates a GitHub
  *release* draft on a mismatch. Bump PRs are ready (not ``--draft``) so
  CODEOWNERS can review immediately. ``--allow-version-mismatch`` is dry-run
  notes preview only. Source of truth is ``pyproject.toml``
  (``scripts/verify-package-version`` on the bump commit);
  ``ddtrace.version`` comes from the built wheel metadata.

Gotchas:
  - Workflow Builder triggers/ URLs: script posts structured fields. The
    Slack Workflow must be rebuilt to use those variable names (see
    ``scripts/gh/SLACK_WORKFLOW_INTERIM.md``). Prefer Incoming Webhook
    (services/) for correct freeform mrkdwn without a Workflow rebuild.
  - Optional ``--slack-precheck`` sends a short connectivity ping before
    the real announce (off by default — avoid dual channel noise).
  - Draft review ping targets ``@dd-trace-py-releasers`` (not individual
    users). For Incoming Webhook mrkdwn set ``DD_SLACK_RELEASERS_SUBTEAM_ID``
    so the subteam mention notifies; Workflow rebuilds should insert a
    static User Group mention (see ``SLACK_WORKFLOW_INTERIM.md``).
  - Requires `gh` CLI 2.x for `--jq` support and `aws` CLI for SSM lookup.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from dataclasses import field
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess  # nosec B404 — script orchestrates git/gh/aws CLIs
import sys
import tempfile
import time
from typing import Any
from typing import Callable
from typing import Optional
from typing import Sequence
from typing import Union
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
# Gitignored local secrets (Slack webhooks). See .env.local.example.
LOCAL_ENV_FILE: Path = Path(__file__).resolve().parent / ".env.local"

# Wet-run CI poll defaults (patch / rc / final / draft-release). Dry-run never waits.
DEFAULT_CI_WAIT_TIMEOUT_S: float = 75 * 60
DEFAULT_CI_POLL_INTERVAL_S: float = 45.0
# After GitHub Publish creates the tag, GitLab tag pipeline builds wheels and
# ``release_pypi_prod`` uploads to PyPI. Branch CI green is not enough — wait
# for PyPI (post-release / wait-pypi). Default ~90m covers a full tag pipeline.
DEFAULT_PYPI_WAIT_TIMEOUT_S: float = 90 * 60
DEFAULT_PYPI_POLL_INTERVAL_S: float = 60.0
# Canonical GitLab project path for tag pipelines (apm-reliability namespace).
GITLAB_DD_TRACE_PY_PROJECT: str = "DataDog/apm-reliability/dd-trace-py"

# Slack webhook env vars. Channel webhooks: prod (#apm-python-release, default
# review ping), optional guild (#guild-dd-trace-py), test, plus legacy
# single-channel. ``--slack-channel`` selects which; ``--test-dm`` uses the DM
# webhook only. Preferred names drop the DD_ product prefix (local release
# tooling, not agent config).
SLACK_CHANNEL_DEFAULT: str = "prod"
SLACK_CHANNEL_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL"
SLACK_CHANNEL_PROD_ENV: str = "DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL"
SLACK_CHANNEL_PROD_ENV_LEGACY: str = "DD_RELEASE_SLACK_WEBHOOK_URL_PROD"
SLACK_CHANNEL_GUILD_ENV: str = "DDTRACE_RELEASE_GUILD_CHANNEL_URL"
SLACK_CHANNEL_TEST_ENV: str = "DD_RELEASE_SLACK_WEBHOOK_URL_TEST"
SLACK_DM_ENV: str = "DDTRACE_RELEASE_DM_URL"
# Slack User Group for people with tag/publish perms (Brett confirmed
# 2026-11-09). Mosaic pack: https://mosaic.us1.ddbuild.io/packs/pack/dd-trace-py-releasers
SLACK_RELEASERS_HANDLE: str = "dd-trace-py-releasers"
# CODEOWNERS for pyproject.toml — request on ready bump PRs (not Slack).
BUMP_PR_REVIEWER_TEAM: str = "DataDog/python-guild"
SLACK_RELEASERS_SUBTEAM_ENV: str = "DD_SLACK_RELEASERS_SUBTEAM_ID"
# #apm-python-release — from docs/contributing-release.rst (not a secret).
SLACK_CHANNEL_PROD_ID: str = "C04MK6NNDG9"
SLACK_CHANNEL_PROD_NAME: str = "apm-python-release"
# #guild-dd-trace-py — Slack workspace lookup 2026-10-29 (not a secret).
# Escape hatch via --slack-channel=guild only; not the default.
SLACK_CHANNEL_GUILD_ID: str = "C02E4BKJQBT"
SLACK_CHANNEL_GUILD_NAME: str = "guild-dd-trace-py"

# AWS SSM Parameter Store paths. Canonical storage for shared release secrets
# in this repo — see .gitlab/release.yml and scripts/upload-debug-symbols-to-
# backend.sh for the same `ci.dd-trace-py.<key>` convention. The script falls
# back to SSM when the corresponding env var is unset, so a freshly-set-up
# release engineer with AWS creds (and no other config) can run the script
# without manually exporting webhook URLs. DM webhook has no SSM path yet
# (env-only; do not commit the secret URL). Prod SSM should store an Incoming
# Webhook (hooks.slack.com/services/...) for #apm-python-release — not a
# Workflow Builder triggers/ URL (those break mrkdwn).
SSM_REGION: str = "us-east-1"
SSM_SLACK_WEBHOOK_PROD: str = "ci.dd-trace-py.slack-release-webhook-prod"
SSM_SLACK_WEBHOOK_GUILD: str = "ci.dd-trace-py.slack-release-webhook-guild"
SSM_SLACK_WEBHOOK_TEST: str = "ci.dd-trace-py.slack-release-webhook-test"
# Force structured Workflow payload even when the URL is not triggers/.
SLACK_WORKFLOW_STRUCTURED_ENV: str = "DDTRACE_RELEASE_SLACK_WORKFLOW_STRUCTURED"

# Required on every `gh release create`. Prerelease / latest are asserted
# separately from the version kind — see ``build_gh_release_args``.
_GH_RELEASE_REQUIRED_FLAGS: tuple[str, ...] = ("--draft",)

GH_REPO: str = "DataDog/dd-trace-py"
BACKPORT_LABEL_RE: re.Pattern[str] = re.compile(r"^backport\s+(\d+\.\d+)$")
SYSTEM_TESTS_PIN_MAX_AGE_DAYS: int = 14
REVIEW_TEXT_TEMPLATE: str = ":headsup: The draft release for {version} is ready for review. Can I get a :lgtm:? {url}"
# Light “small print” footer for Slack / review paste — italic via Slack `_..._`.
# Compare-to-main link is intentional self-promo for the release-script work (no PR yet).
# Same URL is used in bump-PR Markdown footers via ``generated_by_pr_line``.
_RELEASE_SCRIPT_BRANCH_URL: str = (
    "https://github.com/DataDog/dd-trace-py/compare/main...vlad/release-create-release-rc-script"
)
# Cached Slack/review credit for who ran the script (gh login or local name).
_RUNNER_DISPLAY_CACHE: Optional[str] = None
# Plain GitHub login used inside resolve_runner_display / resolve_runner_plain.
_RUNNER_LOGIN_CACHE: Optional[str] = None
# Release-script feature branch name (compare URL leaf + Workflow footer field).
_RELEASE_SCRIPT_BRANCH_NAME: str = "vlad/release-create-release-rc-script"
_RELEASE_SCRIPT_PATH: str = "scripts/gh/create_gh_release.py"

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


def format_pyproject_version_mismatch_error(
    *,
    version: str,
    pyproject_version: str,
    ref: str,
) -> str:
    """Clear refuse text when release version ≠ pyproject at the tip/tag."""
    ver: str = normalize_version_str(version)
    py: str = normalize_version_str(pyproject_version)
    return (
        f"pyproject.toml at {ref} is {py!r} but release version is {ver!r}. "
        "GitLab job `package version` requires CI_COMMIT_TAG == v{pyproject} "
        f"(Publish would create tag v{ver}). Bump version on the release "
        f"branch before drafting/tagging — open and merge "
        f"`chore: bump version to {ver}` (or set version) so pyproject is "
        f"{ver!r} on the tip you draft. Skipping the bump is how v4.16.4 "
        "shipped with pyproject 4.16.3 and blocked PyPI."
    )


def ensure_pyproject_matches_release_version(
    *,
    version: str,
    ref: str,
    dry_run: bool = False,
    allow_mismatch: bool = False,
) -> None:
    """Refuse when ``pyproject.toml`` at ``ref`` ≠ release ``version``.

    Wet draft/publish paths always refuse a mismatch (no override). Dry-run
    may pass ``allow_mismatch=True`` to preview notes only — prints a warning.
    Source of truth for the shipped package version is ``pyproject.toml``;
    ``ddtrace.version`` reads installed metadata from that build.

    Prefer ``ensure_tip_version_before_draft`` on draft/patch paths — that
    opens the bump PR first instead of only refusing.
    """
    ver: str = normalize_version_str(version)
    pyproject_version: str = normalize_version_str(read_pyproject_version_at(ref))
    if ver == pyproject_version:
        return
    msg: str = format_pyproject_version_mismatch_error(
        version=ver,
        pyproject_version=pyproject_version,
        ref=ref,
    )
    if dry_run and allow_mismatch:
        print(f"WARN (--allow-version-mismatch, dry-run only): {msg}", file=sys.stderr)
        return
    if allow_mismatch and not dry_run:
        raise SystemExit(f"{msg}\n--allow-version-mismatch is dry-run only; wet path always refuses.")
    raise SystemExit(msg)


def suggested_next_version_from_tip(tip_version: str) -> Optional[str]:
    """Next patch or RC after ``tip_version``, or None if tip is not bumpable that way."""
    tip: str = normalize_version_str(tip_version)
    if is_stable_version(tip):
        return compute_next_patch(tip)
    if is_rc_version(tip):
        return compute_next_rc(tip)
    return None


def ensure_bump_pr_ready_for_review(pr_number: int, *, dry_run: bool) -> None:
    """Mark an open bump PR ready for review (not draft). No-op if already ready."""
    if dry_run:
        print(f"(dry-run) would ensure PR #{pr_number} is ready for review (not draft)")
        return
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
        [
            "gh",
            "pr",
            "view",
            str(pr_number),
            "--repo",
            GH_REPO,
            "--json",
            "isDraft",
            "--jq",
            ".isDraft",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    if cp.returncode != 0:
        return
    if cp.stdout.strip().lower() == "true":
        run(["gh", "pr", "ready", str(pr_number), "--repo", GH_REPO], cwd=REPO_ROOT)
        print(f"marked bump PR #{pr_number} ready for review")


def open_or_wait_version_bump(
    *,
    base_branch: str,
    new_version: str,
    dry_run: bool,
    body_extra: str = "",
) -> None:
    """Open or reuse a ready bump PR, then always raise ``WaitingOn``.

    Never continues to CI/GitHub-release-draft/Slack — tip must match after merge.
    """
    ver: str = normalize_version_str(new_version)
    open_bps: list[dict[str, Any]] = find_open_bump_prs(base_branch, ver)
    if open_bps:
        url: str = str(open_bps[0].get("url") or "")
        number_raw: Any = open_bps[0].get("number")
        if number_raw is not None:
            ensure_bump_pr_ready_for_review(int(number_raw), dry_run=dry_run)
        raise WaitingOn(
            f"merge of bump PR to {ver}"
            + (f" ({url})" if url else "")
            + " — tip pyproject must equal the release version before CI/draft/Slack/Publish"
        )
    title: str = f"chore: bump {ver}" if is_rc_version(ver) else f"chore: bump version to {ver}"
    extra: str = body_extra.strip() or (
        "Bump-first (release run step 1): tip `pyproject.toml` must equal the "
        "release version before CI wait, draft notes, Slack, or Publish "
        "(GitLab `package version` requires `CI_COMMIT_TAG == v{pyproject}`)."
    )
    pr_url: Optional[str] = open_version_bump_pr(
        base_branch=base_branch,
        new_version=ver,
        title=title,
        dry_run=dry_run,
        body_extra=extra,
    )
    raise WaitingOn(
        f"merge of bump PR to {ver}"
        + (f" ({pr_url})" if pr_url else " (dry-run)")
        + " — tip pyproject must equal the release version before CI/draft/Slack/Publish"
    )


def ensure_tip_version_before_draft(
    *,
    base_branch: str,
    version: str,
    tip_ref: str,
    dry_run: bool = False,
    allow_mismatch: bool = False,
) -> None:
    """Earliest release gate: tip pyproject == version, else bump-first and wait.

    When tip is the previous stable patch or RC and ``version`` is the natural
    next bump, open/reuse that bump PR and raise ``WaitingOn``. Never drafts
    while mismatched. ``allow_mismatch`` is dry-run notes preview only.
    """
    ver: str = normalize_version_str(version)
    tip_ver: str = normalize_version_str(read_pyproject_version_at(tip_ref))
    if ver == tip_ver:
        return
    msg: str = format_pyproject_version_mismatch_error(
        version=ver,
        pyproject_version=tip_ver,
        ref=tip_ref,
    )
    if dry_run and allow_mismatch:
        print(f"WARN (--allow-version-mismatch, dry-run only): {msg}", file=sys.stderr)
        return
    if allow_mismatch and not dry_run:
        raise SystemExit(f"{msg}\n--allow-version-mismatch is dry-run only; wet path always refuses.")

    suggested: Optional[str] = suggested_next_version_from_tip(tip_ver)
    if suggested == ver:
        print(
            f"Bump-first: tip pyproject is {tip_ver!r}, release version is {ver!r}. "
            "Opening/waiting on the version bump before CI/draft/Slack."
        )
        open_or_wait_version_bump(base_branch=base_branch, new_version=ver, dry_run=dry_run)
    raise SystemExit(msg)


def ensure_tag_pyproject_matches_version(version: str) -> None:
    """Refuse when the published tag's tree has the wrong pyproject version."""
    ver: str = normalize_version_str(version)
    tag: str = f"v{ver}"
    try:
        run(["git", "fetch", "origin", f"refs/tags/{tag}:refs/tags/{tag}"], cwd=REPO_ROOT)
    except SystemExit:
        # Tag may already be local; rev-parse below is authoritative.
        pass
    try:
        sha: str = run_out(["git", "rev-parse", f"{tag}^{{commit}}"], cwd=REPO_ROOT)
    except SystemExit as exc:
        raise SystemExit(
            f"cannot resolve tag {tag} locally after fetch — confirm the GitHub release created it. Underlying: {exc}"
        ) from exc
    ensure_pyproject_matches_release_version(version=ver, ref=sha, dry_run=False, allow_mismatch=False)


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


def _gh_api_user_login(*, token: str = "") -> str:
    """Return ``gh api user --jq .login``, optionally with an explicit token."""
    env: dict[str, str] = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token
        env.pop("GITHUB_TOKEN", None)
    try:
        cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
            ["gh", "api", "user", "--jq", ".login"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if cp.returncode != 0:
        return ""
    return cp.stdout.strip()


def _gh_auth_logins_github_com() -> list[str]:
    """Logged-in github.com accounts from ``gh auth status`` (active first)."""
    try:
        cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
            ["gh", "auth", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    text: str = (cp.stdout or "") + (cp.stderr or "")
    active: list[str] = []
    others: list[str] = []
    current: str = ""
    for line in text.splitlines():
        m: Optional[re.Match[str]] = re.search(
            r"Logged in to github\.com account (\S+)",
            line,
        )
        if m is not None:
            current = m.group(1)
            continue
        if current and "Active account: true" in line:
            active.append(current)
            current = ""
            continue
        if current and "Active account: false" in line:
            others.append(current)
            current = ""
            continue
    return active + others


def _gh_auth_token_for_user(login: str) -> str:
    """Token for a logged-in github.com user, or empty if unavailable."""
    try:
        cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
            ["gh", "auth", "token", "--user", login],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if cp.returncode != 0:
        return ""
    return cp.stdout.strip()


def resolve_gh_login() -> str:
    """GitHub login for Slack/review credit.

    Prefer the active ``gh`` login. On DataDog/* repos, if the active login
    looks like an EMU account (``*_ddog``), prefer another logged-in
    github.com account that is not ``*_ddog`` (personal vs EMU). Falls back
    to the active login when no alternate works.
    """
    global _RUNNER_LOGIN_CACHE
    if _RUNNER_LOGIN_CACHE is not None:
        return _RUNNER_LOGIN_CACHE

    active: str = _gh_api_user_login()
    login: str = active
    if GH_REPO.startswith("DataDog/") and active.endswith("_ddog"):
        for candidate in _gh_auth_logins_github_com():
            if candidate == active or candidate.endswith("_ddog"):
                continue
            token: str = _gh_auth_token_for_user(candidate)
            if not token:
                continue
            verified: str = _gh_api_user_login(token=token)
            if verified == candidate:
                login = candidate
                break

    _RUNNER_LOGIN_CACHE = login
    return login


def resolve_runner_display() -> str:
    """Who ran this script, for Slack/review fine print.

    Prefer the authenticated GitHub login (linkable); fall back to
    ``git config user.name``, then the local login name. Cached once per
    process so precheck + announce (or review paste + Slack) do not re-hit
    ``gh``.
    """
    global _RUNNER_DISPLAY_CACHE
    if _RUNNER_DISPLAY_CACHE is not None:
        return _RUNNER_DISPLAY_CACHE

    login: str = resolve_gh_login()
    if login:
        _RUNNER_DISPLAY_CACHE = f"<https://github.com/{login}|{login}>"
        return _RUNNER_DISPLAY_CACHE

    name: str = ""
    try:
        cp_git: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes git CLI, trusted PATH
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=REPO_ROOT,
        )
        if cp_git.returncode == 0:
            name = cp_git.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    if name:
        _RUNNER_DISPLAY_CACHE = f"`{name}`"
        return _RUNNER_DISPLAY_CACHE

    local_user: str = getpass.getuser()
    _RUNNER_DISPLAY_CACHE = f"`{local_user}`"
    return _RUNNER_DISPLAY_CACHE


def generated_by_pr_line(extra: str = "") -> str:
    """Bump-PR body credit linking the release-script feature branch."""
    linked: str = f"[{_RELEASE_SCRIPT_PATH}]({_RELEASE_SCRIPT_BRANCH_URL})"
    if extra:
        return f"_note: generated by {linked} {extra}._"
    return f"_note: generated by {linked}._"


def auto_created_footer() -> str:
    """Fine-print footer: automation branch credit plus who ran the tool."""
    runner: str = resolve_runner_display()
    return (
        f"_Brought to you by release automation — `{_RELEASE_SCRIPT_PATH}` "
        f"on <{_RELEASE_SCRIPT_BRANCH_URL}|{_RELEASE_SCRIPT_BRANCH_NAME}>. "
        f"Run by {runner}. "
        "Try `patch` / `rc` / `final`._"
    )


def review_text(version: str, url: str) -> str:
    head: str = REVIEW_TEXT_TEMPLATE.format(version=normalize_version_str(version), url=url)
    return f"{head}\n{auto_created_footer()}"


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


# Classification labels for red CI (shown in refuse messages).
CI_KIND_RETRY_INFRA: str = "retry_infra"
CI_KIND_ROLLUP: str = "rollup"
CI_KIND_PRODUCT: str = "product"
CI_KIND_UNKNOWN: str = "unknown"

# GitHub status description / context hints that usually mean platform flake.
_CI_INFRA_HINT_RE: re.Pattern[str] = re.compile(
    r"insufficient resources|not scheduled|untolerated taints|"
    r"timed?\s*out|timeout|image pull|BadZipFile|truncated file header|"
    r"context deadline|infrastructure|runner unavailable|network error|"
    r"pod was not|ECR|environment preparation",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CiFailureDetail:
    """One red check context / check-run with optional GitHub status URL."""

    name: str
    url: str = ""
    description: str = ""


def coerce_ci_failures(failing: Sequence[Union[str, CiFailureDetail]]) -> list[CiFailureDetail]:
    """Normalize summary fakes (plain names) and live details to ``CiFailureDetail``."""
    out: list[CiFailureDetail] = []
    for item in failing:
        if isinstance(item, CiFailureDetail):
            out.append(item)
        else:
            out.append(CiFailureDetail(name=str(item)))
    return out


def ci_failure_names(failing: Sequence[Union[str, CiFailureDetail]]) -> list[str]:
    """Plain context names for ReleaseState / SLO filters."""
    return [d.name for d in coerce_ci_failures(failing)]


def classify_ci_failure(detail: CiFailureDetail) -> str:
    """Heuristic class for a red check: retry_infra / rollup / product / unknown.

    Uses GitHub context name + status description only (no extra network).
    Infra/platform leafs → Retry; parent rollups → ignore for Retry list;
    product → likely code; unknown → open Mosaic and inspect.
    """
    bare: str = re.sub(r" \(check-run(?:, [^)]+)?\)$", "", detail.name).strip()
    desc: str = (detail.description or "").strip()
    lower_bare: str = bare.lower()
    if bare == "DDCI Status" or lower_bare.endswith("/default-pipeline") or lower_bare == "default-pipeline":
        return CI_KIND_ROLLUP
    if "action_required" in detail.name.lower():
        return CI_KIND_PRODUCT
    blob: str = f"{bare}\n{desc}"
    if _CI_INFRA_HINT_RE.search(blob):
        return CI_KIND_RETRY_INFRA
    # Microbenchmark matrix jobs on this repo are usually platform flakes.
    if "microbenchmarks" in lower_bare:
        return CI_KIND_RETRY_INFRA
    return CI_KIND_UNKNOWN


def github_commit_checks_url(sha: str) -> str:
    """GitHub commit checks tab for the tip SHA."""
    return f"https://github.com/{GH_REPO}/commit/{sha}/checks"


def format_ci_failure_guidance(sha: str, failing: Sequence[Union[str, CiFailureDetail]]) -> str:
    """Actionable next steps when CI is already red (waiting will not help)."""
    details: list[CiFailureDetail] = coerce_ci_failures(failing)
    if not details:
        return ""
    lines: list[str] = [
        "Waiting will not turn these green — CI already failed.",
        "Classify + next action:",
    ]
    retry: list[CiFailureDetail] = []
    product: list[CiFailureDetail] = []
    unknown: list[CiFailureDetail] = []
    rollup: list[CiFailureDetail] = []
    for d in details:
        kind: str = classify_ci_failure(d)
        if kind == CI_KIND_RETRY_INFRA:
            retry.append(d)
        elif kind == CI_KIND_PRODUCT:
            product.append(d)
        elif kind == CI_KIND_ROLLUP:
            rollup.append(d)
        else:
            unknown.append(d)

    def _emit_group(label: str, items: list[CiFailureDetail]) -> None:
        if not items:
            return
        lines.append(f"  {label}")
        for item in items[:15]:
            lines.append(f"    - {item.name}")
            if item.description:
                lines.append(f"      note: {item.description}")
            if item.url:
                lines.append(f"      url: {item.url}")
        if len(items) > 15:
            lines.append(f"    ... and {len(items) - 15} more")

    _emit_group("Needs Retry (likely platform/infra):", retry)
    _emit_group("Likely product / needs investigation:", product)
    _emit_group("Inspect in Mosaic (class unclear):", unknown)
    if rollup:
        roll_names: str = ", ".join(d.name for d in rollup[:8])
        lines.append(
            f"  Rollup contexts (clear after leaf jobs go green): {roll_names}"
            + (f" (+{len(rollup) - 8} more)" if len(rollup) > 8 else "")
        )
    lines.append(f"  GitHub checks: {github_commit_checks_url(sha)}")
    mosaic_urls: list[str] = []
    for d in details:
        if d.url and "mosaic." in d.url and d.url not in mosaic_urls:
            mosaic_urls.append(d.url)
    if mosaic_urls:
        # Prefer the change-request root when present.
        root: Optional[str] = None
        for u in mosaic_urls:
            m: Optional[re.Match[str]] = re.match(
                r"(https://mosaic[^?\s]+/change-request/[^?\s]+)",
                u,
            )
            if m:
                root = m.group(1)
                break
        lines.append(f"  Mosaic: {root or mosaic_urls[0]}")
        if retry or unknown:
            lines.append(
                "  Open Mosaic → retry the failed leaf jobs (or re-run failed jobs), "
                "then re-run this command when GitHub status is green."
            )
    lines.append("Do not pass --allow-ci-failure unless you intentionally cut from a red tip.")
    return "\n".join(lines)


def get_commit_ci_summary(
    sha: str,
) -> tuple[str, list[CiFailureDetail], list[str], int]:
    """Return CI status as ``(overall_state, failing, pending, passing_count)``.

    Combines two GitHub APIs:

    - ``/commits/{sha}/status`` — the legacy "combined commit status" (used by
      GitLab CI's GitHub bridge in this repo). Returns a single ``state`` field
      and a list of per-context statuses.
    - ``/commits/{sha}/check-runs`` — the modern Checks API (used by GitHub
      Actions and some other tools). Each check-run has ``status`` and
      ``conclusion`` fields.

    Both must report green for the commit to be considered "fully passing".
    Failing entries include ``target_url`` / description when GitHub provides them
    so refuse messages can point at Mosaic Retry links.
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

    failing: list[CiFailureDetail] = []
    pending: list[str] = []
    passing_count: int = 0

    statuses_out: str = run_out(
        [
            "gh",
            "api",
            f"repos/DataDog/dd-trace-py/commits/{sha}/statuses",
            "--paginate",
            "--jq",
            ".[] | {context, state, target_url, description}",
        ],
        cwd=REPO_ROOT,
    )
    # Pagination returns most-recent first; track the latest row per context
    # so older superseded statuses don't double-count.
    latest_by_ctx: dict[str, dict[str, Any]] = {}
    for line in statuses_out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            s: dict[str, Any] = json.loads(line)
        except ValueError:
            continue
        ctx_name: str = s.get("context", "<unknown>")
        latest_by_ctx.setdefault(ctx_name, s)
    for ctx_name, row in latest_by_ctx.items():
        ctx_state: str = str(row.get("state") or "")
        if ctx_state == "success":
            passing_count += 1
        elif ctx_state == "pending":
            pending.append(ctx_name)
        elif ctx_state in ("failure", "error"):
            failing.append(
                CiFailureDetail(
                    name=ctx_name,
                    url=str(row.get("target_url") or ""),
                    description=str(row.get("description") or ""),
                )
            )

    # Modern check-runs API.
    runs_out: str = run_out(
        [
            "gh",
            "api",
            f"repos/DataDog/dd-trace-py/commits/{sha}/check-runs",
            "--jq",
            ".check_runs[] | {name, status, conclusion, html_url}",
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
        html_url: str = str(run_obj.get("html_url") or "")
        if run_status != "completed":
            pending.append(f"{name} (check-run)")
            if overall_state == "success":
                overall_state = "pending"
        elif conclusion in ("success", "neutral", "skipped"):
            passing_count += 1
        else:
            # failure, timed_out, cancelled, action_required, stale, ...
            failing.append(
                CiFailureDetail(
                    name=f"{name} (check-run, {conclusion or 'unknown'})",
                    url=html_url,
                    description=conclusion,
                )
            )
            if overall_state == "success":
                overall_state = "failure"

    return overall_state, failing, pending, passing_count


def format_ci_detail(
    sha: str,
    overall: str,
    failing: Sequence[Union[str, CiFailureDetail]],
    pending: list[str],
    passing: int,
) -> str:
    """Human-readable CI roll-up for logs and WAITING ON / error messages."""
    details: list[CiFailureDetail] = coerce_ci_failures(failing)
    short: str = sha if len(sha) <= 12 else sha[:12]
    detail: str = f"CI on {short}: state={overall}, passing={passing}, pending={len(pending)}, failing={len(details)}"
    if details:
        detail += "\n  failing:"
        for d in details[:10]:
            kind: str = classify_ci_failure(d)
            detail += f"\n    [{kind}] {d.name}"
            if d.description:
                detail += f" — {d.description}"
            if d.url:
                detail += f"\n      {d.url}"
        if len(details) > 10:
            detail += f"\n    ... and {len(details) - 10} more"
        detail += "\n" + format_ci_failure_guidance(sha, details)
    if pending:
        detail += "\n  pending:\n    " + "\n    ".join(pending[:10])
        if len(pending) > 10:
            detail += f"\n    ... and {len(pending) - 10} more"
    return detail


def resolve_wait_ci(args: argparse.Namespace) -> bool:
    """Wet runs wait by default; ``--dry-run`` never waits (even with ``--wait-ci``)."""
    dry_run: bool = bool(getattr(args, "dry_run", False))
    if dry_run:
        return False
    explicit: Optional[bool] = getattr(args, "wait_ci", None)
    if explicit is not None:
        return bool(explicit)
    return True


def wait_for_ci_green(
    sha: str,
    *,
    timeout_s: float = DEFAULT_CI_WAIT_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_CI_POLL_INTERVAL_S,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    summary_fn: Optional[
        Callable[
            [str],
            tuple[str, Sequence[Union[str, CiFailureDetail]], list[str], int],
        ]
    ] = None,
) -> None:
    """Poll GitHub until CI is green, failed, or the timeout elapses.

    On green: returns so the same invocation can create the draft.
    On failure: raises ``SystemExit`` with the failing check names and Retry guidance.
    On timeout: raises ``WaitingOn`` with the last status (main prints
    ``WAITING ON:`` and exits 2).
    """
    # Resolve at call time so tests can patch ``get_commit_ci_summary``.
    resolve_summary: Callable[
        [str],
        tuple[str, Sequence[Union[str, CiFailureDetail]], list[str], int],
    ] = summary_fn if summary_fn is not None else get_commit_ci_summary
    started: float = monotonic_fn()
    deadline: float = started + timeout_s
    poll_n: int = 0
    last_detail: str = ""
    while True:
        overall: str
        failing: Sequence[Union[str, CiFailureDetail]]
        pending: list[str]
        passing: int
        try:
            overall, failing, pending, passing = resolve_summary(sha)
        except subprocess.CalledProcessError as exc:
            # Transient gh/API blips should not abort a long wait; retry until timeout.
            now_err: float = monotonic_fn()
            remaining_err: float = deadline - now_err
            if remaining_err <= 0:
                raise WaitingOn(
                    f"CI still unresolved after {timeout_s:.0f}s on "
                    f"{sha if len(sha) <= 12 else sha[:12]} "
                    f"(last gh API error: exit {exc.returncode}). "
                    f"{last_detail or '(no successful status poll yet)'}\n"
                    "Re-run the same command to keep waiting, or pass --no-wait-ci."
                ) from exc
            elapsed_err: float = now_err - started
            poll_n += 1
            sleep_err: float = min(poll_interval_s, remaining_err)
            print(
                f"WAITING ON CI (poll {poll_n}): gh API error (exit {exc.returncode}); "
                f"retrying — elapsed {elapsed_err:.0f}s, next check in {sleep_err:.0f}s "
                f"(timeout {timeout_s:.0f}s)",
                flush=True,
            )
            sleep_fn(sleep_err)
            continue
        last_detail = format_ci_detail(sha, overall, failing, pending, passing)
        if overall == "success" and not failing and not pending:
            elapsed_ok: float = monotonic_fn() - started
            print(f"CI green after {elapsed_ok:.0f}s ({poll_n} polls): {last_detail}")
            return
        if failing:
            raise SystemExit(
                f"CI failed while waiting; refusing to draft. {last_detail}\n"
                "Fix, tip past the failure, or Retry infra jobs in Mosaic; "
                "only pass --allow-ci-failure if you intentionally cut from red."
            )
        now: float = monotonic_fn()
        remaining: float = deadline - now
        if remaining <= 0:
            raise WaitingOn(
                f"CI still pending after {timeout_s:.0f}s on "
                f"{sha if len(sha) <= 12 else sha[:12]}. {last_detail}\n"
                "Re-run the same command to keep waiting, or pass --no-wait-ci."
            )
        elapsed: float = now - started
        poll_n += 1
        sleep_for: float = min(poll_interval_s, remaining)
        print(
            f"WAITING ON CI (poll {poll_n}): {last_detail} — "
            f"elapsed {elapsed:.0f}s, next check in {sleep_for:.0f}s "
            f"(timeout {timeout_s:.0f}s)",
            flush=True,
        )
        sleep_fn(sleep_for)


def ensure_ci_green(
    sha: str,
    allow_failure: bool = False,
    *,
    wait: bool = False,
    timeout_s: float = DEFAULT_CI_WAIT_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_CI_POLL_INTERVAL_S,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> None:
    """Refuse to draft a release on a commit whose CI isn't green.

    Brett's ask: "validate that CI has completed and is green for the
    target-to-be-released commit". Defensive against the "I cut a release from
    a broken commit" foot-gun. ``allow_failure=True`` (from
    ``--allow-ci-failure``) downgrades to a warning since some teams accept a
    handful of flaky CI signals.

    When CI is still pending and ``wait=True`` (default for wet patch/rc/final/
    draft-release), poll until green, failed, or ``timeout_s``. Dry-run callers
    should pass ``wait=False`` so the script does not hang.
    """
    overall: str
    failing: list[CiFailureDetail]
    pending: list[str]
    passing: int
    overall, failing, pending, passing = get_commit_ci_summary(sha)
    detail: str = format_ci_detail(sha, overall, failing, pending, passing)
    if overall == "success" and not failing and not pending:
        print(detail)
        return
    if allow_failure:
        print(f"WARN (--allow-ci-failure): {detail}", file=sys.stderr)
        return
    if failing:
        raise SystemExit(
            f"refusing to draft release on a commit with non-green CI. {detail}\n"
            "Re-run after leaf jobs are green (Retry in Mosaic if platform/infra). "
            "Pass --allow-ci-failure only to intentionally override a red tip."
        )
    # Pending only from here.
    if wait:
        print(f"CI pending; waiting up to {timeout_s:.0f}s (poll every {poll_interval_s:.0f}s)")
        wait_for_ci_green(
            sha,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn,
            summary_fn=get_commit_ci_summary,
        )
        return
    raise WaitingOn(
        f"CI green on {sha if len(sha) <= 12 else sha[:12]}. {detail}\n"
        "Re-run after CI finishes, or pass --wait-ci to poll in this process."
    )


def ensure_ci_green_from_args(sha: str, args: argparse.Namespace) -> None:
    """Apply ``--wait-ci`` / ``--no-wait-ci`` / timeout flags from argparse."""
    ensure_ci_green(
        sha,
        allow_failure=bool(getattr(args, "allow_ci_failure", False)),
        wait=resolve_wait_ci(args),
        timeout_s=float(getattr(args, "ci_wait_timeout", DEFAULT_CI_WAIT_TIMEOUT_S)),
        poll_interval_s=float(getattr(args, "ci_poll_interval", DEFAULT_CI_POLL_INTERVAL_S)),
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

    pr_body: str = build_version_bump_pr_body(
        base_branch=base_branch,
        new_version=next_version,
        body_extra=("Once merged, run `scripts/gh/create_gh_release.py draft-release` to create the GitHub draft."),
        generated_by_extra="bump-rc",
    )
    try:
        pr_url: str = run_out(
            build_version_bump_pr_create_args(
                base_branch=base_branch,
                head_branch=branch,
                title=f"chore: bump {next_version}",
                pr_body=pr_body,
            ),
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
    print(f"\nReady-for-review bump PR opened: {pr_url}")
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
    That fallback still omits a ``## Unreleased`` heading — GitHub bodies should
    show ``### Bug Fixes`` / etc. directly under the EOL blurb.
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
    # Reno's Unreleased bucket is the right content for unpublished patches, but
    # GitHub release bodies must not keep the "## Unreleased" wrapper heading
    # (see 4.16.1 / 4.15.7 — EOL then ### Bug Fixes / … only).
    stripped: str = re.sub(
        r"(?m)^## Unreleased\s*\n+",
        "",
        formatted.strip(),
    ).strip()
    return stripped


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
            f'.[] | select(.tag_name == "v{ver}") | {{id, draft, html_url, target_commitish}}',
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


def draft_targets_tip(draft: dict[str, Any], tip_sha: str) -> bool:
    """True when draft ``target_commitish`` resolves to the same commit as ``tip_sha``."""
    tip: str = tip_sha.strip().lower()
    raw: str = str(draft.get("target_commitish") or "").strip()
    if not raw or not tip:
        return False
    raw_l: str = raw.lower()
    if raw_l == tip or tip.startswith(raw_l) or raw_l.startswith(tip):
        return True
    # Branch name or short SHA — resolve via git when possible.
    try:
        resolved: str = run_out(["git", "rev-parse", f"{raw}^{{commit}}"], cwd=REPO_ROOT).strip().lower()
    except SystemExit:
        return False
    return resolved == tip or tip.startswith(resolved) or resolved.startswith(tip)


def delete_release_by_id(release_id: int, *, dry_run: bool) -> None:
    """Delete a GitHub release (draft or published) by numeric id."""
    if dry_run:
        print(f"(dry-run) would: gh api -X DELETE repos/{GH_REPO}/releases/{release_id}")
        return
    run(
        ["gh", "api", "-X", "DELETE", f"repos/{GH_REPO}/releases/{release_id}"],
        cwd=REPO_ROOT,
    )


def check_existing_release(
    version: str,
    dry_run: bool = False,
    *,
    reuse_drafts: bool = False,
    required_tip_sha: Optional[str] = None,
) -> Optional[str]:
    """Refuse if published; optionally reuse tip-matching drafts only.

    When ``reuse_drafts`` is True, reuse a draft only if ``required_tip_sha`` is
    set and the draft's ``target_commitish`` matches that tip. Stale drafts
    (wrong SHA / pre-bump tip) are never reused for Slack announce — wet path
    deletes them so a fresh draft can be created at the current tip.
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
    if not drafts:
        return None

    tip: Optional[str] = required_tip_sha.strip() if required_tip_sha else None
    matching: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for d in drafts:
        if tip is not None and draft_targets_tip(d, tip):
            matching.append(d)
        else:
            stale.append(d)

    if stale:
        # Stale drafts (wrong tip) must never be Slack-announced or Published.
        for d in stale:
            did: Any = d.get("id")
            url: str = str(d.get("html_url") or "")
            tc: str = str(d.get("target_commitish") or "")
            print(
                f"stale draft for v{normalize_version_str(version)}: {url} "
                f"(id={did}, target_commitish={tc!r}; tip={tip[:12] if tip else 'unset'})"
            )
            if did is not None:
                delete_release_by_id(int(did), dry_run=dry_run)
        if dry_run:
            print("(dry-run) stale drafts would be deleted")
        else:
            print("deleted stale draft(s)")

    if matching:
        # Tip-matching draft: always reuse — never open a duplicate at the same tip.
        # ``reuse_drafts`` is retained for call-site clarity (Slack re-announce).
        if reuse_drafts:
            print("(reusing tip-matching draft for announce)")
        existing_url: str = str(matching[0].get("html_url") or "")
        if not existing_url:
            raise SystemExit(f"draft(s) exist for v{normalize_version_str(version)} but html_url is missing")
        return existing_url

    if drafts and tip is None and not dry_run:
        draft_urls: str = "\n  ".join(str(r.get("html_url") or "") for r in drafts)
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
    """Copy EOL from the newest published ``vX.Y.*`` release that still has it.

    Walks newest → oldest so a later patch that omitted the line does not
    block drafting. Refuses if none is found (lesson 7: never write TBD).
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
    # Walk newest → oldest: later patch notes sometimes omit the EOL line
    # that the line's first release still carries (e.g. v4.15.7 vs v4.15.0).
    checked: list[str] = []
    for _ver, tag in candidates:
        checked.append(tag)
        body: str = run_out(
            ["gh", "release", "view", tag, "--json", "body", "--jq", ".body"],
            cwd=REPO_ROOT,
        )
        for line in body.splitlines():
            if line.strip().startswith("Estimated end-of-life date"):
                return line.strip()
    raise SystemExit(
        f"no 'Estimated end-of-life date' line found on published "
        f"v{major}.{minor}.* releases ({', '.join(checked)}). Refuse to draft with TBD."
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

    # Step 1 (bump-first): tip pyproject must equal the release version before
    # CI wait, notes, Slack, or draft. On a natural next-patch/RC mismatch,
    # open/reuse the bump PR and WAITING ON — never draft while mismatched.
    # --allow-version-mismatch is dry-run notes preview only.
    ensure_tip_version_before_draft(
        base_branch=target,
        version=version,
        tip_ref=target_sha,
        dry_run=dry_run,
        allow_mismatch=bool(getattr(args, "allow_version_mismatch", False)),
    )

    announce: bool = bool(getattr(args, "slack", False) or getattr(args, "test_dm", False))
    # Always pass tip SHA: reuse only tip-matching drafts; delete stale ones
    # (lesson: wet --slack reused pre-bump draft @ be205bc3 for 4.16.4).
    existing_draft_url: Optional[str] = check_existing_release(
        version,
        dry_run=dry_run,
        reuse_drafts=announce,
        required_tip_sha=target_sha,
    )
    if existing_draft_url is not None:
        print(f"\nExisting draft release: {existing_draft_url}")
        print("(reusing existing draft; not creating a duplicate)")
        print(review_text(version, existing_draft_url))
        if dry_run:
            if announce:
                print_slack_announce_preview(
                    version=version,
                    target_sha=short_sha,
                    draft_url=existing_draft_url,
                    test_dm=bool(args.test_dm),
                    channel=str(getattr(args, "slack_channel", SLACK_CHANNEL_DEFAULT)),
                )
            return 0
        if announce:
            notify_slack(
                version=version,
                target_sha=short_sha,
                draft_url=existing_draft_url,
                test_dm=bool(args.test_dm),
                channel=str(getattr(args, "slack_channel", SLACK_CHANNEL_DEFAULT)),
                precheck=bool(getattr(args, "slack_precheck", False)),
            )
        return 0

    ensure_ci_green_from_args(target_sha, args)

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
        preview_draft_url: str = f"(draft URL for v{version})"
        print(review_text(version, preview_draft_url))
        if announce:
            print_slack_announce_preview(
                version=version,
                target_sha=short_sha,
                draft_url=preview_draft_url,
                test_dm=bool(args.test_dm),
                channel=str(getattr(args, "slack_channel", SLACK_CHANNEL_DEFAULT)),
            )
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
            channel=str(getattr(args, "slack_channel", SLACK_CHANNEL_DEFAULT)),
            precheck=bool(getattr(args, "slack_precheck", False)),
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
            "number,title,url,headRefName,isDraft",
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
        ci_failing=ci_failure_names(failing),
        ci_pending=pending,
    )
    return state


def _is_version_bump_pr_title(title: str) -> bool:
    """True for chore bump / set-version PRs — not fix content in a cut."""
    lowered: str = title.lower().strip()
    if "set version" in lowered:
        return True
    if "bump" in lowered and "version" in lowered:
        return True
    # ``chore: bump 4.17.0rc2`` (no "version" word) from bump-rc / post-release.
    if re.match(r"^(chore:\s*)?bump\s+\d", lowered):
        return True
    return False


def resolve_previous_published_tag(base_branch: str, new_version: str) -> Optional[str]:
    """Tag to diff against for the bump PR ``Included since`` section.

    Prefer the published release matching ``origin/<base_branch>``'s current
    pyproject version (the floor of the cut). Fall back to the newest published
    tag on the same ``X.Y`` line that is strictly older than ``new_version``.
    """
    target: Version = Version(normalize_version_str(new_version))
    try:
        current: str = read_pyproject_version_at(f"origin/{base_branch}")
        current_ver: Version = Version(normalize_version_str(current))
        current_tag: str = f"v{normalize_version_str(current)}"
        if current_ver < target and _release_tag_is_published(current_tag):
            return current_tag
    except (SystemExit, InvalidVersion, subprocess.CalledProcessError):
        pass

    best: Optional[tuple[Version, str]] = None
    for rel in list_published_release_tags():
        if rel.get("isDraft"):
            continue
        raw_tag: str = rel.get("tagName") or ""
        try:
            ver: Version = Version(normalize_version_str(raw_tag))
        except InvalidVersion:
            continue
        if ver.major != target.major or ver.minor != target.minor:
            continue
        if ver >= target:
            continue
        tag: str = raw_tag if raw_tag.startswith("v") else f"v{normalize_version_str(raw_tag)}"
        if best is None or ver > best[0]:
            best = (ver, tag)
    return best[1] if best else None


def _pulls_for_commit(sha: str) -> list[dict[str, Any]]:
    """PRs GitHub associates with ``sha`` (REST ``/commits/{sha}/pulls``)."""
    cp: subprocess.CompletedProcess[str] = subprocess.run(  # nosec B603 B607 — invokes gh CLI, trusted PATH
        [
            "gh",
            "api",
            f"repos/{GH_REPO}/commits/{sha}/pulls",
            "-H",
            "Accept: application/vnd.github+json",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(REPO_ROOT),
    )
    if cp.returncode != 0 or not cp.stdout.strip():
        return []
    try:
        data: Any = json.loads(cp.stdout)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def _pr_numbers_in_subject(subject: str) -> list[int]:
    """``(#12345)`` markers commonly left by merge/backport commits."""
    return [int(m) for m in re.findall(r"\(#(\d+)\)", subject)]


def list_merged_prs_since(prev_tag: str, head_ref: str, *, base_branch: str) -> list[dict[str, Any]]:
    """Merged PRs that land in ``prev_tag..head_ref`` (usually onto ``base_branch``).

    Prefer commit→PR association from the GitHub API; fall back to ``(#N)`` in
    the commit subject. Skips open PRs and version-bump titles so the bump PR
    itself never appears as "included" content.
    """
    log_out: str = run_out(
        ["git", "log", "--format=%H%x00%s", f"{prev_tag}..{head_ref}"],
        cwd=REPO_ROOT,
    )
    found: dict[int, dict[str, Any]] = {}
    for line in log_out.splitlines():
        line = line.strip()
        if not line:
            continue
        if "\x00" in line:
            sha: str
            subject: str
            sha, subject = line.split("\x00", 1)
        else:
            sha, subject = line, ""
        sha = sha.strip()
        if not sha:
            continue

        candidates: list[dict[str, Any]] = []
        for pull in _pulls_for_commit(sha):
            number: Any = pull.get("number")
            if not isinstance(number, int):
                continue
            title: str = pull.get("title") or ""
            merged_at: Optional[str] = pull.get("merged_at")
            base: Any = pull.get("base") or {}
            base_ref: str = base.get("ref") or "" if isinstance(base, dict) else ""
            url: str = pull.get("html_url") or f"https://github.com/{GH_REPO}/pull/{number}"
            candidates.append(
                {
                    "number": number,
                    "title": title,
                    "url": url,
                    "merged_at": merged_at,
                    "base_ref": base_ref,
                }
            )

        if not candidates:
            for num in _pr_numbers_in_subject(subject):
                try:
                    view: dict[str, Any] = gh_pr_view(num)
                except SystemExit:
                    continue
                candidates.append(
                    {
                        "number": int(view.get("number") or num),
                        "title": view.get("title") or "",
                        "url": view.get("url") or f"https://github.com/{GH_REPO}/pull/{num}",
                        "merged_at": view.get("mergedAt"),
                        "base_ref": view.get("baseRefName") or "",
                    }
                )

        for cand in candidates:
            number_i: int = int(cand["number"])
            title_s: str = str(cand.get("title") or "")
            if _is_version_bump_pr_title(title_s):
                continue
            if not cand.get("merged_at"):
                continue
            base_ref_s: str = str(cand.get("base_ref") or "")
            # Prefer PRs merged into the bump base; still accept same-line
            # backports if GitHub omits base (rare) but the subject matched.
            if base_ref_s and base_ref_s != base_branch:
                continue
            if number_i not in found:
                found[number_i] = {
                    "number": number_i,
                    "title": title_s,
                    "url": str(cand.get("url") or f"https://github.com/{GH_REPO}/pull/{number_i}"),
                }

    return [found[n] for n in sorted(found)]


def format_included_since_section(prev_tag: str, prs: list[dict[str, Any]]) -> str:
    """Markdown block listing merged PRs in this cut since ``prev_tag``."""
    lines: list[str] = [f"Included since {prev_tag}:"]
    if not prs:
        lines.append("- (none yet)")
        return "\n".join(lines)
    for pr in prs:
        number: int = int(pr["number"])
        url: str = str(pr.get("url") or f"https://github.com/{GH_REPO}/pull/{number}")
        title: str = str(pr.get("title") or "").strip() or f"#{number}"
        lines.append(f"- [#{number}]({url}): {title}")
    return "\n".join(lines)


def build_version_bump_pr_body(
    *,
    base_branch: str,
    new_version: str,
    body_extra: str = "",
    generated_by_extra: str = "",
    head_ref: Optional[str] = None,
) -> str:
    """Body for a version-bump PR: included PRs since prior tag, italic note footer.

    Title is omitted — it already lives on the PR title field.
    """
    head: str = head_ref or f"origin/{base_branch}"
    parts: list[str] = []
    prev_tag: Optional[str] = resolve_previous_published_tag(base_branch, new_version)
    if prev_tag is not None:
        included: list[dict[str, Any]] = list_merged_prs_since(prev_tag, head, base_branch=base_branch)
        parts.append(format_included_since_section(prev_tag, included))
    if body_extra.strip():
        parts.append(body_extra.strip())
    parts.append(generated_by_pr_line(generated_by_extra))
    return "\n\n".join(parts) + "\n"


def build_version_bump_pr_create_args(
    *,
    base_branch: str,
    head_branch: str,
    title: str,
    pr_body: str,
) -> list[str]:
    """``gh pr create`` argv for a ready-for-review version bump (not ``--draft``)."""
    return [
        "gh",
        "pr",
        "create",
        "--base",
        base_branch,
        "--head",
        head_branch,
        "--label",
        "changelog/no-changelog",
        "--reviewer",
        BUMP_PR_REVIEWER_TEAM,
        "--title",
        title,
        "--body",
        pr_body,
    ]


def open_version_bump_pr(
    *,
    base_branch: str,
    new_version: str,
    title: str,
    dry_run: bool,
    body_extra: str = "",
) -> Optional[str]:
    """Create a ready-for-review version-bump PR. Returns PR URL or None on dry-run.

    Part of the release run (step 1): bump PRs are opened ready (not ``--draft``)
    so CODEOWNERS can review immediately. GitHub *release* drafts stay ``--draft``.
    """
    branch: str = f"bump-version/{new_version}"
    run(["git", "fetch", "origin", base_branch], cwd=REPO_ROOT)
    pr_body: str = build_version_bump_pr_body(
        base_branch=base_branch,
        new_version=new_version,
        body_extra=body_extra,
    )
    if dry_run:
        print(
            f"(dry-run) would open ready-for-review PR {title!r} on base "
            f"{base_branch} via branch {branch} (reviewer {BUMP_PR_REVIEWER_TEAM})"
        )
        print("--- bump PR body ---")
        print(pr_body.rstrip())
        print("--- end bump PR body ---")
        create_preview: list[str] = build_version_bump_pr_create_args(
            base_branch=base_branch,
            head_branch=branch,
            title=title,
            pr_body="<body>",
        )
        print(f"(dry-run) would: {' '.join(create_preview)}")
        return None
    ensure_gh_auth()
    ensure_gh_push_scope()
    ensure_clean_tree()
    run(["git", "switch", base_branch], cwd=REPO_ROOT)
    run(["git", "pull", "--ff-only", "origin", base_branch], cwd=REPO_ROOT)
    # Rebuild after pull so Included-since matches the tip we branch from.
    pr_body = build_version_bump_pr_body(
        base_branch=base_branch,
        new_version=new_version,
        body_extra=body_extra,
    )
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
    pr_url: str = run_out(
        build_version_bump_pr_create_args(
            base_branch=base_branch,
            head_branch=branch,
            title=title,
            pr_body=pr_body,
        ),
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
    fail_names: list[str] = ci_failure_names(failing)
    slo_fail: list[str] = [n for n in fail_names if "slo" in n.lower() or "check-slo-breaches" in n.lower()]
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


def gitlab_tag_pipelines_url(version: str) -> str:
    """GitLab pipelines list filtered to the release tag (publish runs here)."""
    ver: str = normalize_version_str(version)
    return f"https://gitlab.ddbuild.io/{GITLAB_DD_TRACE_PY_PROJECT}/-/pipelines?ref=v{ver}"


def pypi_project_json_url(version: str) -> str:
    """PyPI JSON API URL for a ddtrace version (200 iff published)."""
    ver: str = normalize_version_str(version)
    return f"https://pypi.org/pypi/ddtrace/{ver}/json"


def print_gitlab_watch_list(version: str) -> None:
    ver: str = normalize_version_str(version)
    print(
        f"\nGitLab tag pipeline jobs to watch for v{ver} (glab not required):\n"
        "  - build wheels / tests on the tag pipeline (must be green)\n"
        "  - release_pypi_prod (only runs when the tag pipeline succeeds)\n"
        "  - upload debug symbols / notify jobs\n"
        f"  Pipeline: {gitlab_tag_pipelines_url(ver)}\n"
        "  Branch CI green before drafting is necessary but not sufficient — "
        "PyPI upload happens only after Publish creates the tag and the tag "
        "pipeline passes."
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

    # Step 1 (bump-first): land tip pyproject at the release version before
    # backport wait / CI / draft / Slack. GH PR review is enough for the bump
    # (CODEOWNERS on pyproject.toml); do not Slack @dd-trace-py-releasers here —
    # the channel ping is reserved for the draft-ready announce.
    run(["git", "fetch", "origin", branch], cwd=REPO_ROOT)
    tip_sha: str = run_out(["git", "rev-parse", f"origin/{branch}"], cwd=REPO_ROOT)
    tip_ver: str = normalize_version_str(read_pyproject_version_at(tip_sha))
    if not is_stable_version(tip_ver):
        raise SystemExit(f"origin/{branch} version {tip_ver!r} is not a stable patch version")
    release_version: str
    if _release_tag_is_published(f"v{tip_ver}"):
        release_version = compute_next_patch(tip_ver)
    else:
        release_version = tip_ver
    if xy_branch_for_version(release_version) != branch:
        raise SystemExit(f"version {release_version} does not match target branch {branch}")
    state.version = release_version
    state.sha = tip_sha
    state.did.append(f"origin/{branch} pyproject={tip_ver} sha={tip_sha[:12]}; releasing {release_version}")
    ensure_tip_version_before_draft(
        base_branch=branch,
        version=release_version,
        tip_ref=tip_sha,
        dry_run=dry_run,
        allow_mismatch=False,
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

    # Re-assert bump-first after backports land (tip may have moved).
    ensure_tip_version_before_draft(
        base_branch=branch,
        version=release_version,
        tip_ref=branch_state.sha,
        dry_run=dry_run,
        allow_mismatch=False,
    )
    if branch_state.version != release_version:
        raise SystemExit(
            f"origin/{branch} pyproject is {branch_state.version!r} after bump gate; "
            f"expected release version {release_version!r}"
        )

    ensure_ci_green_from_args(branch_state.sha, args)

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
    ensure_ci_green_from_args(candidate, args)

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
                    channel=str(getattr(args, "slack_channel", SLACK_CHANNEL_DEFAULT)),
                    precheck=bool(getattr(args, "slack_precheck", False)),
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
    print(
        f"waiting on: maintainer publish of v{version}; then tag GitLab pipeline "
        f"+ PyPI (post-release / wait-pypi); then dogfood"
    )
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

    ensure_ci_green_from_args(sha, args)
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
    print(f"waiting on: maintainer publish of v{final_ver}; then tag GitLab pipeline + PyPI (post-release / wait-pypi)")
    if rc == 0:
        raise WaitingOn(f"maintainer publish of draft v{final_ver}")
    return rc


def pypi_has_version(
    version: str,
    *,
    urlopen_fn: Callable[..., Any] = urllib.request.urlopen,
) -> bool:
    """True iff PyPI JSON for ``ddtrace==version`` returns HTTP 200."""
    ver: str = normalize_version_str(version)
    url: str = pypi_project_json_url(ver)
    try:
        req: urllib.request.Request = urllib.request.Request(url, method="GET")
        with urlopen_fn(req, timeout=15) as resp:  # nosec B310 — fixed pypi.org URL
            return int(resp.status) == 200
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise SystemExit(f"PyPI lookup failed for {ver}: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"PyPI lookup failed for {ver}: {exc}") from exc


def format_pypi_wait_failure(version: str) -> str:
    """Human message when PyPI still lacks the version after waiting."""
    ver: str = normalize_version_str(version)
    return (
        f"PyPI still does not list ddtrace=={ver} "
        f"({pypi_project_json_url(ver)}).\n"
        "GitHub Publish only creates the tag; wheels upload in the GitLab "
        f"*tag* pipeline (ref=v{ver}, detached HEAD) via release_pypi_prod "
        "(.is_release + on_success). That pipeline still runs the same-looking "
        "contrib/test jobs as branch CI — a failed molten (or any) test there "
        "skips release_pypi_prod even though it looks like regular CI.\n"
        f"In the GitLab UI: open {gitlab_tag_pipelines_url(ver)} and confirm "
        f"the pipeline Ref is the tag v{ver} (not branch {ver.rsplit('.', 1)[0]}). "
        "Retry failed jobs, wait for release_pypi_prod, then re-run this command. "
        "Branch CI green before drafting is not enough."
    )


def wait_for_pypi_version(
    version: str,
    *,
    timeout_s: float = DEFAULT_PYPI_WAIT_TIMEOUT_S,
    poll_interval_s: float = DEFAULT_PYPI_POLL_INTERVAL_S,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
    has_version_fn: Optional[Callable[[str], bool]] = None,
) -> None:
    """Poll PyPI until ``ddtrace==version`` exists or ``timeout_s`` elapses.

    Source of truth for "release succeeded" is PyPI presence. On timeout raise
    ``WaitingOn`` with the tag-pipeline URL (maintainers Retry failed jobs).
    """
    check: Callable[[str], bool] = has_version_fn if has_version_fn is not None else pypi_has_version
    ver: str = normalize_version_str(version)
    if check(ver):
        print(f"PyPI has ddtrace=={ver}")
        return
    started: float = monotonic_fn()
    deadline: float = started + timeout_s
    poll_n: int = 0
    print(
        f"PyPI does not list ddtrace=={ver} yet; waiting up to {timeout_s:.0f}s "
        f"(poll every {poll_interval_s:.0f}s). Tag pipeline: "
        f"{gitlab_tag_pipelines_url(ver)}"
    )
    while True:
        now: float = monotonic_fn()
        remaining: float = deadline - now
        if remaining <= 0:
            raise WaitingOn(format_pypi_wait_failure(ver))
        sleep_for: float = min(poll_interval_s, remaining)
        poll_n += 1
        elapsed: float = now - started
        print(
            f"WAITING ON PYPI (poll {poll_n}): ddtrace=={ver} not listed yet — "
            f"elapsed {elapsed:.0f}s, next check in {sleep_for:.0f}s "
            f"(timeout {timeout_s:.0f}s)",
            flush=True,
        )
        sleep_fn(sleep_for)
        if check(ver):
            elapsed_ok: float = monotonic_fn() - started
            print(f"PyPI has ddtrace=={ver} after {elapsed_ok:.0f}s ({poll_n} polls)")
            return


def ensure_pypi_version_from_args(version: str, args: argparse.Namespace) -> None:
    """Apply ``--wait-pypi`` / timeout flags; dry-run never polls."""
    dry_run: bool = bool(getattr(args, "dry_run", False))
    wait: Optional[bool] = getattr(args, "wait_pypi", None)
    if dry_run:
        if pypi_has_version(version):
            print(f"(dry-run) PyPI already has ddtrace=={normalize_version_str(version)}")
        else:
            print(
                f"(dry-run) would wait for PyPI ddtrace=={normalize_version_str(version)} "
                f"(tag pipeline {gitlab_tag_pipelines_url(version)})"
            )
            print_gitlab_watch_list(version)
        return
    if wait is False:
        if not pypi_has_version(version):
            print_gitlab_watch_list(version)
            raise WaitingOn(format_pypi_wait_failure(version))
        print(f"PyPI has ddtrace=={normalize_version_str(version)}")
        return
    wait_for_pypi_version(
        version,
        timeout_s=float(getattr(args, "pypi_wait_timeout", DEFAULT_PYPI_WAIT_TIMEOUT_S)),
        poll_interval_s=float(getattr(args, "pypi_poll_interval", DEFAULT_PYPI_POLL_INTERVAL_S)),
    )


def cmd_wait_pypi(args: argparse.Namespace) -> int:
    """Poll until PyPI lists the version (tag-pipeline follow-up)."""
    version: str = normalize_version_str(args.version)
    # Catch the v4.16.4 class of failure early: tag exists but pyproject on the
    # tagged commit still has the previous version → package version red forever.
    ensure_tag_pyproject_matches_version(version)
    if bool(getattr(args, "dry_run", False)):
        ensure_pypi_version_from_args(version, args)
        return 0
    ensure_pypi_version_from_args(version, args)
    print_gitlab_watch_list(version)
    return 0


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

    ensure_tag_pyproject_matches_version(version)

    # Branch CI green ≠ done: Publish creates the tag; GitLab tag pipeline must
    # succeed so release_pypi_prod can upload. Wait for PyPI as the gate.
    ensure_pypi_version_from_args(version, args)
    if not dry_run:
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


def slack_user_id(env_var: str, default_user_id: str) -> str:
    """Slack member ID; env override wins over the hardcoded default."""
    override: str = os.environ.get(env_var, "").strip()
    user_id: str = override or default_user_id
    return user_id


def slack_mention(env_var: str, default_user_id: str) -> str:
    """Build a Slack ``<@ID>`` mention; env override wins over hardcoded default."""
    user_id: str = slack_user_id(env_var, default_user_id)
    return f"<@{user_id}>"


def slack_releasers_subteam_id() -> str:
    """Optional ``S…`` usergroup ID for ``@dd-trace-py-releasers`` (env only)."""
    return os.environ.get(SLACK_RELEASERS_SUBTEAM_ENV, "").strip()


def slack_releasers_mention() -> str:
    """Incoming Webhook mrkdwn mention for ``@dd-trace-py-releasers``.

    With ``DD_SLACK_RELEASERS_SUBTEAM_ID=S…`` → ``<!subteam^S…|handle>`` (notifies).
    Without it → plain ``@handle`` (readable; may not notify on webhooks).
    """
    subteam: str = slack_releasers_subteam_id()
    handle: str = SLACK_RELEASERS_HANDLE
    if subteam:
        return f"<!subteam^{subteam}|{handle}>"
    return f"@{handle}"


def resolve_runner_plain() -> str:
    """Plain GitHub login (or local name) for Workflow text vars — no mrkdwn."""
    login: str = resolve_gh_login()
    if login:
        return login
    display: str = resolve_runner_display()
    linked: Optional[re.Match[str]] = re.fullmatch(
        r"<https://github.com/[^|]+\|([^>]+)>",
        display,
    )
    if linked is not None:
        return linked.group(1)
    if len(display) >= 2 and display.startswith("`") and display.endswith("`"):
        return display[1:-1]
    return display


def _slack_flag_values(args: argparse.Namespace) -> dict[str, Any]:
    """Slack-related Namespace fields for draft_release / notify_slack callers."""
    return {
        "slack": bool(getattr(args, "slack", False)),
        "slack_channel": str(getattr(args, "slack_channel", SLACK_CHANNEL_DEFAULT)),
        "slack_precheck": bool(getattr(args, "slack_precheck", False)),
        "test_dm": bool(getattr(args, "test_dm", False)),
    }


def format_slack_announce(version: str, target_sha: str, draft_url: str) -> str:
    """Build the Slack mrkdwn body for a draft-ready review ping."""
    ver: str = normalize_version_str(version)
    kind: str = draft_kind_phrase(ver)
    releasers: str = slack_releasers_mention()
    return (
        f":package: *Draft {kind}* `{ver}` is ready for review\n"
        f"\n"
        f"<{draft_url}|Open draft release / notes>  ·  target `{target_sha}`\n"
        f"\n"
        f"{releasers} — can I get a :lgtm:? Please review and "
        f"*Publish release* when ready (needs repo-owner perms; the tag is created at publish).\n"
        f"\n"
        f"{auto_created_footer()}"
    )


def format_workflow_trigger_payload(
    *,
    version: str,
    target_sha: str,
    draft_url: str,
    channel: str = SLACK_CHANNEL_DEFAULT,
) -> dict[str, str]:
    """Flat string fields for a Workflow Builder ``triggers/`` webhook.

    Workflow "Send a message" cannot render freeform mrkdwn in a text
    variable. Rebuild the Workflow to insert these keys; ping reviewers with
    a **static** ``@dd-trace-py-releasers`` User Group mention in the editor
    (do not send User ID vars for Brett/Emmett). See
    ``scripts/gh/SLACK_WORKFLOW_INTERIM.md``.
    """
    ver: str = normalize_version_str(version)
    kind: str = draft_kind_phrase(ver)
    channel_id: str = SLACK_CHANNEL_PROD_ID
    if channel == "guild":
        channel_id = SLACK_CHANNEL_GUILD_ID
    return {
        "version": ver,
        "kind": kind,
        "draft_url": draft_url,
        "target_sha": target_sha,
        # Handle only — Workflow should use a static User Group mention for
        # a real ping; inserting this text field alone does not notify.
        "releasers_handle": SLACK_RELEASERS_HANDLE,
        "runner": resolve_runner_plain(),
        "channel_id": channel_id,
        # Footer pieces so Workflow can italicize the "Brought to you by" note
        # without stuffing mrkdwn into one text variable.
        "script": _RELEASE_SCRIPT_PATH,
        "script_branch": _RELEASE_SCRIPT_BRANCH_NAME,
        "script_branch_url": _RELEASE_SCRIPT_BRANCH_URL,
        "commands_hint": "patch / rc / final",
    }


def _slack_announce_destination(*, test_dm: bool, channel: str) -> str:
    """Human-readable destination for dry-run Slack previews (no secrets)."""
    if test_dm:
        return f"DM ({SLACK_DM_ENV})"
    if channel == "prod":
        return f"#{SLACK_CHANNEL_PROD_NAME}"
    if channel == "guild":
        return f"#{SLACK_CHANNEL_GUILD_NAME}"
    if channel == "test":
        return f"test channel ({SLACK_CHANNEL_TEST_ENV})"
    return f"channel={channel}"


def _preview_webhook_url(*, test_dm: bool, channel: str) -> str:
    """Env-only webhook URL for dry-run path selection (no SSM, no secrets printed)."""
    if test_dm:
        url: str
        url, _ = _env_url(SLACK_DM_ENV)
        return url
    if channel == "prod":
        prod_url: str
        prod_url, _ = _env_url(SLACK_CHANNEL_PROD_ENV, SLACK_CHANNEL_PROD_ENV_LEGACY)
        return prod_url
    if channel == "guild":
        guild_url: str
        guild_url, _ = _env_url(SLACK_CHANNEL_GUILD_ENV)
        return guild_url
    if channel == "test":
        return os.environ.get(SLACK_CHANNEL_TEST_ENV, "").strip()
    # auto: same env order as resolve_channel_webhook, without SSM.
    auto_prod: str
    auto_prod, _ = _env_url(SLACK_CHANNEL_PROD_ENV, SLACK_CHANNEL_PROD_ENV_LEGACY)
    if auto_prod:
        return auto_prod
    test_url: str = os.environ.get(SLACK_CHANNEL_TEST_ENV, "").strip()
    if test_url:
        return test_url
    auto_guild: str
    auto_guild, _ = _env_url(SLACK_CHANNEL_GUILD_ENV)
    if auto_guild:
        return auto_guild
    legacy: str
    legacy, _ = _env_url(SLACK_CHANNEL_ENV)
    return legacy


def format_workflow_rendered_mock(fields: dict[str, str]) -> str:
    """Human mock of the rebuilt Workflow "Send a message" body.

    Mirrors ``scripts/gh/SLACK_WORKFLOW_INTERIM.md`` (static bold/italic in the
    Workflow editor + inserted variables). Not posted — dry-run preview only.
    Use ``:lgtm:`` (not ``:+1:`` / thumbs-up) in the Workflow template.
    """
    handle: str = fields.get("releasers_handle", SLACK_RELEASERS_HANDLE)
    return (
        f":package: *Draft* {fields['kind']} {fields['version']} is ready for review\n"
        f"\n"
        f"{fields['draft_url']}  ·  target {fields['target_sha']}\n"
        f"\n"
        f"@{handle} — can I get a :lgtm:? Please review and "
        f"*Publish release* when ready.\n"
        f"\n"
        f"_Brought to you by release automation — {fields['script']} on "
        f"{fields['script_branch_url']} / {fields['script_branch']}. "
        f"Run by {fields['runner']}. Try {fields['commands_hint']}_."
    )


def print_slack_announce_preview(
    *,
    version: str,
    target_sha: str,
    draft_url: str,
    test_dm: bool,
    channel: str,
) -> None:
    """Print the Slack preview that matches the configured webhook URL type.

    ``hooks.slack.com/triggers/`` (or ``DDTRACE_RELEASE_SLACK_WORKFLOW_STRUCTURED``):
    structured Workflow JSON + channel-rendered mock — not freeform mrkdwn
    (literals on unrebuilt / wrong templates). ``hooks.slack.com/services/``:
    Incoming Webhook mrkdwn. See ``scripts/gh/SLACK_WORKFLOW_INTERIM.md``.
    """
    dest: str = _slack_announce_destination(test_dm=test_dm, channel=channel)
    url: str = _preview_webhook_url(test_dm=test_dm, channel=channel)
    print(f"\n(dry-run) would post to {dest}:")

    if url and use_workflow_structured_payload(url):
        workflow_payload: dict[str, str] = format_workflow_trigger_payload(
            version=version,
            target_sha=target_sha,
            draft_url=draft_url,
            channel=channel,
        )
        print("----- WORKFLOW TRIGGER JSON (hooks.slack.com/triggers/) -----")
        print(json.dumps(workflow_payload, indent=2, sort_keys=True))
        print("----- END WORKFLOW TRIGGER JSON -----")
        print("----- WORKFLOW-RENDERED MOCK (after rebuilt template) -----")
        print(format_workflow_rendered_mock(workflow_payload))
        print("----- END WORKFLOW-RENDERED MOCK -----")
        print(
            "warning: structured Workflow JSON only renders correctly after the "
            "Slack Workflow is rebuilt for these variable names "
            "(scripts/gh/SLACK_WORKFLOW_INTERIM.md). Until then, dry-run only — "
            "do not wet-post to an unrebuilt triggers/ Workflow.",
            file=sys.stderr,
        )
        return

    text: str = format_slack_announce(version, target_sha, draft_url)
    if url and is_incoming_webhook_url(url):
        print("----- SLACK MESSAGE (Incoming Webhook mrkdwn) -----")
    elif not url:
        print(
            "----- SLACK MESSAGE (Incoming Webhook mrkdwn; no webhook URL in env — defaulting to services/ shape) -----"
        )
    else:
        print("----- SLACK MESSAGE (webhook text / mrkdwn) -----")
    print(text)
    print("----- END SLACK MESSAGE -----")


def is_workflow_trigger_url(url: str) -> bool:
    """True for Workflow Builder webhook triggers (hooks.slack.com/triggers/...)."""
    return "hooks.slack.com/triggers/" in url


def is_incoming_webhook_url(url: str) -> bool:
    """True for classic Incoming Webhooks (hooks.slack.com/services/...)."""
    return "hooks.slack.com/services/" in url


def use_workflow_structured_payload(url: str) -> bool:
    """True when we should POST flat Workflow fields instead of mrkdwn text."""
    if is_workflow_trigger_url(url):
        return True
    force: str = os.environ.get(SLACK_WORKFLOW_STRUCTURED_ENV, "").strip()
    return force in ("1", "true", "yes")


def refuse_freeform_mrkdwn_on_workflow_trigger(url: str) -> None:
    """Exit if posting freeform mrkdwn ``{"text":...}`` to a triggers/ URL.

    That path shows literal asterisks and angle brackets. Use structured
    fields via ``post_workflow_trigger`` (auto for triggers/) or an Incoming
    Webhook (services/).
    """
    if not is_workflow_trigger_url(url):
        return
    raise SystemExit(
        "Refusing freeform mrkdwn on a Workflow Builder triggers/ URL "
        "(hooks.slack.com/triggers/...). Use structured Workflow fields "
        "(automatic when notify_slack sees triggers/) or an Incoming Webhook "
        f"(hooks.slack.com/services/...) for #{SLACK_CHANNEL_PROD_NAME}. "
        f"See scripts/gh/SLACK_WORKFLOW_INTERIM.md."
    )


def _slack_http_post_json(url: str, payload: dict[str, Any]) -> None:
    """POST JSON to a Slack webhook URL; accept classic ``ok`` or JSON ``ok: true``."""
    body_bytes: bytes = json.dumps(payload).encode("utf-8")
    req: urllib.request.Request = urllib.request.Request(
        url,
        data=body_bytes,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 — Slack webhook URL from env/SSM
        body: str = resp.read().decode("utf-8", errors="replace").strip()
        ok: bool = body.lower() == "ok"
        if not ok:
            try:
                ok = bool(json.loads(body).get("ok"))
            except (ValueError, AttributeError):
                ok = False
        if resp.status >= 300 or not ok:
            raise SystemExit(f"slack webhook returned {resp.status}: {body}")


def post_webhook(url: str, text: str) -> None:
    """POST Incoming Webhook-style ``{"text": mrkdwn}``. Refuses triggers/."""
    refuse_freeform_mrkdwn_on_workflow_trigger(url)
    _slack_http_post_json(url, {"text": text})


def post_workflow_trigger(url: str, fields: dict[str, str]) -> None:
    """POST flat string fields to a Workflow Builder ``triggers/`` URL."""
    if not fields:
        raise SystemExit("workflow trigger payload is empty")
    _slack_http_post_json(url, dict(fields))


def load_local_env(path: Path = LOCAL_ENV_FILE) -> None:
    """Load KEY=VAL lines from a gitignored local file into ``os.environ``.

    Missing file is fine. Blank lines and ``#`` comments are skipped. Optional
    ``export `` prefix is accepted. Already-set non-empty env vars win so an
    explicit export still overrides the file. Values may be single- or
    double-quoted.
    """
    if not path.is_file():
        return
    text: str = path.read_text(encoding="utf-8")
    for raw_line in text.splitlines():
        line: str = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key: str
        value: str
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if not key:
            continue
        existing: str = os.environ.get(key, "").strip()
        if existing:
            continue
        os.environ[key] = value


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

    ``channel`` is one of "prod", "guild", "test", or "auto". "auto" only
    consults env vars (prod → test → guild → legacy) and refuses to silently
    hit SSM — picking a specific channel for SSM should be explicit.
    """
    prod_url: str
    prod_env: str
    prod_url, prod_env = _env_url(SLACK_CHANNEL_PROD_ENV, SLACK_CHANNEL_PROD_ENV_LEGACY)
    guild_url: str
    guild_env: str
    guild_url, guild_env = _env_url(SLACK_CHANNEL_GUILD_ENV)
    test_url: str = os.environ.get(SLACK_CHANNEL_TEST_ENV, "").strip()
    legacy_url: str = os.environ.get(SLACK_CHANNEL_ENV, "").strip()

    if channel == "prod":
        if prod_url:
            return prod_url, f"{prod_env} env var"
        try:
            return (
                fetch_ssm_parameter(SSM_SLACK_WEBHOOK_PROD),
                f"AWS SSM ({SSM_SLACK_WEBHOOK_PROD})",
            )
        except SystemExit as exc:
            raise SystemExit(
                f"--slack-channel=prod needs {SLACK_CHANNEL_PROD_ENV} "
                f"(or legacy {SLACK_CHANNEL_PROD_ENV_LEGACY}) or SSM "
                f"{SSM_SLACK_WEBHOOK_PROD} (webhook posting to "
                f"#{SLACK_CHANNEL_PROD_NAME} / <#{SLACK_CHANNEL_PROD_ID}>). "
                "Create a Slack Incoming Webhook (hooks.slack.com/services/...) "
                f"for #{SLACK_CHANNEL_PROD_NAME}, then export the URL or store it "
                f"in SSM. Do not use Workflow Builder triggers/ URLs (broken "
                f"mrkdwn). Underlying error: {exc}"
            ) from exc
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
                f"--slack-channel=guild needs {SLACK_CHANNEL_GUILD_ENV} or SSM "
                f"{SSM_SLACK_WEBHOOK_GUILD} (webhook posting to "
                f"#{SLACK_CHANNEL_GUILD_NAME} / <#{SLACK_CHANNEL_GUILD_ID}>). "
                "Create a Slack Incoming Webhook (hooks.slack.com/services/...) "
                f"for #{SLACK_CHANNEL_GUILD_NAME}, then export the URL or store it "
                f"in SSM. Do not use Workflow Builder triggers/ URLs (broken "
                f"mrkdwn). Underlying error: {exc}"
            ) from exc
    if channel == "test":
        if test_url:
            return test_url, f"{SLACK_CHANNEL_TEST_ENV} env var"
        return fetch_ssm_parameter(SSM_SLACK_WEBHOOK_TEST), f"AWS SSM ({SSM_SLACK_WEBHOOK_TEST})"
    # auto: env vars only; require explicit --slack-channel for SSM.
    if prod_url:
        return prod_url, f"{prod_env} env var"
    if test_url:
        return test_url, f"{SLACK_CHANNEL_TEST_ENV} env var"
    if guild_url:
        return guild_url, f"{guild_env} env var"
    if legacy_url:
        return legacy_url, f"{SLACK_CHANNEL_ENV} env var"
    raise SystemExit(
        "--slack with --slack-channel=auto requires one of "
        f"{SLACK_CHANNEL_PROD_ENV}, {SLACK_CHANNEL_TEST_ENV}, "
        f"{SLACK_CHANNEL_GUILD_ENV}, or {SLACK_CHANNEL_ENV} to be set. "
        "Pass --slack-channel=prod|guild|test to fetch from AWS SSM instead "
        f"(default for review pings → #{SLACK_CHANNEL_PROD_NAME})."
    )


def _post_slack_announce(
    url: str,
    *,
    version: str,
    target_sha: str,
    draft_url: str,
    channel: str,
    text_prefix: str = "",
) -> str:
    """Post announce to ``url``; return a short human label for the transport used."""
    text: str = format_slack_announce(version, target_sha, draft_url)
    if text_prefix:
        text = f"{text_prefix}{text}"
    if use_workflow_structured_payload(url):
        fields: dict[str, str] = format_workflow_trigger_payload(
            version=version,
            target_sha=target_sha,
            draft_url=draft_url,
            channel=channel,
        )
        if text_prefix:
            fields = dict(fields)
            fields["kind"] = f"{text_prefix.strip()} {fields['kind']}".strip()
        post_workflow_trigger(url, fields)
        return "Workflow trigger (structured fields)"
    post_webhook(url, text)
    if is_incoming_webhook_url(url):
        return "Incoming Webhook (mrkdwn)"
    return "webhook text"


def notify_slack(
    version: str, target_sha: str, draft_url: str, test_dm: bool, channel: str, precheck: bool = False
) -> None:
    dm_url: str
    dm_env: str
    dm_url, dm_env = _env_url(SLACK_DM_ENV)

    if test_dm:
        if not dm_url:
            raise SystemExit(
                f"--test-dm requires {SLACK_DM_ENV} to be set "
                "(secret DM webhook; not committed — export it locally or "
                "add an SSM path later)"
            )
        if precheck:
            if use_workflow_structured_payload(dm_url):
                ping_fields: dict[str, str] = format_workflow_trigger_payload(
                    version=version,
                    target_sha=target_sha,
                    draft_url=draft_url,
                    channel=channel,
                )
                ping_fields["kind"] = "connectivity check"
                post_workflow_trigger(dm_url, ping_fields)
            else:
                post_webhook(dm_url, "[ping] release-script connectivity check (DM)")
        how: str = _post_slack_announce(
            dm_url,
            version=version,
            target_sha=target_sha,
            draft_url=draft_url,
            channel=channel,
            text_prefix="[TEST] ",
        )
        print(f"sent test DM via {dm_env} ({how})")
        return

    channel_url: str
    channel_env: str
    channel_url, channel_env = resolve_channel_webhook(channel)
    if precheck:
        # Optional connectivity ping (off by default). Useful when debugging a
        # new webhook; keep off for normal announces to avoid dual posts.
        if use_workflow_structured_payload(channel_url):
            ping: dict[str, str] = format_workflow_trigger_payload(
                version=version,
                target_sha=target_sha,
                draft_url=draft_url,
                channel=channel,
            )
            ping["kind"] = "connectivity check"
            post_workflow_trigger(channel_url, ping)
        else:
            post_webhook(channel_url, "[ping] release-script connectivity check")
    how_channel: str = _post_slack_announce(
        channel_url,
        version=version,
        target_sha=target_sha,
        draft_url=draft_url,
        channel=channel,
    )
    print(f"posted to release channel via {channel_env}")
    print(f"({how_channel})")
    if use_workflow_structured_payload(channel_url):
        print(
            "warning: Workflow must map structured fields "
            "(scripts/gh/SLACK_WORKFLOW_INTERIM.md); unrebuilt workflows "
            "still look wrong."
        )
    if dm_url:
        how_dm: str = _post_slack_announce(
            dm_url,
            version=version,
            target_sha=target_sha,
            draft_url=draft_url,
            channel=channel,
            text_prefix="(runner copy) ",
        )
        print(f"sent runner-copy DM via {dm_env} ({how_dm})")


# --------------------------------------------------------------------------- #
# argparse
# --------------------------------------------------------------------------- #


def _add_common_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--verbose",
        action="store_true",
        help="echo stderr from external tools (reno, pandoc, gh) and print tool versions at start",
    )


def _add_pypi_wait_flags(sp: argparse.ArgumentParser) -> None:
    """PyPI presence poll (post-release / wait-pypi). Dry-run never waits."""
    wait_g: Any = sp.add_mutually_exclusive_group()
    wait_g.add_argument(
        "--wait-pypi",
        dest="wait_pypi",
        action="store_true",
        default=None,
        help=(
            "poll PyPI until ddtrace==version exists (default for wet "
            "post-release / wait-pypi; ignored by --dry-run so dry-run never hangs)"
        ),
    )
    wait_g.add_argument(
        "--no-wait-pypi",
        dest="wait_pypi",
        action="store_false",
        help="do not poll; exit WAITING ON immediately when PyPI lacks the version",
    )
    sp.add_argument(
        "--pypi-wait-timeout",
        type=float,
        default=DEFAULT_PYPI_WAIT_TIMEOUT_S,
        metavar="SECONDS",
        help=(
            f"max seconds to wait for PyPI when waiting "
            f"(default: {int(DEFAULT_PYPI_WAIT_TIMEOUT_S)} = "
            f"{int(DEFAULT_PYPI_WAIT_TIMEOUT_S) // 60}m)"
        ),
    )
    sp.add_argument(
        "--pypi-poll-interval",
        type=float,
        default=DEFAULT_PYPI_POLL_INTERVAL_S,
        metavar="SECONDS",
        help=f"seconds between PyPI polls (default: {int(DEFAULT_PYPI_POLL_INTERVAL_S)})",
    )


def _add_ci_flags(sp: argparse.ArgumentParser) -> None:
    """CI-green preflight + optional wait/poll (patch / rc / final / draft-release)."""
    sp.add_argument(
        "--allow-ci-failure",
        action="store_true",
        help=(
            "downgrade the CI-green pre-flight to a warning. Use sparingly; the "
            "default behavior refuses to draft on a commit with red or pending CI."
        ),
    )
    wait_g: Any = sp.add_mutually_exclusive_group()
    wait_g.add_argument(
        "--wait-ci",
        dest="wait_ci",
        action="store_true",
        default=None,
        help=(
            "poll GitHub until CI is green before drafting (default for wet runs; "
            "ignored by --dry-run so dry-run never hangs)"
        ),
    )
    wait_g.add_argument(
        "--no-wait-ci",
        dest="wait_ci",
        action="store_false",
        help="do not poll; exit WAITING ON immediately when CI is still pending",
    )
    sp.add_argument(
        "--ci-wait-timeout",
        type=float,
        default=DEFAULT_CI_WAIT_TIMEOUT_S,
        metavar="SECONDS",
        help=(
            f"max seconds to wait for CI when waiting "
            f"(default: {int(DEFAULT_CI_WAIT_TIMEOUT_S)} = "
            f"{int(DEFAULT_CI_WAIT_TIMEOUT_S) // 60}m)"
        ),
    )
    sp.add_argument(
        "--ci-poll-interval",
        type=float,
        default=DEFAULT_CI_POLL_INTERVAL_S,
        metavar="SECONDS",
        help=f"seconds between CI status polls (default: {int(DEFAULT_CI_POLL_INTERVAL_S)})",
    )


def _add_slack_flags(sp: argparse.ArgumentParser) -> None:
    sp.add_argument(
        "--slack",
        action="store_true",
        help=(
            f"post a review ping to the channel selected by --slack-channel "
            f"(default: prod → #{SLACK_CHANNEL_PROD_NAME})"
        ),
    )
    sp.add_argument(
        "--slack-channel",
        choices=["prod", "guild", "test", "auto"],
        default=SLACK_CHANNEL_DEFAULT,
        help=(
            "which channel webhook to use: prod reads "
            f"{SLACK_CHANNEL_PROD_ENV} (#{SLACK_CHANNEL_PROD_NAME}), "
            f"guild reads {SLACK_CHANNEL_GUILD_ENV} (#{SLACK_CHANNEL_GUILD_NAME}; "
            f"optional escape hatch), test reads {SLACK_CHANNEL_TEST_ENV}, "
            f"auto prefers prod then test then guild then the legacy "
            f"{SLACK_CHANNEL_ENV} (default: {SLACK_CHANNEL_DEFAULT})"
        ),
    )
    sp.add_argument(
        "--slack-precheck",
        dest="slack_precheck",
        action="store_true",
        default=False,
        help=(
            "also post a short connectivity ping before the real announcement (default: off — one channel message only)"
        ),
    )
    sp.add_argument(
        "--no-slack-precheck",
        dest="slack_precheck",
        action="store_false",
        help="explicit no-op: precheck is already off by default",
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
    _add_ci_flags(patch_p)
    patch_p.add_argument("--dry-run", action="store_true")
    _add_slack_flags(patch_p)
    _add_common_flags(patch_p)
    patch_p.set_defaults(func=cmd_patch)

    rc_p: argparse.ArgumentParser = sub.add_parser("rc", help="resumable: cut the next RC draft")
    rc_p.add_argument("--candidate", help="SHA to release (default: origin/main HEAD)")
    _add_ci_flags(rc_p)
    rc_p.add_argument("--dry-run", action="store_true")
    _add_slack_flags(rc_p)
    _add_common_flags(rc_p)
    rc_p.set_defaults(func=cmd_rc)

    final_p: argparse.ArgumentParser = sub.add_parser("final", help="resumable: ship X.Y.0 from the last RC")
    final_p.add_argument("xy", help="release line like 4.17")
    _add_ci_flags(final_p)
    final_p.add_argument("--dry-run", action="store_true")
    _add_slack_flags(final_p)
    _add_common_flags(final_p)
    final_p.set_defaults(func=cmd_final)

    post_p: argparse.ArgumentParser = sub.add_parser(
        "post-release",
        help="after publish: wait for PyPI (tag pipeline) + open the next bump PR",
    )
    post_p.add_argument("version", help="published version (vX.Y.Z or X.Y.Z)")
    post_p.add_argument("--dry-run", action="store_true")
    _add_pypi_wait_flags(post_p)
    _add_common_flags(post_p)
    post_p.set_defaults(func=cmd_post_release)

    wait_pypi_p: argparse.ArgumentParser = sub.add_parser(
        "wait-pypi",
        help="poll until PyPI lists ddtrace==version (GitLab tag-pipeline gate)",
    )
    wait_pypi_p.add_argument("version", help="published version (vX.Y.Z or X.Y.Z)")
    wait_pypi_p.add_argument("--dry-run", action="store_true")
    _add_pypi_wait_flags(wait_pypi_p)
    _add_common_flags(wait_pypi_p)
    wait_pypi_p.set_defaults(func=cmd_wait_pypi)

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
            "dry-run only: warn instead of bump-first/refusing when --version "
            "disagrees with pyproject at --target (notes preview). Wet path "
            "always bump-first or refuses — never drafts on a mismatch."
        ),
    )
    _add_ci_flags(dr)
    _add_slack_flags(dr)
    dr.add_argument("--dry-run", action="store_true")
    _add_common_flags(dr)
    dr.set_defaults(func=cmd_draft_release)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    global _VERBOSE
    # Optional gitignored secrets (Slack webhooks) so --slack works without
    # re-exporting each session. Explicit env still wins over the file.
    load_local_env()
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
