#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""Update the pinned libdatadog dependency in src/native to the target
revision of libdatadog, or the latest from main if unspecified. Solve trivial
backward-incompatible updates with an agent, but make a deliberate effort to
avoid any non-trivial changes:

Phases:
  1. Resolve target
  2. Apply bump.
  3. Validate (build, tests, etc.).
  4. Repair if validation is red. Claude attempts a bounded set of fixes. A
     fresh agent then validates that the changes are mechanical.
  5. Push: commit to the target branch (or a default name if not provided).
     Create the branch if needed.

The script is intentionally runnable locally: with no GH token it stops after
printing the changes, and `--dry-run` makes no changes at all.

Usage:
  scripts/libdatadog-auto-update.py                        # bump to latest main, validate, push
  scripts/libdatadog-auto-update.py --dry-run              # show what would change, touch nothing
  scripts/libdatadog-auto-update.py --target-rev SHA       # bump to a specific rev
  scripts/libdatadog-auto-update.py --target-branch foo-bar  # push the result to the foo-bar
                                                            # branch (create if needed)
  scripts/libdatadog-auto-update.py --skip-python          # cargo-only validation (fast local loop)

Environment:
  GH_TOKEN              GitHub token (from octo-sts). When set, the bump is
                        committed via the GitHub API as a verified,
                        bot-attributed commit and pushed to the target branch.
                        Without it, the changes are listed and nothing is
                        pushed.
  ANTHROPIC_AUTH_TOKEN  AI-gateway bearer token. Required for the repair and
  ANTHROPIC_BASE_URL    the mechanical-changes validation. If unset, both are
                        skipped and a red build fails the run.
  ARTIFACTS_DIR         where to write logs and the summary (default: ./libdatadog-auto-update)
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import urllib.error
import urllib.request


LIBDATADOG_REPO = "https://github.com/DataDog/libdatadog"
GH_REPO_SLUG = "DataDog/dd-trace-py"
GH_API_BASE = "https://api.github.com"

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

