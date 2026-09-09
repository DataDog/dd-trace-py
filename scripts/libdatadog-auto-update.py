#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""Update the pinned libdatadog dependency in src/native to the target
revision of libdatadog, or the latest from main if unspecified.

The design mirrors dd-trace-php's libdatadog-latest job: the bump itself is
cheap and deterministic, and *validation is delegated to the real CI
pipeline* rather than re-run inside this job.

Phases:
  1. Resolve target.
  2. Apply bump (Cargo.toml revs, Cargo.lock refresh, release note).
  3. Push the bump to the target branch as a GitHub-signed commit. The push
     triggers the full, parallelized dd-trace-py pipeline for that branch.
  4. Wait for that pipeline via the GitLab API, collect failed jobs (with a
     single retry round to filter flaky tests), and hand their traces to
     Claude. Claude repairs the code from the traces alone — it cannot run
     cargo — and an independent agent gates that the changes are mechanical
     before the repair commit is pushed and the loop repeats.

The script is intentionally runnable locally: with no GH token it stops after
printing the changes (`--dry-run` touches nothing), and outside CI it skips
the pipeline wait entirely.

Usage:
  scripts/libdatadog-auto-update.py                        # bump, push, validate via pipeline
  scripts/libdatadog-auto-update.py --dry-run              # show what would change, touch nothing
  scripts/libdatadog-auto-update.py --target-rev SHA       # bump to a specific rev
  scripts/libdatadog-auto-update.py --target-branch foo    # push to foo (create/force-update)
  scripts/libdatadog-auto-update.py --no-push              # apply the bump, list changes, don't push

Environment:
  GH_TOKEN              GitHub token (from octo-sts). When set, the bump is
                        committed via the GitHub API as a verified,
                        bot-attributed commit and pushed to the target branch.
                        Without it, the changes are listed and nothing is
                        pushed.
  ANTHROPIC_AUTH_TOKEN  AI-gateway bearer token. Required for the repair and
  ANTHROPIC_BASE_URL    mechanical-changes validation agents. If unset, both
                        are skipped and a red pipeline fails the run.
  AUTHANYWHERE_BIN      Path to the authanywhere binary (falls back to PATH).
                        Used in CI to mint the short-lived BTI JWT that
                        exchanges for a GitLab API token (pipeline polling).
  CI_API_V4_URL,        GitLab CI env; presence selects the "wait for the
  CI_PROJECT_ID         pushed branch's pipeline" phase.
  ARTIFACTS_DIR         where to write logs and the summary (default: ./libdatadog-auto-update)

Prompts live next to the job (not in this script):
  .gitlab/libdatadog-auto-update-repair-prompt.md   — instructions for the repair agent
  .gitlab/libdatadog-auto-update-review-prompt.md   — instructions for the mechanical-review agent
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


LIBDATADOG_REPO = "https://github.com/DataDog/libdatadog"
GH_REPO_SLUG = "DataDog/dd-trace-py"
GH_API_BASE = "https://api.github.com"
LIBDATADOG_GH_SLUG = "DataDog/libdatadog"
LIBDATADOG_TARBALL_URL = "https://codeload.github.com/DataDog/libdatadog/tar.gz"

# BTI API used to exchange an authanywhere JWT (audience rapid-devex-ci, same
# as .gitlab/scripts/summarize_failures.py) for a short-lived GitLab API token.
BTI_BASE_URL = "https://bti-ci-api.us1.ddbuild.io/internal/ci"
BTI_AUDIENCE = "rapid-devex-ci"

# Second line of defense: refuse to commit a path that looks like a
# secret/credential/binary. If this ever fires it means something unexpected
# landed in the working tree.
SENSITIVE_PATH_RE = re.compile(
    r"(token|secret|credential|authanywhere|password|\.pem$|\.key$|id_rsa|\.env$|\.netrc)",
    re.IGNORECASE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIVE_DIR = REPO_ROOT / "src" / "native"
CARGO_TOML = NATIVE_DIR / "Cargo.toml"
RELEASENOTES_DIR = REPO_ROOT / "releasenotes" / "notes"
PROMPTS_DIR = REPO_ROOT / ".gitlab"
REPAIR_PROMPT_FILE = PROMPTS_DIR / "libdatadog-auto-update-repair-prompt.md"
REVIEW_PROMPT_FILE = PROMPTS_DIR / "libdatadog-auto-update-review-prompt.md"

# Claude model + tools. The repair agent mirrors dd-trace-php: no Bash at all,
# so Claude cannot run cargo or any other build/test command — it reasons from
# the CI failure traces and the libdatadog source alone. The reviewer gets
# read-only git access to inspect the change set. Hard cap per agent call so a
# hung/slow agent can't block the job; the job's own timeout is the outer
# backstop.
CLAUDE_TIMEOUT_S = 600
CLAUDE_MAX_TURNS = 50
CLAUDE_MODEL = "anthropic/claude-sonnet-4-6"
CLAUDE_REPAIR_TOOLS = ["Read", "Glob", "Grep", "Edit", "Write"]
CLAUDE_REVIEW_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Bash(git status:*)",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(cat:*)",
    "Bash(ls:*)",
]

# GitLab pipeline polling (mirrors the dd-trace-php job's polling cadence).
PIPELINE_POLL_INTERVAL_S = 30
PIPELINE_APPEAR_TIMEOUT_S = 20 * 60  # GitHub→GitLab mirror latency before a new branch's pipeline shows up
PIPELINE_STATUS_TIMEOUT_DEFAULT_S = 2 * 60 * 60  # per pipeline run
PIPELINE_TERMINAL_STATUSES = frozenset({"success", "failed", "canceled", "skipped"})
JOB_RUNNING_STATUSES = frozenset({"running", "pending", "created", "waiting_for_resource", "preparing"})
RETRY_FAILURES_MAX = 10  # retry flaky failed jobs only when there are fewer than this
RETRY_WAIT_TIMEOUT_S = 60 * 60
TRACE_TAIL_BYTES = 16 * 1024  # keep the tail of each failed job's trace, like the PHP job's ~15 KiB

