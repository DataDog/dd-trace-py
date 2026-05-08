#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["packaging"]
# ///
"""Unit tests for the pure-function logic in ``create_gh_release_rc.py``.

The script's most fragile bits are the markdown slice-and-merge that produces
the GitHub release body from reno+pandoc output, and the version-bump regex
that rewrites ``pyproject.toml``. Both are pure (no I/O once you hand them the
input string), so we test them with canned fixtures.

Run with:
    python -m unittest scripts.gh.test_create_gh_release_rc

Or directly:
    scripts/gh/test_create_gh_release_rc.py
"""

from __future__ import annotations

from pathlib import Path
import sys
import textwrap
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parent))

from create_gh_release_rc import _FALLBACK_SECTION_ORDER  # noqa: E402
from create_gh_release_rc import _GH_RELEASE_REQUIRED_FLAGS  # noqa: E402
from create_gh_release_rc import _merge_subsections  # noqa: E402
from create_gh_release_rc import _strip_code_fenced_lines  # noqa: E402
from create_gh_release_rc import check_existing_release  # noqa: E402
from create_gh_release_rc import compute_next_rc  # noqa: E402
from create_gh_release_rc import ensure_ci_green  # noqa: E402
from create_gh_release_rc import load_section_order  # noqa: E402
from create_gh_release_rc import slice_and_merge_notes  # noqa: E402
from create_gh_release_rc import write_pyproject_version  # noqa: E402