# Claude model + tools for the repair and review agents, mirroring
# .gitlab/check-libdatadog-version.yml. The read-only shell tools let the
# repair agent locate and inspect libdatadog's fetched source under
# $CARGO_HOME/git/checkouts (outside the repo tree) so it can discover e.g. a
# crate's new name when one was renamed at the target rev. Hard cap per agent
# call so a hung/slow agent can't block the job; the job's own timeout is the
# outer backstop.
CLAUDE_TIMEOUT_S = 600
CLAUDE_MODEL = "anthropic/claude-sonnet-4-6"
CLAUDE_REPAIR_TOOLS = [
    "Read",
    "Edit",
    "Write",
    "Grep",
    "Glob",
    "Bash(cargo:*)",
    "Bash(find:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(grep:*)",
]
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
    resolution error resurfaces in `cargo build` during validate_native(), which
    IS routed to the repair loop so the agent can fix src/native/Cargo.toml.
    """
    pkgs = _libdatadog_package_names()
    cmd = ["cargo", "update", "--manifest-path", str(CARGO_TOML)]
    for pkg in pkgs:
        cmd += ["-p", pkg]
    res = run("cargo-update", cmd, cwd=NATIVE_DIR, check=False)
    if not res.ok:
        print(
            "\n[cargo-update] lock refresh failed (likely a renamed/removed crate at the new rev); "
            "deferring to `cargo build` in validation, which routes failures to the repair loop."
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


# --------------------------------------------------------------------------- #
# Phase 3 — validate
# --------------------------------------------------------------------------- #
def validate_native() -> None:
    """Run the cargo validation suite — the repairable signal.

    Raises StepError on the first failing step so the caller can route it to the
    repair loop. This is native-code only: every failure here is something the
    agent can plausibly fix by editing src/native.
    """
    for label, cmd in (
        ("cargo-build", ["cargo", "build", "--all-features"]),
        ("cargo-fmt", ["cargo", "fmt", "--all", "--", "--check"]),
        ("cargo-clippy", ["cargo", "clippy", "--all-features", "--", "-D", "warnings"]),
        ("cargo-test", ["cargo", "test", "--no-fail-fast", "--locked"]),
    ):
        run(label, cmd, cwd=NATIVE_DIR)


def _python_with_pip() -> str | None:
    """A real interpreter that has pip, or None.

    NOT sys.executable: under `uv run --script` that's an ephemeral env without
    pip, so installing the project with it fails spuriously.
    """
    self_py = Path(sys.executable).resolve()
    for cand in ("python3", "python", "python3.12", "python3.11"):
        path = shutil.which(cand)
        if not path or Path(path).resolve() == self_py:
            continue
        if run("python-pip-check", [path, "-m", "pip", "--version"], check=False).ok:
            return path
    return None


def validate_python() -> bool:
    """Best-effort build + import smoke. Returns False on a real failure.

    Deliberately NOT routed to the repair loop: a pip/venv problem is an
    environment issue, not a libdatadog API change. The comprehensive Python
    suite runs in PR CI anyway; this is just an early ABI smoke. Skips (returns
    True) when no pip-capable interpreter is available.
    """
    py = _python_with_pip()
    if not py:
        print("\n[python-smoke] no pip-capable interpreter found; skipping (full suite runs in PR CI).")
        return True
    if not run("pip-install", [py, "-m", "pip", "install", "-e", "."], cwd=REPO_ROOT, check=False).ok:
        print("\n[python-smoke] `pip install -e .` failed — see log; not treated as a native-build failure.")
        return False
    smoke = run(
        "native-import-smoke",
        [py, "-c", "import ddtrace.internal.native; print('native import OK')"],
        cwd=REPO_ROOT,
        check=False,
    )
    return smoke.ok


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


def _repair_prompt(failure: StepError, target_rev: str, log_path: Path) -> str:
    return (
        f"The pinned libdatadog dependency in src/native was just bumped to {short(target_rev)} "
        f"and the native build now fails at `{failure.label}`. The full failing output is in "
        f"{log_path} — read it first.\n\n"
        "Fix the build by adapting this repo to libdatadog's changed API — usually a renamed or "
        "moved crate (fix the entries in src/native/Cargo.toml), a changed function/type "
        "signature, or a moved import.\n\n"
        "Keep the changes as small and mechanical as possible, and never weaken or delete tests, "
        "assertions, or lints to make the build pass. Verify with `cargo build --all-features` "
        "and `cargo clippy --all-features -- -D warnings` in src/native before finishing. If the "
        "breakage cannot be fixed with small mechanical changes, stop and say so. "
        "Finish with a brief summary of what changed and why."
    )


def repair_loop(failure: StepError, target_rev: str, max_iterations: int, artifacts: Path) -> bool:
    """Attempt to fix native build breakage from libdatadog API changes.

    Each iteration: invoke Claude, then re-run validate_native() — the
    deterministic gate the agent cannot influence. Returns True once it passes,
    else False after max_iterations. Only native (cargo) failures are repaired;
    environment issues (e.g. pip) are never routed here. What the agent changed
    is judged afterwards by validate_changes_mechanical().
    """
    if not _claude_available():
        return False

    for i in range(1, max_iterations + 1):
        log_path = artifacts / f"repair-input-{i}.log"
        log_path.write_text(f"step: {failure.label} (exit {failure.returncode})\n\n{failure.output}")
        print(f"\n[repair] iteration {i}/{max_iterations} — invoking Claude…")

        # Single --allowedTools followed by all values, matching the proven form
        # in .gitlab/check-libdatadog-version.yml.
        cmd = [
            "claude",
            "--bare",
            "-p",
            _repair_prompt(failure, target_rev, log_path),
            "--model",
            CLAUDE_MODEL,
            "--allowedTools",
            *CLAUDE_REPAIR_TOOLS,
            "--permission-mode",
            "bypassPermissions",
        ]
        transcript = run(f"claude-repair-{i}", cmd, cwd=REPO_ROOT, check=False, timeout=CLAUDE_TIMEOUT_S)
        (artifacts / f"repair-transcript-{i}.log").write_text(transcript.output)
        if transcript.returncode == 124:
            print(f"[repair] iteration {i} timed out after {CLAUDE_TIMEOUT_S}s; moving on.")

        try:
            validate_native()
            print(f"\n[repair] converged after {i} iteration(s).")
            return True
        except StepError as exc:
            failure = exc
            (artifacts / f"{exc.label}-after-repair-{i}.log").write_text(exc.output)
            print(f"[repair] still failing at {exc.label!r} after iteration {i}.")

    print(f"\n[repair] did not converge after {max_iterations} iteration(s).")
    return False


def _review_prompt(target_rev: str) -> str:
    return (
        f"An automated script just bumped the pinned libdatadog dependency in this repo to "
        f"{short(target_rev)}; because the native build broke, an AI agent then repaired it. "
        "You are an independent reviewer: verify the result is simple and mechanical.\n\n"
        "Inspect the full working-tree change set with `git status` and `git diff HEAD` (new "
        "files are untracked and only show in `git status`). Expected mechanical changes: "
        "libdatadog `rev` pins in src/native/Cargo.toml, the regenerated Cargo.lock, and a "
        "release note in releasenotes/notes/.\n\n"
        "PASS only if every change is a simple, mechanical adaptation (rev pins, lockfile "
        "churn, renames, signature/import fixes). FAIL if you see weakened or deleted tests, "
        "assertions, or lints; refactors unrelated to the bump; behavior changes; or anything "
        "that requires a design decision or looks like working around the failure instead of "
        "fixing it.\n\n"
        "End your reply with exactly one final line: 'VERDICT: PASS' or 'VERDICT: FAIL: <reason>'."
    )


def validate_changes_mechanical(target_rev: str, artifacts: Path) -> bool:
    """Second, independent agent reviews the full change set and confirms it is
    simple and mechanical. Returns False (blocking the push) on a FAIL verdict
    or an unreadable/missing verdict.
    """
    if not _claude_available():
        # No repair could have happened either (it needs the same setup), so the
        # change set is just the mechanical bump itself.
        return True

    print("\n[review] invoking an independent agent to validate the changes are mechanical…")
    cmd = [
        "claude",
        "--bare",
        "-p",
        _review_prompt(target_rev),
        "--model",
        CLAUDE_MODEL,
        "--allowedTools",
        *CLAUDE_REVIEW_TOOLS,
        "--permission-mode",
        "bypassPermissions",
    ]
    transcript = run("claude-review", cmd, cwd=REPO_ROOT, check=False, timeout=CLAUDE_TIMEOUT_S)
    (artifacts / "review-transcript.log").write_text(transcript.output)

    verdicts = re.findall(r"VERDICT:\s*(PASS|FAIL)\b", transcript.output, re.IGNORECASE)
    if not verdicts:
        print("\n[review] no verdict found in the transcript; failing closed (see review-transcript.log).")
        return False
    verdict = verdicts[-1].upper()
    if verdict == "PASS":
        print("\n[review] PASS — changes confirmed simple and mechanical.")
        return True
    print("\n[review] FAIL — changes are not mechanical; the result will NOT be pushed.")
    return False


# --------------------------------------------------------------------------- #
# Phase 5 — push
# --------------------------------------------------------------------------- #
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


def _gh_api(method: str, path: str, *, token: str, body: dict | None = None) -> dict:
    """Call the GitHub REST API and return the parsed JSON (or {} for empty 2xx)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{GH_API_BASE}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 (fixed api.github.com host)
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