# Matches the `rev = "..."` on any Cargo.toml line that pins the libdatadog git
# source. Every git dependency in src/native/Cargo.toml is libdatadog, but we
# anchor on the URL anyway so this stays correct if other git deps are added.
_LIBDD_REV_RE = re.compile(r'(git\s*=\s*"https://github\.com/DataDog/libdatadog"[^\n]*?\brev\s*=\s*")([^"]+)(")')


class StepError(RuntimeError):
    """A validation/build step failed; carries the captured output for diagnosis."""

    def __init__(self, label: str, returncode: int, output: str) -> None:
        super().__init__(f"step {label!r} failed (exit {returncode})")
        self.label = label
        self.returncode = returncode
        self.output = output


class PipelineError(RuntimeError):
    """The triggered pipeline could not be found, waited for, or inspected."""


@dataclasses.dataclass
class RunResult:
    label: str
    returncode: int
    output: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(
    label: str, cmd: list[str], *, cwd: Path | None = None, check: bool = True, timeout: float | None = None
) -> RunResult:
    """Run a command, streaming its output live and capturing it for the caller.

    Output is streamed line-by-line (so long-running steps don't look frozen in
    CI) while also being captured into the result. If `timeout` is set, the
    process is killed after that many seconds and the step is reported as
    failed (rc 124).
    """
    print(f"\n$ {' '.join(cmd)}", flush=True)
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    timed_out = False
    timer: threading.Timer | None = None
    if timeout:

        def _kill() -> None:
            nonlocal timed_out
            timed_out = True
            proc.kill()

        timer = threading.Timer(timeout, _kill)
        timer.start()

    lines: list[str] = []
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            lines.append(line)
    finally:
        proc.wait()
        if timer:
            timer.cancel()

    output = "".join(lines)
    returncode = proc.returncode
    if timed_out:
        msg = f"\n[timeout] '{label}' exceeded {timeout:.0f}s and was killed."
        print(msg, flush=True)
        output += msg + "\n"
        returncode = 124

    result = RunResult(label=label, returncode=returncode, output=output)
    if check and not result.ok:
        raise StepError(label, returncode, output)
    return result


def capture(cmd: list[str], *, timeout: float = 60) -> str:
    """Run a command and return its stdout WITHOUT printing it.

    For commands whose output contains secrets (e.g. authanywhere JWTs) —
    `run()` would leak them into the CI log.
    """
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"command failed (exit {proc.returncode}): {' '.join(cmd)}\n{proc.stderr}")
    return proc.stdout.strip()


# --------------------------------------------------------------------------- #
# Phase 1 — resolve target
# --------------------------------------------------------------------------- #
def resolve_latest_main_sha() -> str:
    """Return the current commit SHA of libdatadog's main branch."""
    out = run(
        "ls-remote",
        ["git", "ls-remote", LIBDATADOG_REPO, "refs/heads/main"],
    ).output.strip()
    if not out:
        raise RuntimeError(f"git ls-remote returned nothing for {LIBDATADOG_REPO} main")
    sha = out.split()[0]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError(f"unexpected SHA from ls-remote: {sha!r}")
    return sha


def current_pinned_revs() -> set[str]:
    text = CARGO_TOML.read_text()
    return {m.group(2) for m in _LIBDD_REV_RE.finditer(text)}


# --------------------------------------------------------------------------- #
# Phase 2 — apply the bump
# --------------------------------------------------------------------------- #
def rewrite_cargo_toml(target_rev: str) -> int:
    """Point every libdatadog rev at target_rev. Returns the number of lines changed."""
    text = CARGO_TOML.read_text()
    new_text, n = _LIBDD_REV_RE.subn(rf"\g<1>{target_rev}\g<3>", text)
    if n:
        CARGO_TOML.write_text(new_text)
    return n


def regenerate_lockfile() -> None:
    """Best-effort refresh of Cargo.lock for the changed git revs.

    Running `cargo update` against each libdatadog package re-resolves only the
    libdatadog git source; registry deps are left untouched, keeping the lock
    diff minimal in the happy path.

    Tolerant by design: if the new rev renamed/removed a crate we depend on,
    this `cargo update` fails to resolve. We do NOT crash here — the same
    resolution error resurfaces in the triggered pipeline's cargo build, whose
    traces are then handed to the repair agent.
    """
    if not shutil.which("cargo"):
        print("\n[cargo-update] cargo not found; skipping lock refresh (the pipeline will surface any lock drift).")
        return
    pkgs = _libdatadog_package_names()
    cmd = ["cargo", "update", "--manifest-path", str(CARGO_TOML)]
    for pkg in pkgs:
        cmd += ["-p", pkg]
    res = run("cargo-update", cmd, cwd=NATIVE_DIR, check=False)
    if not res.ok:
        print(
            "\n[cargo-update] lock refresh failed (likely a renamed/removed crate at the new rev); "
            "deferring to the triggered pipeline, which routes the failure to the repair agent."
        )


def _libdatadog_package_names() -> list[str]:
    """Crate names of the libdatadog git deps declared in Cargo.toml."""
    text = CARGO_TOML.read_text()
    names = []
    for line in text.splitlines():
        # Each libdatadog dep declares `git = "...libdatadog"` on the same line as
        # its name (even the multi-line ones like data-pipeline/build_common), so a
        # single-line match captures all 15.
        m = re.match(r"\s*([A-Za-z0-9_-]+)\s*=\s*\{[^}]*github\.com/DataDog/libdatadog", line)
        if m:
            names.append(m.group(1))
    return sorted(set(names))


