#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging"]
# ///
"""Unit tests for ``create_gh_release.py`` (fixture-driven, no network).

Run with:
    scripts/gh/test_create_gh_release.py
    # or:
    python -m unittest scripts.gh.test_create_gh_release
"""

from __future__ import annotations

from pathlib import Path
import sys
import textwrap
from typing import Any
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))

from create_gh_release import _GH_RELEASE_REQUIRED_FLAGS  # noqa: E402
from create_gh_release import ReleaseState  # noqa: E402
from create_gh_release import WaitingOn  # noqa: E402
from create_gh_release import build_gh_release_args  # noqa: E402
from create_gh_release import compose_release_body  # noqa: E402
from create_gh_release import compute_final_version  # noqa: E402
from create_gh_release import compute_next_minor_rc1  # noqa: E402
from create_gh_release import compute_next_patch  # noqa: E402
from create_gh_release import compute_next_rc  # noqa: E402
from create_gh_release import format_notes_for_version  # noqa: E402
from create_gh_release import is_rc_version  # noqa: E402
from create_gh_release import is_stable_version  # noqa: E402
from create_gh_release import review_text  # noqa: E402
from create_gh_release import should_mark_latest  # noqa: E402
from create_gh_release import write_pyproject_version  # noqa: E402
from create_gh_release import xy_branch_for_version  # noqa: E402


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


class VersionMathTests(unittest.TestCase):
    def test_compute_next_rc(self) -> None:
        self.assertEqual(compute_next_rc("4.9.0rc1"), "4.9.0rc2")
        self.assertEqual(compute_next_rc("4.9.0rc9"), "4.9.0rc10")

    def test_compute_next_rc_rejects_stable(self) -> None:
        with self.assertRaises(SystemExit):
            compute_next_rc("4.9.0")

    def test_compute_next_patch(self) -> None:
        self.assertEqual(compute_next_patch("4.16.1"), "4.16.2")
        self.assertEqual(compute_next_patch("v4.16.2"), "4.16.3")

    def test_compute_next_minor_rc1(self) -> None:
        self.assertEqual(compute_next_minor_rc1("4.16.0"), "4.17.0rc1")

    def test_compute_final_version(self) -> None:
        self.assertEqual(compute_final_version("4.17"), "4.17.0")

    def test_is_rc_and_stable(self) -> None:
        self.assertTrue(is_rc_version("4.17.0rc3"))
        self.assertFalse(is_rc_version("4.16.2"))
        self.assertTrue(is_stable_version("4.16.2"))
        self.assertFalse(is_stable_version("4.17.0rc1"))

    def test_xy_branch_for_version(self) -> None:
        self.assertEqual(xy_branch_for_version("4.16.2"), "4.16")
        self.assertEqual(xy_branch_for_version("v4.17.0rc1"), "4.17")


class LatestFlagTests(unittest.TestCase):
    def test_patch_on_latest_line_is_latest(self) -> None:
        published: list[str] = ["v4.15.3", "v4.16.1"]
        self.assertTrue(should_mark_latest("4.16.2", published))

    def test_older_line_patch_is_not_latest(self) -> None:
        published: list[str] = ["v4.15.3", "v4.16.1"]
        self.assertFalse(should_mark_latest("4.15.4", published))

    def test_rc_never_latest(self) -> None:
        self.assertFalse(should_mark_latest("4.17.0rc1", ["v4.16.1"]))

    def test_equal_to_published_is_not_latest(self) -> None:
        self.assertFalse(should_mark_latest("4.16.1", ["v4.16.1"]))


class GhReleaseFlagTests(unittest.TestCase):
    def test_required_flags_are_draft_only(self) -> None:
        self.assertEqual(_GH_RELEASE_REQUIRED_FLAGS, ("--draft",))
        self.assertNotIn("--prerelease", _GH_RELEASE_REQUIRED_FLAGS)

    def test_rc_args_include_prerelease_and_not_latest(self) -> None:
        args: list[str] = build_gh_release_args("4.17.0rc2", "abc", "notes.md", latest=False)
        self.assertIn("--draft", args)
        self.assertIn("--prerelease", args)
        self.assertIn("--latest=false", args)

    def test_patch_args_latest_true(self) -> None:
        args: list[str] = build_gh_release_args("4.16.2", "abc", "notes.md", latest=True)
        self.assertIn("--draft", args)
        self.assertNotIn("--prerelease", args)
        self.assertIn("--latest=true", args)

    def test_older_patch_args_latest_false(self) -> None:
        args: list[str] = build_gh_release_args("4.15.4", "abc", "notes.md", latest=False)
        self.assertIn("--latest=false", args)
        self.assertNotIn("--prerelease", args)