def push_branch(branch: str, title: str, body: str, changes: list[tuple[str, bool]]) -> None:
    """Create a GitHub-signed (Verified) commit via the API and point `branch`
    at it (creating or force-updating the ref as needed). No PR is opened.

    The commit is built server-side on top of the local HEAD, so GitHub
    attributes it to the token's app bot (dd-octo-sts[bot]) and signs it — no
    local commit, no git author config, and no token-in-remote-URL needed.
    """
    # Safety net: never commit a secret/credential-looking path.
    sensitive = [p for p, _ in changes if SENSITIVE_PATH_RE.search(p)]
    if sensitive:
        raise RuntimeError(f"refusing to create commit: sensitive-looking path(s) in change set: {sensitive}")

    token = os.environ["GH_TOKEN"]
    owner, repo = GH_REPO_SLUG.split("/")

    base_sha = run("git-head-sha", ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT).output.strip()
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

    print(f"\nPushed branch (GitHub-signed commit): https://github.com/{GH_REPO_SLUG}/tree/{branch}")


def push_or_list(
    branch: str, title: str, summary: str, changes: list[tuple[str, bool]], *, push: bool, dry_run: bool
) -> None:
    print(f"\nChanges to commit ({len(changes)}):")
    for path, deleted in changes:
        print(f"  {'D' if deleted else 'M'} {path}")

    if dry_run or not push:
        why = "dry-run" if dry_run else "--no-push"
        print(f"\n[{why}] skipping push; would commit to branch {branch!r}.")
        return

    if not os.environ.get("GH_TOKEN"):
        print(f"\n[no GH_TOKEN] cannot push {branch!r} via the GitHub API; skipping (changes listed above).")
        return

    push_branch(branch, title, summary, changes)


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
    parser.add_argument("--skip-python", action="store_true", help="cargo-only validation")
    parser.add_argument("--no-push", dest="push", action="store_false", help="list the changes, don't push")
    parser.add_argument("--max-repair-iterations", type=int, default=3)
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
            "regenerate Cargo.lock, and validate."
        )
        return 0

    # AIDEV-NOTE: the Rust toolchain channel / MSRV (setup.py's
    # RUST_MINIMUM_VERSION) is deliberately NOT synced from the target rev —
    # channel/MSRV bumps are infrequent and need human scrutiny. We assume the
    # current and target libdatadog revs require the same toolchain; a real
    # mismatch simply surfaces as a build failure in validation below.
    n = rewrite_cargo_toml(target_rev)
    print(f"\nRewrote {n} libdatadog rev(s) in {CARGO_TOML.relative_to(REPO_ROOT)}.")
    regenerate_lockfile()
    note = write_release_note(target_rev)

    # Phase 3 — validate (repair on red) --------------------------------------- #
    converged = True
    repaired = False
    try:
        validate_native()
    except StepError as exc:
        (artifacts / f"{exc.label}.log").write_text(exc.output)
        print(f"\nValidation failed at {exc.label!r}; entering repair loop.")
        converged = repair_loop(exc, target_rev, args.max_repair_iterations, artifacts)
        repaired = converged
        if not converged:
            print(
                f"\nNative build FAILED at `{exc.label}` and automated repair did not converge "
                f"after {args.max_repair_iterations} attempt(s); nothing was pushed. "
                "See the `repair-*` / `*-after-repair-*` logs in the job artifacts."
            )
            return 1

    # Phase 4 — independent review of the change set ---------------------------- #
    if not validate_changes_mechanical(target_rev, artifacts):
        print(
            "\nChanges are not simple/mechanical; nothing was pushed. See review-transcript.log in the job artifacts."
        )
        return 1

    # Python smoke — best-effort, NOT routed to repair (env issue ≠ API change).
    # The comprehensive Python suite runs in PR CI.
    python_ok = args.skip_python or validate_python()

    # Phase 5 — push ------------------------------------------------------------ #
    summary_lines = [
        f"Bumps libdatadog to `{short(target_rev)}`.",
        "",
        f"- Source: {LIBDATADOG_REPO}/commit/{target_rev}",
        f"- Rewrote {n} `rev` pin(s) in `src/native/Cargo.toml` and regenerated `Cargo.lock`.",
    ]
    summary_lines.append(f"- Added release note `{note.relative_to(REPO_ROOT)}`.")
    if repaired:
        summary_lines.append(
            "- Build initially broke and was auto-repaired by Claude. **Scrutinize the src/native diff.**"
        )
    if not python_ok:
        summary_lines.append("- ⚠️ Python import smoke did not pass — verify the native ABI in PR CI.")
    summary = "\n".join(summary_lines)
    (artifacts / "summary.md").write_text(summary)

    branch = args.target_branch or git_branch_name(target_rev)
    title = f"chore(native): update libdatadog to {short(target_rev)}"
    push_or_list(
        branch,
        title,
        summary,
        _commit_changes(artifacts_rel),
        push=args.push,
        dry_run=args.dry_run,
    )
    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