def write_release_note(target_rev: str) -> Path:
    """Write a reno-style upgrade note. Filename suffix is derived from the SHA
    (deterministic, avoids the random hash reno would otherwise generate).
    """
    path = RELEASENOTES_DIR / f"upgrade-libdatadog-{short(target_rev)}.yaml"
    path.write_text(
        textwrap.dedent(
            f"""\
            ---
            upgrade:
              - |
                Bumps libdatadog dependency to {LIBDATADOG_REPO}@{short(target_rev)} (main).
            """
        )
    )
    return path


def git_branch_name(target_rev: str) -> str:
    return f"chore/update-libdatadog-{short(target_rev)}"


# --------------------------------------------------------------------------- #
# GitLab API — find/wait for the pipeline triggered by the pushed branch
# --------------------------------------------------------------------------- #
def bti_gitlab_token() -> str:
    """Mint a short-lived GitLab API token via authanywhere + BTI.

    Same flow as .gitlab/scripts/summarize_failures.py: authanywhere produces
    a JWT for the rapid-devex-ci audience, which the BTI CI API exchanges for
    a project-scoped GitLab PAT. Tokens are never printed.
    """
    bin_path = os.environ.get("AUTHANYWHERE_BIN") or shutil.which("authanywhere")
    if not bin_path:
        raise PipelineError("authanywhere not found (set AUTHANYWHERE_BIN); cannot query the GitLab API.")
    jwt = capture([bin_path, "--audience", BTI_AUDIENCE]).removeprefix("Authorization: Bearer ")
    owner, repo = GH_REPO_SLUG.split("/")
    url = f"{BTI_BASE_URL}/gitlab/token?{urllib.parse.urlencode({'owner': owner, 'repository': repo})}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {jwt}"})
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 (fixed bti host)
        return json.loads(resp.read().decode())["token"]


def _gl_fetch(token: str, method: str, path: str, *, ok_not_found: bool = False) -> str | None:
    """Call the GitLab REST API (CI_API_V4_URL) and return the raw body text."""
    api_v4 = os.environ.get("CI_API_V4_URL", "").rstrip("/")
    if not api_v4:
        raise PipelineError("CI_API_V4_URL not set; not running inside GitLab CI?")
    req = urllib.request.Request(f"{api_v4}{path}", method=method)
    req.add_header("PRIVATE-TOKEN", token)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 (fixed CI API host)
            return resp.read().decode(errors="replace")
    except urllib.error.HTTPError as exc:
        if ok_not_found and exc.code == 404:
            return None
        body = exc.read().decode(errors="replace")[:500]
        raise PipelineError(f"GitLab API {method} {path} → HTTP {exc.code}: {body}") from exc


def _gl_api(token: str, method: str, path: str, *, ok_not_found: bool = False) -> object | None:
    """Call the GitLab REST API and return the parsed JSON (or None for empty/404)."""
    raw = _gl_fetch(token, method, path, ok_not_found=ok_not_found)
    if raw is None or not raw:
        return None
    return json.loads(raw)


def _gl_jobs(token: str, pipeline_id: str) -> list[dict]:
    """All jobs of a pipeline (same project), paginated."""
    jobs: list[dict] = []
    project_id = os.environ["CI_PROJECT_ID"]
    for page in range(1, 11):
        try:
            batch = _gl_api(
                token,
                "GET",
                f"/projects/{project_id}/pipelines/{pipeline_id}/jobs?per_page=100&page={page}",
            )
        except (PipelineError, json.JSONDecodeError) as exc:
            # Tolerant like the PHP job: a foreign-project child pipeline 404s here.
            print(f"  [warn] cannot list jobs of pipeline {pipeline_id}: {exc}")
            break
        if not isinstance(batch, list):
            break
        jobs.extend(batch)
        if len(batch) < 100:
            break
    return jobs


def _gl_bridges(token: str, pipeline_id: str) -> list[dict]:
    """Bridges (trigger jobs) of a pipeline, paginated."""
    project_id = os.environ["CI_PROJECT_ID"]
    bridges: list[dict] = []
    for page in range(1, 6):
        batch = _gl_api(
            token,
            "GET",
            f"/projects/{project_id}/pipelines/{pipeline_id}/bridges?per_page=100&page={page}",
        )
        assert isinstance(batch, list)
        bridges.extend(batch)
        if len(batch) < 100:
            break
    return bridges


def wait_for_branch_pipeline(token: str, branch: str, head_sha: str) -> dict:
    """Wait for the pipeline GitLab creates for (branch, head_sha) to appear.

    The GitHub→GitLab mirror takes a bit to pick up the pushed branch; poll
    until a pipeline for exactly this commit shows up.
    """
    project_id = os.environ["CI_PROJECT_ID"]
    query = urllib.parse.urlencode({"ref": branch, "sha": head_sha})
    deadline = time.monotonic() + PIPELINE_APPEAR_TIMEOUT_S
    while True:
        pipelines = _gl_api(token, "GET", f"/projects/{project_id}/pipelines?{query}")
        assert isinstance(pipelines, list)
        if pipelines:
            pipeline = pipelines[0]
            print(
                f"\n[pipeline] found pipeline #{pipeline['id']} for {branch}@{short(head_sha)} "
                f"(status: {pipeline['status']}) — {pipeline.get('web_url')}"
            )
            return pipeline
        if time.monotonic() > deadline:
            ref_query = urllib.parse.urlencode({"ref": branch})
            existing = _gl_api(token, "GET", f"/projects/{project_id}/pipelines?{ref_query}")
            assert isinstance(existing, list)
            raise PipelineError(
                f"no pipeline appeared for {branch}@{head_sha} after "
                f"{PIPELINE_APPEAR_TIMEOUT_S // 60} min ({len(existing)} pipeline(s) exist for the ref). "
                "Is the GitHub→GitLab mirror running?"
            )
        print("[pipeline] waiting for the mirror to create the pipeline…", flush=True)
        time.sleep(PIPELINE_POLL_INTERVAL_S)