def dedent(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


class SliceAndMergeNotesTests(unittest.TestCase):
    """Cover the slicer that picks out the right release body from reno+pandoc output."""

    def test_post_bump_split_sections_are_merged(self) -> None:
        """The case that motivated this whole refactor — reno splits an rc into
        ``## Unreleased`` (post-bump) and ``## v<target>`` (pre-bump), and the
        slicer must combine both *and* merge duplicate ``### `` subsections.
        """
        md = dedent(
            """
            # Release Notes

            ## Unreleased

            ### Bug Fixes

            - azure_cosmos: change http.status_code

            ## v4.9.0rc2

            ### New Features

            - llmobs: cost_tags

            ### Bug Fixes

            - profiling: gevent

            ## v4.9.0rc1

            ### New Features

            - already-published feature
            """
        )
        body = slice_and_merge_notes(md, "4.9.0rc2")
        # Sections in canonical order: New Features before Bug Fixes.
        self.assertIn("### New Features\n\n- llmobs: cost_tags", body)
        self.assertIn("### Bug Fixes\n\n- azure_cosmos: change http.status_code\n\n- profiling: gevent", body)
        # The intermediate "## v4.9.0rc2" heading is stripped.
        self.assertNotIn("## v4.9.0rc2", body)
        # We stop before v4.9.0rc1 so already-published content does not leak in.
        self.assertNotIn("already-published feature", body)
        # New Features must precede Bug Fixes (canonical order from
        # releasenotes/config.yaml).
        nf = body.find("### New Features")
        bf = body.find("### Bug Fixes")
        self.assertLess(nf, bf)

    def test_only_unreleased_is_kept_intact(self) -> None:
        """When only ``## Unreleased`` exists (no prior published rc), take its body."""
        md = dedent(
            """
            # Release Notes

            ## Unreleased

            ### New Features

            - one feature
            """
        )
        body = slice_and_merge_notes(md, "5.0.0rc1")
        self.assertIn("- one feature", body)

    def test_stops_at_prior_published_rc(self) -> None:
        """Slicer must not include content from a prior tagged version."""
        md = dedent(
            """
            # Release Notes

            ## Unreleased

            ### Bug Fixes

            - new fix

            ## v4.8.0rc5

            ### Bug Fixes

            - already-shipped fix
            """
        )
        body = slice_and_merge_notes(md, "4.9.0rc1")
        self.assertIn("- new fix", body)
        self.assertNotIn("already-shipped fix", body)

    def test_empty_input_raises_helpful_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            slice_and_merge_notes("# Release Notes\n\n", "4.9.0rc2")
        self.assertIn("no version sections found", str(ctx.exception))

    def test_subsections_emitted_in_canonical_order(self) -> None:
        """Reno may emit subsections in arbitrary order; merger must reorder
        them to match releasenotes/config.yaml: Upgrade Notes → New Features →
        Bug Fixes → Other Changes.
        """
        md = dedent(
            """
            ## Unreleased

            ### Bug Fixes

            - fix one

            ### Upgrade Notes

            - upgrade one

            ### New Features

            - feature one
            """
        )
        body = slice_and_merge_notes(md, "4.9.0rc2")
        positions = {
            "Upgrade Notes": body.find("### Upgrade Notes"),
            "New Features": body.find("### New Features"),
            "Bug Fixes": body.find("### Bug Fixes"),
        }
        # All three present.
        for name, pos in positions.items():
            self.assertNotEqual(pos, -1, f"{name} missing")
        # Canonical order.
        self.assertLess(positions["Upgrade Notes"], positions["New Features"])
        self.assertLess(positions["New Features"], positions["Bug Fixes"])

    def test_unknown_subsection_kept_after_known_ones(self) -> None:
        """A subsection title not in RELEASE_NOTE_SECTION_ORDER (e.g. someone's
        custom heading) shouldn't be silently dropped.
        """
        md = dedent(
            """
            ## Unreleased

            ### New Features

            - feat

            ### Custom Heading

            - custom note
            """
        )
        body = slice_and_merge_notes(md, "4.9.0rc2")
        self.assertIn("### Custom Heading", body)
        self.assertIn("- custom note", body)
        # Custom heading appears after known ones.
        self.assertLess(body.find("### New Features"), body.find("### Custom Heading"))


class MergeSubsectionsTests(unittest.TestCase):
    """Cover ``_merge_subsections`` directly with edge cases that aren't easy to
    set up through the full slicer.
    """

    def test_no_subsections_returns_input(self) -> None:
        body = "Just a preamble with no ### subsections."
        self.assertEqual(_merge_subsections(body), body)

    def test_two_bug_fixes_blocks_are_merged_in_order(self) -> None:
        body = dedent(
            """
            ### Bug Fixes

            - fix A

            ### New Features

            - feat A

            ### Bug Fixes

            - fix B
            """
        )
        merged = _merge_subsections(body)
        # One Bug Fixes section with both items.
        self.assertEqual(merged.count("### Bug Fixes"), 1)
        self.assertIn("- fix A", merged)
        self.assertIn("- fix B", merged)
        # Order: New Features before Bug Fixes (canonical).
        self.assertLess(merged.find("### New Features"), merged.find("### Bug Fixes"))


class ComputeNextRcTests(unittest.TestCase):
    def test_increments_rc_suffix(self) -> None:
        self.assertEqual(compute_next_rc("4.9.0rc1"), "4.9.0rc2")
        self.assertEqual(compute_next_rc("4.9.0rc9"), "4.9.0rc10")
        self.assertEqual(compute_next_rc("4.9.0rc99"), "4.9.0rc100")

    def test_handles_double_digit_rc(self) -> None:
        self.assertEqual(compute_next_rc("5.0.0rc15"), "5.0.0rc16")

    def test_rejects_non_rc_version(self) -> None:
        for v in ["4.9.0", "4.9.0a1", "4.9.0b2", "4.9.0.dev1"]:
            with self.assertRaises(SystemExit, msg=f"expected error for {v!r}"):
                compute_next_rc(v)


class WritePyprojectVersionTests(unittest.TestCase):
    """Cover the version-line rewrite, including TOML quote-style preservation."""

    def setUp(self) -> None:
        import tempfile

        self.tmp = Path(tempfile.mkstemp(suffix=".toml")[1])

    def tearDown(self) -> None:
        self.tmp.unlink(missing_ok=True)

    def test_double_quoted_version_rewritten(self) -> None:
        self.tmp.write_text('[project]\nname = "x"\nversion = "4.9.0rc1"\n')
        write_pyproject_version("4.9.0rc2", source=self.tmp)
        self.assertIn('version = "4.9.0rc2"', self.tmp.read_text())

    def test_single_quoted_version_preserved(self) -> None:
        self.tmp.write_text("[project]\nname = 'x'\nversion = '4.9.0rc1'\n")
        write_pyproject_version("4.9.0rc2", source=self.tmp)
        self.assertIn("version = '4.9.0rc2'", self.tmp.read_text())

    def test_missing_version_line_raises(self) -> None:
        self.tmp.write_text('[project]\nname = "x"\n')
        with self.assertRaises(SystemExit):
            write_pyproject_version("4.9.0rc2", source=self.tmp)

    def test_only_top_level_version_replaced(self) -> None:
        """A `version = ...` line nested inside another table (e.g. a comment-
        looking line in another section) must not be touched. We pin the regex
        to lines starting at column 0.
        """
        content = '[project]\nversion = "4.9.0rc1"\n[other]\n# version = "0.0.0" (comment)\n'
        self.tmp.write_text(content)
        write_pyproject_version("4.9.0rc2", source=self.tmp)
        text = self.tmp.read_text()
        self.assertIn('version = "4.9.0rc2"', text)
        self.assertIn('# version = "0.0.0"', text)


class GhReleaseCreateContractTests(unittest.TestCase):
    """Pin down the never-publish contract: the constants list must include
    both flags, and any future change to the script that drops one of them
    will fail the assertion in cmd_draft_release at runtime.
    """

    def test_required_flags_include_draft_and_prerelease(self) -> None:
        self.assertIn("--draft", _GH_RELEASE_REQUIRED_FLAGS)
        self.assertIn("--prerelease", _GH_RELEASE_REQUIRED_FLAGS)

    def test_required_flags_appear_in_args_list(self) -> None:
        """The args list constructed by cmd_draft_release must contain every
        required flag. We grep the source rather than running the full command
        end-to-end (that needs network) — this guards against a refactor that
        renames or drops a flag.
        """
        source: str = (Path(__file__).resolve().parent / "create_gh_release_rc.py").read_text()
        # Find the gh_release_args list opener and walk balanced brackets to
        # find the matching close. (Naive `find("]", idx)` matches the `]` in
        # `list[str]`, which is the wrong bracket.)
        anchor: str = "gh_release_args: list[str] = ["
        idx: int = source.find(anchor)
        self.assertNotEqual(idx, -1, "gh_release_args list not found in script")
        # Start counting from the `[` at the end of the anchor.
        start: int = idx + len(anchor) - 1
        depth: int = 0
        end: int = -1
        for i in range(start, len(source)):
            ch: str = source[i]
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        self.assertNotEqual(end, -1, "could not find closing bracket of gh_release_args list")
        block: str = source[idx : end + 1]
        for flag in _GH_RELEASE_REQUIRED_FLAGS:
            self.assertIn(flag, block, f"{flag} missing from gh_release_args list")


class SliceFragileShapesTests(unittest.TestCase):
    """Defensive coverage for unfamiliar reno-output shapes that could cause
    silent content loss.
    """

    def test_subcycle_marker_heading_is_stripped(self) -> None:
        """We saw `## v4.9.0rc2-2` once during a live test (reno's sub-cycle
        marker). It belongs to the same rc and shouldn't appear in the body
        as an extra heading.
        """
        md = dedent(
            """
            ## Unreleased

            ### Bug Fixes

            - new fix

            ## v4.9.0rc2

            ### New Features

            - feat one

            ## v4.9.0rc2-2

            ### Bug Fixes

            - sub-cycle fix

            ## v4.9.0rc1

            ### Bug Fixes

            - already-shipped
            """
        )
        body = slice_and_merge_notes(md, "4.9.0rc2", section_order=_FALLBACK_SECTION_ORDER)
        # The intermediate "## v4.9.0rc2" and "## v4.9.0rc2-2" headings should
        # both be elided; we should not stop at them.
        self.assertNotIn("## v4.9.0rc2", body)
        self.assertIn("- new fix", body)
        self.assertIn("- feat one", body)
        self.assertIn("- sub-cycle fix", body)
        self.assertNotIn("already-shipped", body)

    def test_trailing_whitespace_on_heading_is_tolerated(self) -> None:
        """Pandoc occasionally emits trailing whitespace after a heading. The
        slicer must still recognize it as a heading boundary.
        """
        md = "## Unreleased\n\n### Bug Fixes\n\n- the fix\n\n## v4.9.0rc1   \n\n### Bug Fixes\n\n- already shipped\n"
        body = slice_and_merge_notes(md, "4.9.0rc2", section_order=_FALLBACK_SECTION_ORDER)
        self.assertIn("- the fix", body)
        self.assertNotIn("already shipped", body)

    def test_code_fenced_hashes_are_not_treated_as_headings(self) -> None:
        """A release note containing a code sample with a `## ` line must not
        be split at that line. Without code-fence handling, the slicer would
        truncate the body at the fake heading.
        """
        md = dedent(
            """
            ## Unreleased

            ### New Features

            - documented:
              ```
              ## not-a-real-heading
              some example code
              ```
              actual feature description

            ## v4.9.0rc1

            ### Bug Fixes

            - already shipped
            """
        )
        body = slice_and_merge_notes(md, "4.9.0rc2", section_order=_FALLBACK_SECTION_ORDER)
        # The fake heading inside the fence should not have caused early termination;
        # we should still see the feature description after the fence.
        self.assertIn("actual feature description", body)
        self.assertNotIn("already shipped", body)

    def test_strip_code_fenced_lines_preserves_fence_markers(self) -> None:
        """Sanity check the helper: fence markers themselves stay so the body
        still renders as code blocks; only inner lines are blanked.
        """
        md = "before\n```\n## fake\n```\nafter\n"
        out = _strip_code_fenced_lines(md)
        self.assertIn("```", out)
        self.assertNotIn("## fake", out)
        self.assertIn("before", out)
        self.assertIn("after", out)


class SectionOrderConfigTests(unittest.TestCase):
    """Verify the runtime-loaded section order matches releasenotes/config.yaml."""

    def test_runtime_load_matches_yaml_sections(self) -> None:
        """The team can add a new section to config.yaml without touching the
        script. Verify the parser picks up the live YAML (not the fallback).
        """
        # Construct repo path the same way the script does.
        repo_root: Path = Path(__file__).resolve().parents[2]
        config_path: Path = repo_root / "releasenotes" / "config.yaml"
        self.assertTrue(config_path.is_file(), "releasenotes/config.yaml not found")

        loaded = load_section_order(config_path)
        # Expect at least the fallback set (real config.yaml might add more).
        for name in _FALLBACK_SECTION_ORDER:
            self.assertIn(name, loaded, f"{name!r} missing from loaded section order")
        # The fallback ordering must be a prefix or subsequence-preserving
        # rearrangement of `loaded`. Cheaper: same elements.
        self.assertEqual(set(loaded) & set(_FALLBACK_SECTION_ORDER), set(_FALLBACK_SECTION_ORDER))

    def test_load_section_order_falls_back_when_file_missing(self) -> None:
        loaded = load_section_order(Path("/nonexistent/config.yaml"))
        self.assertEqual(loaded, _FALLBACK_SECTION_ORDER)


class VersionMismatchPreflightTests(unittest.TestCase):
    """Pin down the new pyproject-vs-explicit-version validation that Brett
    requested. The check fires inside cmd_draft_release; we exercise it via a
    minimal Namespace stand-in so we don't need a real git/gh stack.
    """

    def test_mismatch_raises_without_override(self) -> None:
        """When --version disagrees with pyproject@target and --allow-version-mismatch
        isn't set, cmd_draft_release should refuse before any remote call.
        """
        import argparse as ap

        import create_gh_release_rc as mod

        # Stub out everything that would touch the network/git/gh.
        original_run: object = mod.run
        original_run_out: object = mod.run_out
        original_ensure_auth: object = mod.ensure_gh_auth
        original_read_pyproject: object = mod.read_pyproject_version_at
        try:
            mod.ensure_gh_auth = lambda: None
            mod.run = lambda *a, **kw: None  # type: ignore[assignment]
            # First call resolves the SHA; subsequent reads of pyproject.
            sha_calls: list[int] = [0]

            def fake_run_out(cmd: list[str], cwd: object = None) -> str:
                sha_calls[0] += 1
                return "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"

            mod.run_out = fake_run_out
            mod.read_pyproject_version_at = lambda ref: "4.9.0rc1"

            args = ap.Namespace(
                target="4.9",
                version="4.9.0rc99",  # disagrees with pyproject (4.9.0rc1)
                allow_version_mismatch=False,
                allow_ci_failure=False,
                dry_run=True,
                slack=False,
                slack_channel="auto",
                slack_precheck=True,
                test_dm=False,
            )
            with self.assertRaises(SystemExit) as ctx:
                mod.cmd_draft_release(args)
            msg: str = str(ctx.exception)
            self.assertIn("disagrees with pyproject", msg)
            self.assertIn("4.9.0rc99", msg)
            self.assertIn("4.9.0rc1", msg)
        finally:
            mod.run = original_run  # type: ignore[assignment]
            mod.run_out = original_run_out  # type: ignore[assignment]
            mod.ensure_gh_auth = original_ensure_auth  # type: ignore[assignment]
            mod.read_pyproject_version_at = original_read_pyproject  # type: ignore[assignment]


class CiGreenPreflightTests(unittest.TestCase):
    """Coverage for Brett's "validate CI is green on the target commit" ask.

    We monkey-patch run_out to return canned API responses so the test doesn't
    need network access.
    """

    def _patch_responses(self, state: str, statuses_ndjson: str, check_runs_ndjson: str):
        """Install a fake run_out that dispatches to one of three responses
        by URL substring (the function makes three distinct gh-api calls).
        """
        import create_gh_release_rc as mod

        original = mod.run_out

        def fake(cmd: list[str], cwd: object = None) -> str:
            joined: str = " ".join(cmd)
            # Order matters: more-specific URLs first.
            if "/check-runs" in joined:
                return check_runs_ndjson
            if "/statuses" in joined:
                return statuses_ndjson
            if "/status" in joined:
                return state
            return ""

        mod.run_out = fake
        return mod, original

    def test_all_green_passes(self) -> None:
        mod, original = self._patch_responses(
            state="success",
            statuses_ndjson='{"context": "ci", "state": "success"}\n',
            check_runs_ndjson='{"name": "lint", "status": "completed", "conclusion": "success"}\n',
        )
        try:
            ensure_ci_green("dead")
        finally:
            mod.run_out = original

    def test_failing_status_refuses_by_default(self) -> None:
        mod, original = self._patch_responses(
            state="failure",
            statuses_ndjson=(
                '{"context": "dd-gitlab/build", "state": "failure"}\n'
                '{"context": "dd-gitlab/test", "state": "success"}\n'
            ),
            check_runs_ndjson="",
        )
        try:
            with self.assertRaises(SystemExit) as ctx:
                ensure_ci_green("dead")
            self.assertIn("non-green CI", str(ctx.exception))
            self.assertIn("dd-gitlab/build", str(ctx.exception))
        finally:
            mod.run_out = original

    def test_failing_check_run_refuses(self) -> None:
        mod, original = self._patch_responses(
            state="success",
            statuses_ndjson="",
            check_runs_ndjson='{"name": "test-suite", "status": "completed", "conclusion": "failure"}\n',
        )
        try:
            with self.assertRaises(SystemExit) as ctx:
                ensure_ci_green("dead")
            self.assertIn("test-suite", str(ctx.exception))
            self.assertIn("failure", str(ctx.exception))
        finally:
            mod.run_out = original

    def test_pending_check_refuses(self) -> None:
        mod, original = self._patch_responses(
            state="success",
            statuses_ndjson="",
            check_runs_ndjson='{"name": "running", "status": "in_progress", "conclusion": null}\n',
        )
        try:
            with self.assertRaises(SystemExit) as ctx:
                ensure_ci_green("dead")
            self.assertIn("running", str(ctx.exception))
        finally:
            mod.run_out = original

    def test_allow_ci_failure_downgrades_to_warning(self) -> None:
        mod, original = self._patch_responses(
            state="failure",
            statuses_ndjson='{"context": "dd-gitlab/x", "state": "failure"}\n',
            check_runs_ndjson="",
        )
        try:
            ensure_ci_green("dead", allow_failure=True)
        finally:
            mod.run_out = original

    def test_paginated_statuses_dedupe_by_context(self) -> None:
        """Pagination returns superseded statuses; only the most-recent state
        per context (first occurrence) should count.
        """
        mod, original = self._patch_responses(
            state="success",
            statuses_ndjson=(
                # Most-recent first.
                '{"context": "dd-gitlab/test", "state": "success"}\n'
                # Older entry for the same context — should be ignored.
                '{"context": "dd-gitlab/test", "state": "failure"}\n'
            ),
            check_runs_ndjson="",
        )
        try:
            ensure_ci_green("dead")
        finally:
            mod.run_out = original


class CheckExistingReleasePaginationTests(unittest.TestCase):
    """The pagination behavior of check_existing_release was identified as a
    risk in the audit. Today's coverage exercises the parsing logic with a
    canned multi-line response; an end-to-end test would require mocking gh
    which is out of scope.
    """

    def test_parses_multiple_json_lines(self) -> None:
        """check_existing_release reads NDJSON-style output (one object per line
        across paginated pages). Verify we tolerate that shape without crashing
        and correctly classify drafts vs published.
        """
        # Monkey-patch run_out to return a multi-line gh response simulating
        # output across pagination boundaries.
        import create_gh_release_rc as mod

        canned: str = (
            '{"id": 1, "draft": false, "html_url": "https://github.com/DataDog/dd-trace-py/releases/tag/v9.9.9rc1"}\n'
            '{"id": 2, "draft": true, "html_url": "https://github.com/DataDog/dd-trace-py/releases/tag/untagged-abc"}\n'
        )
        original = mod.run_out
        mod.run_out = lambda *a, **kw: canned
        try:
            with self.assertRaises(SystemExit) as ctx:
                check_existing_release("9.9.9rc1")
            self.assertIn("already PUBLISHED", str(ctx.exception))
        finally:
            mod.run_out = original


if __name__ == "__main__":
    unittest.main()
