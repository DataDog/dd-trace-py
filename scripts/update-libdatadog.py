#!/usr/bin/env scripts/uv-run-script
# -*- mode: python -*-
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
r"""Update the pinned libdatadog dependency in src/native to the latest commit on
libdatadog's `main` branch, validate the build, and (when green) open a PR.

Phases:
  1. Resolve target — `git ls-remote` libdatadog main → SHA. No-op if already pinned.
  2. Apply bump — rewrite every libdatadog `rev = "..."` in src/native/Cargo.toml,
     regenerate Cargo.lock, and sync setup.py's RUST_MINIMUM_VERSION if needed.
  3. Validate — cargo build / fmt / clippy / test (+ optional python smoke).
  4. Outcome — green: write release note, push branch, open PR (or print the
     command if no GH token).
  5. Repair — if validation is red, Claude (via the AI gateway) attempts a
     bounded set of fixes constrained to src/native + setup.py, with the build
     re-validated by code the agent can't influence. Converges → normal PR;
     gives up → draft PR with the diagnosis.

The script is intentionally runnable locally: with no GH token it stops after
pushing/printing, and `--dry-run` makes no changes at all.

Usage:
  scripts/update-libdatadog.py                 # bump to latest main, validate, PR
  scripts/update-libdatadog.py --dry-run       # show what would change, touch nothing
  scripts/update-libdatadog.py --target-rev SHA  # bump to a specific rev
  scripts/update-libdatadog.py --skip-python   # cargo-only validation (fast local loop)

Environment:
  GH_TOKEN              if set (and `gh` present), used to open the PR; otherwise
                        the branch is pushed and the create-PR command is printed.
  GIT_PUSH_REMOTE       git remote to push to (default: origin). CI sets this to a
                        GitHub remote since `origin` is the GitLab mirror.
  GIT_AUTHOR_*/         commit identity, read natively by git. CI sets these to
  GIT_COMMITTER_*       dd-octo-sts[bot]; locally git falls back to your config.
  ANTHROPIC_AUTH_TOKEN  AI-gateway bearer token; required for the Phase 5 repair
  ANTHROPIC_BASE_URL    loop. If unset, repair is skipped and red builds draft-PR.
  ARTIFACTS_DIR         where to write logs and the summary (default: ./libdatadog-update)
"""

from __future__ import annotations

import argparse
import dataclasses
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import urllib.request


LIBDATADOG_REPO = "https://github.com/DataDog/libdatadog"
GH_REPO_SLUG = "DataDog/dd-trace-py"

REPO_ROOT = Path(__file__).resolve().parent.parent
NATIVE_DIR = REPO_ROOT / "src" / "native"
CARGO_TOML = NATIVE_DIR / "Cargo.toml"
SETUP_PY = REPO_ROOT / "setup.py"
RELEASENOTES_DIR = REPO_ROOT / "releasenotes" / "notes"

# The only paths the AI repair loop is allowed to touch. Anything it changes
# outside these is reverted before re-validation, so it can't "fix" a red build
# by weakening tests or loosening lints.
ALLOWED_REPAIR_PREFIXES = ("src/native/", "setup.py")