def wait_for_pipeline_completion(token: str, pipeline: dict, timeout_s: float) -> str:
    """Poll the pipeline until it reaches a terminal status; return the status."""
    project_id = os.environ["CI_PROJECT_ID"]
    pipeline_id = pipeline["id"]
    deadline = time.monotonic() + timeout_s
    status = pipeline["status"]
    while status not in PIPELINE_TERMINAL_STATUSES:
        if status == "manual":
            raise PipelineError(
                f"pipeline {pipeline_id} is waiting on a manual job — the auto-update flow "
                "cannot proceed. Needs human attention."
            )
        if time.monotonic() > deadline:
            raise PipelineError(
                f"pipeline {pipeline_id} still '{status}' after {timeout_s / 3600:.1f}h; giving up on it."
            )
        print(f"[pipeline] {pipeline_id}: {status} ({time.strftime('%H:%M:%S')})", flush=True)
        time.sleep(PIPELINE_POLL_INTERVAL_S)
        data = _gl_api(token, "GET", f"/projects/{project_id}/pipelines/{pipeline_id}")
        assert isinstance(data, dict)
        status = data["status"]
    print(f"\n[pipeline] {pipeline_id} finished with status: {status} — {pipeline.get('web_url')}")
    return status


def collect_failed_jobs(token: str, pipeline_id: str) -> list[dict]:
    """Failed jobs of the pipeline and of its (same-project) child pipelines.

    Mirrors the dd-trace-php job: the full test matrix runs in child pipelines
    (bridges), so failures must be collected across all of them. Cross-project
    triggers (e.g. serverless lambda tests) are skipped with a warning — their
    failures are not repairable in this repo anyway.
    """
    failures: list[dict] = []
    for job in _gl_jobs(token, pipeline_id):
        if job["status"] == "failed":
            failures.append({**job, "trigger": "pipeline"})
    for bridge in _gl_bridges(token, pipeline_id):
        child = bridge.get("downstream_pipeline") or {}
        if not child.get("id"):
            continue
        if str(child.get("project_id", os.environ["CI_PROJECT_ID"])) != os.environ["CI_PROJECT_ID"]:
            print(f"[pipeline] skipping cross-project child pipeline of {bridge['name']!r}")
            continue
        for job in _gl_jobs(token, str(child["id"])):
            if job["status"] == "failed":
                failures.append({**job, "trigger": bridge["name"]})
    return failures


def retry_flaky_jobs(token: str, failures: list[dict]) -> list[dict]:
    """Retry failed jobs once (when there are only a few) to filter flakiness.

    Direct port of the dd-trace-php behavior: with fewer than
    RETRY_FAILURES_MAX failures, each failed job is retried via the GitLab API
    and only jobs that still fail afterwards are kept.
    """
    if not (0 < len(failures) < RETRY_FAILURES_MAX):
        return failures
    print(f"\n[pipeline] retrying {len(failures)} failed job(s) to filter flakiness…")
    project_id = os.environ["CI_PROJECT_ID"]
    retried: list[dict] = []
    for job in failures:
        new_job = None
        try:
            new_job = _gl_api(token, "POST", f"/projects/{project_id}/jobs/{job['id']}/retry", ok_not_found=True)
        except (PipelineError, json.JSONDecodeError) as exc:
            print(f"  retry of {job['name']!r} failed: {exc}")
        if isinstance(new_job, dict) and new_job.get("id"):
            print(f"  retried: [{job['trigger']}] {job['name']} → job {new_job['id']}")
            retried.append({**job, **new_job})
        else:
            print(f"  could not retry: [{job['trigger']}] {job['name']} (keeping the original failure)")
            retried.append(job)

    deadline = time.monotonic() + RETRY_WAIT_TIMEOUT_S
    while True:
        running = 0
        for job in retried:
            data = _gl_api(token, "GET", f"/projects/{project_id}/jobs/{job['id']}", ok_not_found=True)
            if isinstance(data, dict):
                # Write the fresh status back so `still` below sees the retried outcome.
                job["status"] = data.get("status", job.get("status", "failed"))
            if job.get("status", "failed") in JOB_RUNNING_STATUSES:
                running += 1
        if not running:
            break
        if time.monotonic() > deadline:
            print("[pipeline] retried job(s) did not finish in time; keeping them as failures.")
            for job in retried:
                job["status"] = "failed" if job.get("status") in JOB_RUNNING_STATUSES else job.get("status", "failed")
            break
        time.sleep(PIPELINE_POLL_INTERVAL_S)

    still = [job for job in retried if job.get("status", "failed") == "failed"]
    print(f"[pipeline] {len(failures) - len(still)} flaky job(s) passed on retry; {len(still)} still failing.")
    return still