class NotesFormattingTests(unittest.TestCase):
    def test_format_notes_for_version_uses_formatter(self) -> None:
        raw: str = dedent(
            """
            # Release Notes

            ## Unreleased

            ### Bug Fixes

            - tracing: Fixes a thing.

            ## v4.16.1

            ### Bug Fixes

            - profiling: Already shipped.
            """
        )
        body: str = format_notes_for_version(raw, "v4.16.2")
        self.assertIn("tracing", body)
        self.assertNotIn("Already shipped", body)

    def test_collapse_rcs_for_final(self) -> None:
        raw: str = dedent(
            """
            # Release Notes

            ## v4.17.0rc2

            ### New Features

            - tracing: Feature two.

            ## v4.17.0rc1

            ### New Features

            - tracing: Feature one.

            ## v4.16.0

            ### Bug Fixes

            - other: Prior line.
            """
        )
        body: str = format_notes_for_version(
            raw,
            "v4.17.0rc1",
            collapse_release_candidates=True,
            exclude_unreleased=True,
        )
        self.assertIn("Feature one", body)
        self.assertIn("Feature two", body)
        self.assertNotIn("Prior line", body)


class EolComposeTests(unittest.TestCase):
    def test_compose_appends_support_link_when_missing(self) -> None:
        body: str = compose_release_body("- note", "Estimated end-of-life date: 05-2027")
        self.assertIn("Estimated end-of-life date: 05-2027", body)
        self.assertIn("support level definitions", body)
        self.assertIn("- note", body)

    def test_review_text_template(self) -> None:
        text: str = review_text("4.16.2", "https://example/draft")
        self.assertIn("4.16.2", text)
        self.assertIn(":lgtm:", text)
        self.assertIn("https://example/draft", text)


class WritePyprojectVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self.tmp: Path = Path(tempfile.mkstemp(suffix=".toml")[1])

    def tearDown(self) -> None:
        self.tmp.unlink(missing_ok=True)

    def test_double_quoted_version_rewritten(self) -> None:
        self.tmp.write_text('[project]\nname = "x"\nversion = "4.9.0rc1"\n')
        write_pyproject_version("4.9.0rc2", source=self.tmp)
        self.assertIn('version = "4.9.0rc2"', self.tmp.read_text())


class ReleaseStateResolverTests(unittest.TestCase):
    def test_report_prints_waiting_on(self) -> None:
        state: ReleaseState = ReleaseState(branch="4.16", version="4.16.2")
        state.did.append("loaded PR")
        state.waiting_on = "merge of backport"
        with mock.patch("builtins.print") as printed:
            state.report()
        calls: list[str] = [str(c.args[0]) for c in printed.call_args_list]
        self.assertTrue(any(c.startswith("did: loaded PR") for c in calls))
        self.assertTrue(any("waiting on: merge of backport" in c for c in calls))

    def test_waiting_on_exception_message(self) -> None:
        with self.assertRaises(WaitingOn) as ctx:
            raise WaitingOn("review of PR #1")
        self.assertEqual(ctx.exception.message, "review of PR #1")


class DraftReleaseMismatchTests(unittest.TestCase):
    def test_mismatch_raises_without_override(self) -> None:
        import argparse as ap

        import create_gh_release as mod

        original_run: Any = mod.run
        original_run_out: Any = mod.run_out
        original_ensure_auth: Any = mod.ensure_gh_auth
        original_read_pyproject: Any = mod.read_pyproject_version_at
        try:
            setattr(mod, "ensure_gh_auth", lambda: None)
            setattr(mod, "run", lambda *a, **kw: None)

            def fake_run_out(cmd: list[str], cwd: object = None) -> str:
                return "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

            setattr(mod, "run_out", fake_run_out)
            setattr(mod, "read_pyproject_version_at", lambda ref: "4.9.0rc1")

            args = ap.Namespace(
                target="4.9",
                version="4.9.0rc99",
                allow_version_mismatch=False,
                allow_ci_failure=False,
                dry_run=True,
                slack=False,
                slack_channel="auto",
                slack_precheck=True,
                test_dm=False,
                collapse_rcs=False,
                earliest_version=None,
            )
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_draft_release(args)
            msg: str = str(ctx.exception)
            self.assertIn("disagrees with pyproject", msg)
        finally:
            setattr(mod, "run", original_run)
            setattr(mod, "run_out", original_run_out)
            setattr(mod, "ensure_gh_auth", original_ensure_auth)
            setattr(mod, "read_pyproject_version_at", original_read_pyproject)


class CiGreenPreflightTests(unittest.TestCase):
    def _patch_responses(self, state: str, statuses_ndjson: str, check_runs_ndjson: str) -> tuple[Any, Any]:
        import create_gh_release as mod

        original: Any = mod.run_out

        def fake(cmd: list[str], cwd: object = None) -> str:
            joined: str = " ".join(cmd)
            if "/check-runs" in joined:
                return check_runs_ndjson
            if "/statuses" in joined:
                return statuses_ndjson
            if "/status" in joined:
                return state
            return ""

        setattr(mod, "run_out", fake)
        return mod, original

    def test_failing_status_refuses(self) -> None:
        from create_gh_release import ensure_ci_green

        mod, original = self._patch_responses(
            state="failure",
            statuses_ndjson='{"context": "dd-gitlab/build", "state": "failure"}\n',
            check_runs_ndjson="",
        )
        try:
            with self.assertRaises(SystemExit) as ctx:
                ensure_ci_green("dead")
            self.assertIn("non-green CI", str(ctx.exception))
        finally:
            mod.run_out = original


if __name__ == "__main__":
    unittest.main()