# Claude model + tools for the repair loop, mirroring .gitlab/check-libdatadog-version.yml.
# The read-only shell tools let the agent locate and inspect libdatadog's fetched
# source under $CARGO_HOME/git/checkouts (outside the repo tree) so it can discover
# e.g. a crate's new name when one was renamed at the target rev.
# Hard cap per repair iteration so a hung/slow agent can't block the job; the
# job's own timeout is the outer backstop.
CLAUDE_REPAIR_TIMEOUT_S = 600
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

    Output is streamed line-by-line (so long-running steps don't look frozen in CI)
    while also being captured into the result. If `timeout` is set, the process is
    killed after that many seconds and the step is reported as failed (rc 124) —
    this is what stops a hung subprocess (e.g. Claude) from blocking the job
    indefinitely.
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


def _fetch_libdatadog_rust_channel(target_rev: str) -> str | None:
    """Read libdatadog's pinned Rust channel at target_rev, or None if unavailable.

    libdatadog uses a `rust-toolchain.toml` with `channel = "X.Y.Z"`; older revs
    may use a bare `rust-toolchain` file. Best-effort over the network.
    """
    for fname in ("rust-toolchain.toml", "rust-toolchain"):
        url = f"https://raw.githubusercontent.com/DataDog/libdatadog/{target_rev}/{fname}"
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310 (trusted host)
                text = resp.read().decode("utf-8")
        except Exception:
            continue
        m = re.search(r'channel\s*=\s*"([^"]+)"', text)  # toml form
        if m:
            return m.group(1).strip()
        bare = text.strip()  # plain `rust-toolchain` file: just the version
        if re.fullmatch(r"\d+\.\d+(\.\d+)?", bare):
            return bare
    return None


def sync_rust_minimum_version(target_rev: str) -> str | None:
    """Update setup.py's RUST_MINIMUM_VERSION to libdatadog's pinned channel.

    Returns the new version if it changed, else None. Best-effort: if the
    toolchain file can't be fetched or only specifies a partial version, leave
    setup.py untouched and let the build step surface any real mismatch.
    """
    channel = _fetch_libdatadog_rust_channel(target_rev)
    if not channel or not re.fullmatch(r"\d+\.\d+\.\d+", channel):
        # Only act on a full X.Y.Z; "stable"/"1.87" aren't safe to pin verbatim.
        print(
            f"\n[rust-version] no actionable channel from libdatadog@{short(target_rev)} "
            f"({channel!r}); leaving setup.py."
        )
        return None

    text = SETUP_PY.read_text()
    m = re.search(r'(RUST_MINIMUM_VERSION\s*=\s*")([^"]+)(")', text)
    if not m:
        print("\n[rust-version] RUST_MINIMUM_VERSION not found in setup.py; skipping.")
        return None
    if m.group(2) == channel:
        return None

    new_text = text[: m.start(2)] + channel + text[m.end(2) :]
    # Refresh the explanatory comment if it names a specific version.
    new_text = re.sub(
        r"(# libdatadog\s+\S+\s+requires rust\s+)\d+\.\d+\.\d+\.?",
        rf"\g<1>{channel}.",
        new_text,
    )
    SETUP_PY.write_text(new_text)
    print(f"\n[rust-version] RUST_MINIMUM_VERSION {m.group(2)} -> {channel} (from libdatadog rust-toolchain).")
    return channel


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
# Phase 4 — outcome (release note + PR / fallback)
# --------------------------------------------------------------------------- #
def short(sha: str) -> str:
    return sha[:12]


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


def open_pr_or_fallback(
    target_rev: str, branch: str, summary: str, *, push: bool, dry_run: bool, draft: bool = False
) -> None:
    flavor = "DRAFT " if draft else ""
    title = f"chore(native): update libdatadog to {short(target_rev)} (main)"
    body = summary

    if dry_run:
        print(f"\n[dry-run] would create branch, commit, and open {flavor}PR:")
        print(f"  branch: {branch}\n  title:  {title}")
        return

    run("git-checkout", ["git", "checkout", "-B", branch], cwd=REPO_ROOT)
    run(
        "git-add",
        ["git", "add", "src/native/Cargo.toml", "src/native/Cargo.lock", "setup.py", "releasenotes/notes/"],
        cwd=REPO_ROOT,
    )
    # git reads the author/committer from GIT_AUTHOR_*/GIT_COMMITTER_* (the CI job
    # sets these to dd-octo-sts[bot]) or, locally, from the developer's git config.
    run("git-commit", ["git", "commit", "-m", title], cwd=REPO_ROOT)

    # In GitLab CI `origin` is the GitLab mirror, not GitHub; the job sets
    # GIT_PUSH_REMOTE to a GitHub remote it configures with the octo-sts token.
    remote = os.environ.get("GIT_PUSH_REMOTE", "origin")

    if not push:
        print("\n[--no-push] branch committed locally; not pushing. To open the PR:")
        print(f"  git push -u {remote} {branch} && gh pr create --repo {GH_REPO_SLUG} --fill")
        return

    run("git-push", ["git", "push", "-u", remote, branch, "--force-with-lease"], cwd=REPO_ROOT)

    if os.environ.get("GH_TOKEN") and shutil.which("gh"):
        # --repo is required: `origin` here is the GitLab mirror, so gh can't infer
        # the GitHub repo from the remotes.
        cmd = [
            "gh",
            "pr",
            "create",
            "--repo",
            GH_REPO_SLUG,
            "--base",
            "main",
            "--head",
            branch,
            "--title",
            title,
            "--body",
            body,
        ]
        if draft:
            cmd.append("--draft")
        run("gh-pr-create", cmd, cwd=REPO_ROOT)
    else:
        print(f"\n[no GH_TOKEN/gh] branch pushed. To open the {flavor}PR:")
        draft_flag = " --draft" if draft else ""
        print(
            f"  gh pr create --repo {GH_REPO_SLUG} --base main --head {branch}{draft_flag} --title {title!r} --body ..."
        )


# --------------------------------------------------------------------------- #
# Phase 5 — bounded AI repair loop
# --------------------------------------------------------------------------- #
def _claude_available() -> bool:
    """True if the `claude` CLI and AI-gateway credentials are present."""
    if not shutil.which("claude"):
        print("\n[repair] `claude` CLI not found in PATH — skipping auto-repair.")
        return False
    if not (os.environ.get("ANTHROPIC_AUTH_TOKEN") and os.environ.get("ANTHROPIC_BASE_URL")):
        print("\n[repair] AI-gateway env (ANTHROPIC_AUTH_TOKEN/ANTHROPIC_BASE_URL) not set — skipping.")
        return False
    return True


def _revert_changes_outside_allowed() -> list[str]:
    """Revert tracked files the agent modified outside ALLOWED_REPAIR_PREFIXES.

    This is the integrity gate: the agent can edit src/native and setup.py to
    adapt to API changes, but it cannot make a red build pass by touching tests,
    CI, or anything else — those edits are thrown away before we re-validate.
    Returns the list of reverted paths.
    """
    changed = run("git-changed", ["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, check=False).output
    reverted = []
    for path in (line.strip() for line in changed.splitlines() if line.strip()):
        if not path.startswith(ALLOWED_REPAIR_PREFIXES):
            run("git-revert-file", ["git", "checkout", "HEAD", "--", path], cwd=REPO_ROOT, check=False)
            reverted.append(path)
    if reverted:
        print(f"\n[repair] reverted out-of-scope edits: {', '.join(reverted)}")
    return reverted


def _repair_prompt(failure: StepError, target_rev: str, log_path: Path) -> str:
    return (
        f"The libdatadog Rust dependency in src/native was just bumped to {short(target_rev)} (main) "
        f"and the build no longer passes. The failing step was `{failure.label}`. "
        f"Its full output is in {log_path} — read it first.\n\n"
        "Your job: make the native build compile and pass again by adapting our code to libdatadog's "
        "changed API. The breakage may be a renamed/removed/moved crate (fix the dependency entries in "
        "src/native/Cargo.toml — e.g. a `datadog-*` crate renamed to `libdd-*`), a changed function/type "
        "signature, or a moved import. Constraints:\n"
        "- Edit ONLY files under src/native/ and setup.py. Do NOT modify tests, CI config, or anything else.\n"
        "- Do NOT weaken or delete assertions, lints, or tests to force a pass.\n"
        "- Verify your fix by running `cargo build --all-features` (and `cargo clippy --all-features -- -D warnings`) "
        "in src/native before finishing.\n"
        "- If the breakage is NOT a small, mechanical API adaptation (e.g. it needs a design decision or a large "
        "rewrite), stop and explain why rather than forcing a change.\n"
        "When done, briefly summarize what changed and why."
    )


def repair_loop(failure: StepError, target_rev: str, max_iterations: int, artifacts: Path) -> bool:
    """Attempt to fix native build breakage from libdatadog API changes.

    Each iteration: invoke Claude (constrained to src/native + setup.py), revert
    any out-of-scope edits, then re-run validate_native() — the deterministic
    gate the agent cannot influence. Returns True once it passes, else False
    after max_iterations. Only native (cargo) failures are repaired; environment
    issues (e.g. pip) are never routed here.
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
        transcript = run(f"claude-repair-{i}", cmd, cwd=REPO_ROOT, check=False, timeout=CLAUDE_REPAIR_TIMEOUT_S)
        (artifacts / f"repair-transcript-{i}.log").write_text(transcript.output)
        if transcript.returncode == 124:
            print(f"[repair] iteration {i} timed out after {CLAUDE_REPAIR_TIMEOUT_S}s; moving on.")

        _revert_changes_outside_allowed()

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


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target-rev", help="bump to this rev instead of latest main")
    parser.add_argument("--dry-run", action="store_true", help="show changes, modify nothing")
    parser.add_argument("--skip-python", action="store_true", help="cargo-only validation")
    parser.add_argument("--no-push", dest="push", action="store_false", help="commit but do not push/PR")
    parser.add_argument("--max-repair-iterations", type=int, default=3)
    args = parser.parse_args()

    artifacts = Path(os.environ.get("ARTIFACTS_DIR", REPO_ROOT / "libdatadog-update"))
    artifacts.mkdir(parents=True, exist_ok=True)

    # Phase 1 -------------------------------------------------------------- #
    target_rev = args.target_rev or resolve_latest_main_sha()
    pinned = current_pinned_revs()
    print(f"\nTarget rev : {target_rev}")
    print(f"Current pin: {', '.join(sorted(pinned)) or '(none found)'}")

    if pinned == {target_rev}:
        print("Already pinned to target — nothing to do.")
        return 0

    # Phase 2 -------------------------------------------------------------- #
    if args.dry_run:
        n = len(_LIBDD_REV_RE.findall(CARGO_TOML.read_text()))
        print(
            f"\n[dry-run] would rewrite {n} libdatadog rev(s) → {short(target_rev)}, "
            "regenerate Cargo.lock, and validate."
        )
        return 0

    n = rewrite_cargo_toml(target_rev)
    print(f"\nRewrote {n} libdatadog rev(s) in {CARGO_TOML.relative_to(REPO_ROOT)}.")
    regenerate_lockfile()
    rust_version = sync_rust_minimum_version(target_rev)

    # Phase 3 -------------------------------------------------------------- #
    summary_lines = [
        f"Bumps libdatadog to `{short(target_rev)}` (main).",
        "",
        f"- Source: {LIBDATADOG_REPO}/commit/{target_rev}",
        f"- Rewrote {n} `rev` pin(s) in `src/native/Cargo.toml` and regenerated `Cargo.lock`.",
    ]
    if rust_version:
        summary_lines.append(f"- Bumped `RUST_MINIMUM_VERSION` to `{rust_version}` (from libdatadog's toolchain).")

    # Native (cargo) validation — the repairable signal. Only failures here are
    # routed to the AI repair loop.
    converged = True
    repaired = False
    try:
        validate_native()
    except StepError as exc:
        (artifacts / f"{exc.label}.log").write_text(exc.output)
        print(f"\nValidation failed at {exc.label!r}; entering repair loop.")
        # Phase 5 ---------------------------------------------------------- #
        converged = repair_loop(exc, target_rev, args.max_repair_iterations, artifacts)
        repaired = converged
        if not converged:
            summary_lines += [
                "",
                f"> ⚠️ **Native build FAILED** at `{exc.label}` and automated repair did not converge "
                f"after {args.max_repair_iterations} attempt(s). Opened as a **draft** for a human to finish. "
                "See the `repair-*` / `*-after-repair-*` logs in the job artifacts.",
            ]
    if repaired:
        summary_lines.append(
            f"- Build initially broke and was auto-repaired by Claude "
            f"({args.max_repair_iterations}-attempt cap). **Scrutinize the src/native diff.**"
        )

    # Python smoke — best-effort, NOT routed to repair (env issue ≠ API change).
    # The comprehensive Python suite runs in PR CI.
    if converged and not args.skip_python and not validate_python():
        summary_lines.append("- ⚠️ Python import smoke did not pass — verify the native ABI in PR CI.")

    # Phase 4 -------------------------------------------------------------- #
    note = write_release_note(target_rev)
    summary_lines.append(f"- Added release note `{note.relative_to(REPO_ROOT)}`.")
    summary = "\n".join(summary_lines)
    (artifacts / "summary.md").write_text(summary)

    open_pr_or_fallback(
        target_rev, git_branch_name(target_rev), summary, push=args.push, dry_run=args.dry_run, draft=not converged
    )
    print("\nDone." if converged else "\nDone (draft PR — validation did not pass).")
    return 0 if converged else 1


if __name__ == "__main__":
    sys.exit(main())