def write_ci_results(failures: list[dict], token: str, artifacts: Path, round_no: int) -> None:
    """Persist the CI failure report: ci-summary-<n>.txt, traces-<n>/, failures-<n>.json."""
    project_id = os.environ["CI_PROJECT_ID"]
    traces_dir = artifacts / f"traces-{round_no}"
    traces_dir.mkdir(parents=True, exist_ok=True)

    for job in failures:
        safe = re.sub(r"[^\w.]+", "_", job["name"])[:60]
        trace_path = traces_dir / f"{safe}_{job['id']}.txt"
        header = f"=== {job['name']} (trigger: {job['trigger']}) — {job.get('web_url', '')} ===\n\n"
        trace = "[trace unavailable]\n"
        try:
            # The trace endpoint returns raw text, not JSON.
            trace = (
                _gl_fetch(token, "GET", f"/projects/{project_id}/jobs/{job['id']}/trace", ok_not_found=True) or trace
            )
        except PipelineError as exc:
            print(f"  [warn] cannot fetch trace of {job['name']!r}: {exc}")
        trace_path.write_text(header + trace[-TRACE_TAIL_BYTES:])
        try:
            shown = trace_path.relative_to(REPO_ROOT)
        except ValueError:
            shown = trace_path
        print(f"  saved trace tail → {shown}")

    summary = artifacts / f"ci-summary-{round_no}.txt"
    lines = [f"Total persistent failures: {len(failures)}", ""]
    lines += [f"[{job['trigger']}] {job['name']} — {job.get('web_url', '')}" for job in failures]
    summary.write_text("\n".join(lines) + "\n")

    (artifacts / f"failures-{round_no}.json").write_text(json.dumps(failures, indent=2))


def write_libdatadog_changelog(pinned: set[str], target_rev: str, artifacts: Path) -> Path | None:
    """Best-effort git log of libdatadog between the old pin(s) and the target.

    Gives the repair agent commit-message context for the API changes (the PHP
    job ships the same changelog). Uses the GitHub compare API; unauthenticated
    if no GH_TOKEN is set.
    """
    path = artifacts / "libdatadog-changelog.txt"
    subjects: dict[str, str] = {}
    headers = {}
    for rev in sorted(pinned)[:3]:
        url = f"{GH_API_BASE}/repos/{LIBDATADOG_GH_SLUG}/compare/{rev}...{target_rev}"
        req = urllib.request.Request(url)
        token = os.environ.get("GH_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 (fixed api.github.com host)
                data = json.loads(resp.read().decode())
        except (urllib.error.URLError, json.JSONDecodeError) as exc:
            print(f"[changelog] compare {rev[:12]}...{short(target_rev)} failed ({exc}); skipping.")
            continue
        for commit in data.get("commits", []):
            subjects.setdefault(commit["sha"], commit["commit"]["message"].splitlines()[0])
        headers[rev] = f"commits in {rev[:12]}..{short(target_rev)}: {data.get('ahead_by', '?')}"
    if not subjects:
        return None
    body = "\n".join(sorted(headers.values())) + "\n\n" + "\n".join(list(subjects.values())[:200])
    path.write_text(body)
    return path


def download_libdatadog_source(target_rev: str) -> Path | None:
    """Fetch the libdatadog source at the target rev into a directory outside
    the repo, so the repair agent can read the new API (the PHP job hands
    Claude a `libdatadog/` checkout the same way). Best-effort.
    """
    dest = Path(tempfile.mkdtemp(prefix="libdatadog-src-")) / f"libdatadog-{short(target_rev)}"
    dest.mkdir(parents=True, exist_ok=True)
    url = f"{LIBDATADOG_TARBALL_URL}/{target_rev}"
    try:
        with urllib.request.urlopen(url, timeout=300) as resp:  # nosec B310 (fixed codeload.github.com host)
            with tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz") as tar:
                try:
                    tar.extractall(dest, filter="data")  # nosec B202 (data filter blocks path escapes)
                except TypeError:  # Python < 3.11.2 has no `filter` kwarg
                    tar.extractall(dest)  # nosec B202 (tarball comes from the fixed codeload URL above)
        # codeload tarballs unpack to libdatadog-<ref>/; flatten one level.
        children = list(dest.iterdir())
        if len(children) == 1 and children[0].is_dir():
            for item in children[0].iterdir():
                item.rename(dest / item.name)
            shutil.rmtree(children[0], ignore_errors=True)
        print(f"[libdatadog-src] unpacked to {dest}")
        return dest
    except (urllib.error.URLError, tarfile.TarError, OSError) as exc:
        print(f"[libdatadog-src] download failed ({exc}); the repair agent will reason from traces only.")
        return None


# --------------------------------------------------------------------------- #
# Phase 4 — AI repair + independent review
# --------------------------------------------------------------------------- #
def short(sha: str) -> str:
    return sha[:12]


def _claude_available() -> bool:
    """True if the `claude` CLI and AI-gateway credentials are present."""
    if not shutil.which("claude"):
        print("\n[claude] `claude` CLI not found in PATH — skipping agent steps.")
        return False
    if not (os.environ.get("ANTHROPIC_AUTH_TOKEN") and os.environ.get("ANTHROPIC_BASE_URL")):
        print("\n[claude] AI-gateway env (ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL) not set — skipping agent steps.")
        return False
    return True


def _load_prompt(path: Path) -> str:
    try:
        return path.read_text()
    except FileNotFoundError:
        raise RuntimeError(
            f"prompt file {path} not found — it ships with the repo; run from a full checkout."
        ) from None


def repair_with_claude(
    target_rev: str,
    pinned: set[str],
    pipeline: dict,
    artifacts: Path,
    round_no: int,
    libdd_src: Path | None,
) -> bool:
    """Run one repair attempt against the collected CI failures.

    The prompt (context block + .gitlab/libdatadog-auto-update-repair-prompt.md)
    is composed here but authored in the repo — same layout as dd-trace-php's
    .gitlab/libdatadog-latest-prompt.md. The agent has no Bash tools at all:
    it cannot run cargo and must reason from the traces alone.
    """
    changelog = artifacts / "libdatadog-changelog.txt"
    context = (
        "## Environment\n"
        f"- dd-trace-py source: {REPO_ROOT}\n"
        f"- libdatadog bumped to: {target_rev} (previous pin: {', '.join(sorted(pinned))})\n"
        f"- CI pipeline: {pipeline.get('web_url')} (status: {pipeline.get('status')})\n"
        f"- CI summary: {artifacts / f'ci-summary-{round_no}.txt'}\n"
        f"- Failure trace tails: {artifacts / f'traces-{round_no}'}\n"
        f"- libdatadog changelog: {changelog if changelog.exists() else '(unavailable)'}\n"
        f"- libdatadog source at the new rev: {libdd_src if libdd_src else '(unavailable)'}\n"
    )
    prompt = context + "\n\n" + _load_prompt(REPAIR_PROMPT_FILE)
    (artifacts / f"repair-input-{round_no}.md").write_text(prompt)

    print(f"\n[repair] round {round_no} — invoking Claude…")
    cmd = [
        "claude",
        "--bare",
        "-p",
        prompt,
        "--model",
        CLAUDE_MODEL,
        "--max-turns",
        str(CLAUDE_MAX_TURNS),
        "--allowedTools",
        *CLAUDE_REPAIR_TOOLS,
        "--permission-mode",
        "bypassPermissions",
    ]
    transcript = run(f"claude-repair-{round_no}", cmd, cwd=REPO_ROOT, check=False, timeout=CLAUDE_TIMEOUT_S)
    (artifacts / f"repair-transcript-{round_no}.log").write_text(transcript.output)
    if transcript.returncode != 0:
        print(f"[repair] Claude exited with {transcript.returncode}; see repair-transcript-{round_no}.log.")
    return transcript.returncode != 124


def validate_changes_mechanical(target_rev: str, artifacts: Path, round_no: int) -> bool:
    """Second, independent agent reviews the full change set and confirms it is
    simple and mechanical — a local gate BEFORE pushing a repair commit, so a
    bad repair never wastes a pipeline run. Returns False on a FAIL verdict or
    an unreadable/missing verdict.
    """
    if not _claude_available():
        return False

    print("\n[review] invoking an independent agent to validate the changes are mechanical…")
    prompt = _load_prompt(REVIEW_PROMPT_FILE).replace("__TARGET_REV__", short(target_rev))
    cmd = [
        "claude",
        "--bare",
        "-p",
        prompt,
        "--model",
        CLAUDE_MODEL,
        "--allowedTools",
        *CLAUDE_REVIEW_TOOLS,
        "--permission-mode",
        "bypassPermissions",
    ]
    transcript = run("claude-review", cmd, cwd=REPO_ROOT, check=False, timeout=CLAUDE_TIMEOUT_S)
    (artifacts / f"review-transcript-{round_no}.log").write_text(transcript.output)

    verdicts = re.findall(r"VERDICT:\s*(PASS|FAIL)\b", transcript.output, re.IGNORECASE)
    if not verdicts:
        print(f"\n[review] no verdict found in the transcript; failing closed (see review-transcript-{round_no}.log).")
        return False
    verdict = verdicts[-1].upper()
    if verdict == "PASS":
        print("\n[review] PASS — changes confirmed simple and mechanical.")
        return True
    print("\n[review] FAIL — changes are not mechanical; the repair will NOT be pushed.")
    return False


# --------------------------------------------------------------------------- #
# Phase 3 — push (GitHub API commits, signed server-side)
# --------------------------------------------------------------------------- #
def _gh_api(method: str, path: str, *, token: str, body: dict | None = None) -> dict:
    """Call the GitHub REST API and return the parsed JSON (or {} for empty 2xx)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{GH_API_BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:  # nosec B310 (fixed api.github.com host)
        raw = resp.read().decode()
    return json.loads(raw) if raw else {}


def _commit_changes(artifacts_rel: str | None) -> list[tuple[str, bool]]:
    """(path, is_deleted) for working-tree changes vs HEAD, incl. untracked.

    Only the artifacts dir is excluded (job logs/summaries never enter the
    commit); everything else the bump and the agents changed is included.
    """
    out = run(
        "git-status", ["git", "status", "--porcelain", "--untracked-files=all"], cwd=REPO_ROOT, check=False
    ).output
    changes = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        if " -> " in path:  # rename → take the new path
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if artifacts_rel and (path == artifacts_rel or path.startswith(artifacts_rel + "/")):
            continue
        changes.append((path, "D" in status))
    return changes


def push_branch(
    branch: str,
    title: str,
    body: str,
    changes: list[tuple[str, bool]],
    *,
    base_sha: str | None = None,
    base_tree: str | None = None,
) -> str:
    """Create a GitHub-signed (Verified) commit via the API and point `branch`
    at it (creating or force-updating the ref as needed). Returns the commit SHA.

    The commit is built server-side, so GitHub attributes it to the token's app
    bot (dd-octo-sts[bot]) and signs it — no local commit, no git author
    config, and no token-in-remote-URL needed. By default the commit is built
    on local HEAD; for follow-up (repair) commits pass the remote head via
    base_sha/base_tree so the commit stacks on the previously pushed commit.
    """
    # Safety net: never commit a secret/credential-looking path.
    sensitive = [p for p, _ in changes if SENSITIVE_PATH_RE.search(p)]
    if sensitive:
        raise RuntimeError(f"refusing to create commit: sensitive-looking path(s) in change set: {sensitive}")

    token = os.environ["GH_TOKEN"]
    owner, repo = GH_REPO_SLUG.split("/")

    if base_sha is None:
        base_sha = run("git-head-sha", ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT).output.strip()
    if base_tree is None:
        base_tree = run("git-head-tree", ["git", "rev-parse", "HEAD^{tree}"], cwd=REPO_ROOT).output.strip()

    tree: list[dict] = []
    for path, deleted in changes:
        full = REPO_ROOT / path
        if deleted or not full.exists():
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
            continue
        blob = _gh_api(
            "POST",
            f"/repos/{owner}/{repo}/git/blobs",
            token=token,
            body={"content": base64.b64encode(full.read_bytes()).decode(), "encoding": "base64"},
        )
        mode = "100755" if os.access(full, os.X_OK) else "100644"
        tree.append({"path": path, "mode": mode, "type": "blob", "sha": blob["sha"]})

    new_tree = _gh_api(
        "POST", f"/repos/{owner}/{repo}/git/trees", token=token, body={"base_tree": base_tree, "tree": tree}
    )
    # Omit author/committer: GitHub attributes the commit to the app bot and signs it.
    commit = _gh_api(
        "POST",
        f"/repos/{owner}/{repo}/git/commits",
        token=token,
        body={"message": f"{title}\n\n{body}", "tree": new_tree["sha"], "parents": [base_sha]},
    )

    try:
        _gh_api(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            token=token,
            body={"ref": f"refs/heads/{branch}", "sha": commit["sha"]},
        )
    except urllib.error.HTTPError as exc:
        if exc.code != 422:  # 422 == ref already exists
            raise
        _gh_api(
            "PATCH",
            f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            token=token,
            body={"sha": commit["sha"], "force": True},
        )

    branch_url = f"https://github.com/{GH_REPO_SLUG}/tree/{branch}"
    print(f"\nPushed branch (GitHub-signed commit {short(commit['sha'])}): {branch_url}")
    return commit["sha"]


def _remote_head(branch: str) -> tuple[str, str]:
    """(commit sha, tree sha) of the current head of `branch` on GitHub."""
    token = os.environ["GH_TOKEN"]
    owner, repo = GH_REPO_SLUG.split("/")
    ref = _gh_api("GET", f"/repos/{owner}/{repo}/git/ref/heads/{urllib.parse.quote(branch)}", token=token)
    commit = _gh_api("GET", f"/repos/{owner}/{repo}/git/commits/{ref['object']['sha']}", token=token)
    return ref["object"]["sha"], commit["tree"]["sha"]


def push_commit(
    branch: str,
    title: str,
    body: str,
    changes: list[tuple[str, bool]],
    *,
    push: bool,
    dry_run: bool,
    base: tuple[str, str] | None = None,
) -> str | None:
    """Commit the change set to `branch` and return the new head SHA.

    Returns None when nothing was pushed (dry-run, --no-push, or no GH_TOKEN) —
    the caller then skips the pipeline-validation phase.
    """
    print(f"\nChanges to commit ({len(changes)}):")
    for path, deleted in changes:
        print(f"  {'D' if deleted else 'M'} {path}")

    if dry_run or not push:
        why = "dry-run" if dry_run else "--no-push"
        print(f"\n[{why}] skipping push; would commit to branch {branch!r}.")
        return None

    if not os.environ.get("GH_TOKEN"):
        print(f"\n[no GH_TOKEN] cannot push {branch!r} via the GitHub API; skipping (changes listed above).")
        return None

    if base is None:
        return push_branch(branch, title, body, changes)
    return push_branch(branch, title, body, changes, base_sha=base[0], base_tree=base[1])


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target-rev", help="bump to this rev instead of latest main")
    parser.add_argument(
        "--target-branch", help="push the result to this branch (default: a chore/update-libdatadog-<rev> name)"
    )
    parser.add_argument("--dry-run", action="store_true", help="show changes, modify nothing")
    parser.add_argument(
        "--no-push", dest="push", action="store_false", help="apply the bump, list the changes, don't push"
    )
    parser.add_argument(
        "--max-repair-iterations", type=int, default=3, help="max Claude repair rounds after red pipelines"
    )
    parser.add_argument(
        "--pipeline-timeout",
        type=float,
        default=PIPELINE_STATUS_TIMEOUT_DEFAULT_S,
        help="seconds to wait for each triggered pipeline run (default: %(default)s)",
    )
    args = parser.parse_args()

    artifacts = Path(os.environ.get("ARTIFACTS_DIR", REPO_ROOT / "libdatadog-auto-update"))
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        artifacts_rel = artifacts.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        artifacts_rel = None  # artifacts live outside the repo; nothing to exclude

    # Phase 1 — resolve target ------------------------------------------------ #
    target_rev = args.target_rev or resolve_latest_main_sha()
    pinned = current_pinned_revs()
    print(f"\nTarget rev : {target_rev}")
    print(f"Current pin: {', '.join(sorted(pinned)) or '(none found)'}")

    if pinned == {target_rev}:
        print("\nNothing to do — already pinned to the target rev.")
        return 0

    # Phase 2 — apply bump ---------------------------------------------------- #
    if args.dry_run:
        n = len(_LIBDD_REV_RE.findall(CARGO_TOML.read_text()))
        print(
            f"\n[dry-run] would rewrite {n} libdatadog rev(s) → {short(target_rev)}, "
            "regenerate Cargo.lock, push, and validate via the triggered pipeline."
        )
        return 0

    # AIDEV-NOTE: the Rust toolchain channel / MSRV (setup.py's
    # RUST_MINIMUM_VERSION) is deliberately NOT synced from the target rev —
    # channel/MSRV bumps are infrequent and need human scrutiny. We assume the
    # current and target libdatadog revs require the same toolchain; a real
    # mismatch simply surfaces as a build failure in the triggered pipeline.
    n = rewrite_cargo_toml(target_rev)
    print(f"\nRewrote {n} libdatadog rev(s) in {CARGO_TOML.relative_to(REPO_ROOT)}.")
    regenerate_lockfile()
    note = write_release_note(target_rev)

    # Phase 3 — push, which triggers the real (fully parallelized) pipeline --- #
    branch = args.target_branch or git_branch_name(target_rev)
    title = f"chore(native): update libdatadog to {short(target_rev)}"
    bump_summary = "\n".join(
        [
            f"Bumps libdatadog to `{short(target_rev)}`.",
            "",
            f"- Source: {LIBDATADOG_REPO}/commit/{target_rev}",
            f"- Rewrote {n} `rev` pin(s) in `src/native/Cargo.toml` and regenerated `Cargo.lock`.",
            f"- Added release note `{note.relative_to(REPO_ROOT)}`.",
            "",
            "Validation: the CI pipeline for this branch (triggered by this push).",
        ]
    )
    head_sha = push_commit(
        branch, title, bump_summary, _commit_changes(artifacts_rel), push=args.push, dry_run=args.dry_run
    )
    if head_sha is None:
        return 0  # changes were listed; nothing was pushed

    # Phase 4 — wait for the triggered pipeline, repair on red ---------------- #
    gitlab_token = None
    if os.environ.get("CI_API_V4_URL") and os.environ.get("CI_PROJECT_ID"):
        try:
            gitlab_token = bti_gitlab_token()
            print("[pipeline] minted a short-lived GitLab API token.")
        except (PipelineError, RuntimeError, urllib.error.URLError) as exc:
            print(f"\n[pipeline] no GitLab API access ({exc}); skipping pipeline validation.")
    else:
        print("\n[pipeline] not running in GitLab CI; skipping pipeline validation (the branch's pipeline will run).")

    if gitlab_token is None:
        (artifacts / "summary.md").write_text(bump_summary)
        print("\nDone (validation skipped).")
        return 0

    pipeline = None
    rounds = 0
    while True:
        try:
            pipeline = wait_for_branch_pipeline(gitlab_token, branch, head_sha)
            status = wait_for_pipeline_completion(gitlab_token, pipeline, args.pipeline_timeout)
        except PipelineError as exc:
            print(f"\n[pipeline] {exc}")
            (artifacts / "summary.md").write_text(f"{bump_summary}\n\nPipeline error: {exc}\n")
            return 1

        if status == "success":
            break

        try:
            failures = collect_failed_jobs(gitlab_token, str(pipeline["id"]))
            failures = retry_flaky_jobs(gitlab_token, failures)
            write_ci_results(failures, gitlab_token, artifacts, rounds + 1)
        except PipelineError as exc:
            print(f"\n[pipeline] {exc}")
            (artifacts / "summary.md").write_text(f"{bump_summary}\n\nPipeline error: {exc}\n")
            return 1

        if not failures:
            msg = (
                f"pipeline {pipeline['id']} finished with status {status!r} but no failed jobs "
                "were found; needs human investigation."
            )
            print(f"\n[pipeline] {msg}")
            (artifacts / "summary.md").write_text(f"{bump_summary}\n\n{msg}\n")
            return 1

        if rounds >= args.max_repair_iterations:
            print(
                f"\nPipeline still red after {rounds} repair round(s); giving up. "
                "See ci-summary-*.txt / traces-*/ in the job artifacts."
            )
            break
        rounds += 1

        # Repair: Claude works from the CI traces alone — no cargo, no builds.
        libdd_src = download_libdatadog_source(target_rev)
        write_libdatadog_changelog(pinned, target_rev, artifacts)
        if not _claude_available():
            (artifacts / "summary.md").write_text(f"{bump_summary}\n\nPipeline failed; repair agents unavailable.\n")
            return 1
        before = run("git-status-before", ["git", "status", "--porcelain"], cwd=REPO_ROOT, check=False).output
        repair_with_claude(target_rev, pinned, pipeline, artifacts, rounds, libdd_src)
        after = run("git-status-after", ["git", "status", "--porcelain"], cwd=REPO_ROOT, check=False).output
        if before == after:
            print(
                "\n[repair] Claude made no changes (likely classified all failures as flaky or "
                "libdatadog bugs); needs human attention. See repair-transcript-*.log."
            )
            break
        if not validate_changes_mechanical(target_rev, artifacts, rounds):
            (artifacts / "summary.md").write_text(
                f"{bump_summary}\n\nRepair round {rounds} rejected by the mechanical-changes review.\n"
            )
            return 1

        # Push the repair commit on top of the remote head; the push triggers
        # the next pipeline run, and the loop waits for it again.
        repair_title = f"fix(native): adapt to libdatadog changes at {short(target_rev)} (repair {rounds})"
        head_sha = push_commit(
            branch,
            repair_title,
            f'Follow-up to "{title}". Repairs CI failures found by pipeline {pipeline.get("web_url")}.',
            _commit_changes(artifacts_rel),
            push=args.push,
            dry_run=False,
            base=_remote_head(branch),
        )
        if head_sha is None:
            return 1

    # Phase 5 — summary -------------------------------------------------------- #
    ok = status == "success"
    summary_lines = [
        f"Bumps libdatadog to `{short(target_rev)}`.",
        "",
        f"- Source: {LIBDATADOG_REPO}/commit/{target_rev}",
        f"- Rewrote {n} `rev` pin(s) in `src/native/Cargo.toml` and regenerated `Cargo.lock`.",
        f"- Added release note `{note.relative_to(REPO_ROOT)}`.",
        f"- Validated by the pipeline for branch `{branch}`: {pipeline.get('web_url') if pipeline else 'n/a'}",
    ]
    if rounds:
        summary_lines.append(f"- {rounds} Claude repair round(s) (see repair-transcript-*.log in the artifacts).")
    if not ok:
        summary_lines.append("- ⚠️ Pipeline still red — see ci-summary-*.txt / traces-*/ in the artifacts.")
    summary = "\n".join(summary_lines)
    (artifacts / "summary.md").write_text(summary)
    print(f"\n{summary}")
    print("\nDone.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
