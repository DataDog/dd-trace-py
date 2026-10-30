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

import os
from pathlib import Path
import subprocess  # nosec B404 — only to build CalledProcessError fixtures for wait tests
import sys
import tempfile
import textwrap
from typing import Any
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parent))

from create_gh_release import _GH_RELEASE_REQUIRED_FLAGS  # noqa: E402
from create_gh_release import _RELEASE_SCRIPT_BRANCH_URL  # noqa: E402
from create_gh_release import SLACK_CHANNEL_DEFAULT  # noqa: E402
from create_gh_release import SLACK_CHANNEL_GUILD_ENV  # noqa: E402
from create_gh_release import SLACK_CHANNEL_PROD_ENV  # noqa: E402
from create_gh_release import SLACK_CHANNEL_PROD_ENV_LEGACY  # noqa: E402
from create_gh_release import SLACK_DM_ENV  # noqa: E402
from create_gh_release import SLACK_USER_BRETT_ID  # noqa: E402
from create_gh_release import SLACK_USER_EMMETT_ID  # noqa: E402
from create_gh_release import ReleaseState  # noqa: E402
from create_gh_release import WaitingOn  # noqa: E402
from create_gh_release import _is_version_bump_pr_title  # noqa: E402
from create_gh_release import auto_created_footer  # noqa: E402
from create_gh_release import build_gh_release_args  # noqa: E402
from create_gh_release import build_parser  # noqa: E402
from create_gh_release import build_version_bump_pr_body  # noqa: E402
from create_gh_release import compose_release_body  # noqa: E402
from create_gh_release import compute_final_version  # noqa: E402
from create_gh_release import compute_next_minor_rc1  # noqa: E402
from create_gh_release import compute_next_patch  # noqa: E402
from create_gh_release import compute_next_rc  # noqa: E402
from create_gh_release import draft_kind_phrase  # noqa: E402
from create_gh_release import format_included_since_section  # noqa: E402
from create_gh_release import format_notes_for_version  # noqa: E402
from create_gh_release import format_slack_announce  # noqa: E402
from create_gh_release import generated_by_pr_line  # noqa: E402
from create_gh_release import is_rc_version  # noqa: E402
from create_gh_release import is_stable_version  # noqa: E402
from create_gh_release import load_local_env  # noqa: E402
from create_gh_release import print_slack_announce_preview  # noqa: E402
from create_gh_release import resolve_runner_display  # noqa: E402
from create_gh_release import review_text  # noqa: E402
from create_gh_release import should_mark_latest  # noqa: E402
from create_gh_release import slack_mention  # noqa: E402
from create_gh_release import write_pyproject_version  # noqa: E402
from create_gh_release import xy_branch_for_version  # noqa: E402


_FAKE_RUNNER: str = "<https://github.com/test-runner|test-runner>"


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
        with mock.patch("create_gh_release.resolve_runner_display", return_value=_FAKE_RUNNER):
            text: str = review_text("4.16.2", "https://example/draft")
        self.assertIn("4.16.2", text)
        self.assertIn(":lgtm:", text)
        self.assertIn("https://example/draft", text)
        self.assertIn(f"Run by {_FAKE_RUNNER}", text)

    def test_draft_kind_phrase(self) -> None:
        self.assertEqual(draft_kind_phrase("4.17.0rc2"), "RC release")
        self.assertEqual(draft_kind_phrase("4.16.2"), "patch release")
        self.assertEqual(draft_kind_phrase("4.17.0"), "release")


class SlackDefaultsTests(unittest.TestCase):
    def test_hardcoded_approver_ids_are_real_looking(self) -> None:
        self.assertTrue(SLACK_USER_BRETT_ID.startswith("U"))
        self.assertTrue(SLACK_USER_EMMETT_ID.startswith("U"))
        self.assertNotEqual(SLACK_USER_BRETT_ID, SLACK_USER_EMMETT_ID)

    def test_slack_mention_uses_hardcoded_default(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            brett: str = slack_mention("DD_SLACK_USER_BRETT", SLACK_USER_BRETT_ID)
            emmett: str = slack_mention("DD_SLACK_USER_EMMETT", SLACK_USER_EMMETT_ID)
        self.assertEqual(brett, f"<@{SLACK_USER_BRETT_ID}>")
        self.assertEqual(emmett, f"<@{SLACK_USER_EMMETT_ID}>")

    def test_slack_mention_env_override(self) -> None:
        with mock.patch.dict("os.environ", {"DD_SLACK_USER_BRETT": "UOVERRIDE"}, clear=True):
            mention: str = slack_mention("DD_SLACK_USER_BRETT", SLACK_USER_BRETT_ID)
        self.assertEqual(mention, "<@UOVERRIDE>")

    def test_intent_commands_accept_slack_flags(self) -> None:
        parser = build_parser()
        for argv in (
            ["patch", "1", "--branch", "4.16", "--test-dm", "--no-slack-precheck"],
            ["rc", "--slack", "--slack-channel", "prod"],
            ["final", "4.17", "--slack", "--test-dm"],
            ["draft-release", "--test-dm"],
            ["rc", "--slack", "--slack-channel", "guild"],
        ):
            ns = parser.parse_args(argv)
            self.assertTrue(hasattr(ns, "slack"))
            self.assertTrue(hasattr(ns, "test_dm"))
            self.assertTrue(hasattr(ns, "slack_channel"))
            self.assertTrue(hasattr(ns, "slack_precheck"))

    def test_slack_channel_defaults_to_prod(self) -> None:
        parser = build_parser()
        ns = parser.parse_args(["rc", "--slack"])
        self.assertEqual(ns.slack_channel, "prod")
        self.assertEqual(ns.slack_channel, SLACK_CHANNEL_DEFAULT)

    def test_format_slack_announce_template(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("create_gh_release.resolve_runner_display", return_value=_FAKE_RUNNER):
                body: str = format_slack_announce("4.17.0rc2", "abc123", "https://example/draft")
        self.assertIn("*Draft RC release*", body)
        self.assertIn("`4.17.0rc2`", body)
        self.assertIn("<https://example/draft|Open draft release / notes>", body)
        self.assertIn("target `abc123`", body)
        self.assertIn(f"<@{SLACK_USER_BRETT_ID}>", body)
        self.assertIn(f"<@{SLACK_USER_EMMETT_ID}>", body)
        self.assertIn(":lgtm:", body)
        self.assertIn("*Publish release*", body)
        self.assertIn(f"Run by {_FAKE_RUNNER}", body)
        self.assertIn("release automation", body)
        self.assertIn("scripts/gh/create_gh_release.py", body)
        self.assertIn(
            "https://github.com/DataDog/dd-trace-py/compare/main...vlad/release-create-release-rc-script",
            body,
        )
        self.assertIn("|vlad/release-create-release-rc-script>", body)
        self.assertNotIn("Draft patch release", body)

    def test_print_slack_announce_preview_prints_exact_body(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with mock.patch("create_gh_release.resolve_runner_display", return_value=_FAKE_RUNNER):
                expected: str = format_slack_announce("4.16.2", "deadbeef", "https://example/draft")
                with mock.patch("builtins.print") as print_mock:
                    print_slack_announce_preview(
                        version="4.16.2",
                        target_sha="deadbeef",
                        draft_url="https://example/draft",
                        test_dm=False,
                        channel="prod",
                    )
        printed: list[str] = [str(c.args[0]) for c in print_mock.call_args_list if c.args]
        self.assertTrue(any("#apm-python-release" in line for line in printed))
        self.assertIn("----- SLACK MESSAGE -----", printed)
        self.assertIn(expected, printed)
        self.assertIn("----- END SLACK MESSAGE -----", printed)

    def test_auto_created_footer_includes_runner(self) -> None:
        with mock.patch("create_gh_release.resolve_runner_display", return_value=_FAKE_RUNNER):
            footer: str = auto_created_footer()
        self.assertIn("release automation", footer)
        self.assertIn("scripts/gh/create_gh_release.py", footer)
        self.assertIn("|vlad/release-create-release-rc-script>", footer)
        self.assertIn(f"Run by {_FAKE_RUNNER}", footer)
        self.assertIn("Try `patch` / `rc` / `final`.", footer)

    def test_generated_by_pr_line_links_feature_branch(self) -> None:
        line: str = generated_by_pr_line()
        self.assertEqual(
            line,
            f"_note: generated by [scripts/gh/create_gh_release.py]({_RELEASE_SCRIPT_BRANCH_URL})._",
        )
        with_extra: str = generated_by_pr_line("bump-rc")
        self.assertEqual(
            with_extra,
            f"_note: generated by [scripts/gh/create_gh_release.py]({_RELEASE_SCRIPT_BRANCH_URL}) bump-rc._",
        )
        self.assertIn(
            "/compare/main...vlad/release-create-release-rc-script",
            line,
        )

    def test_is_version_bump_pr_title(self) -> None:
        self.assertTrue(_is_version_bump_pr_title("chore: bump version to 4.16.2"))
        self.assertTrue(_is_version_bump_pr_title("chore: set version to 4.17.0"))
        self.assertTrue(_is_version_bump_pr_title("chore: bump 4.17.0rc2"))
        self.assertFalse(_is_version_bump_pr_title("fix(falcon): support asgi apps [backport 4.16]"))

    def test_format_included_since_section(self) -> None:
        empty: str = format_included_since_section("v4.16.1", [])
        self.assertEqual(empty, "Included since v4.16.1:\n- (none yet)")
        filled: str = format_included_since_section(
            "v4.16.1",
            [
                {
                    "number": 21387,
                    "title": "fix(falcon): support asgi apps [backport 4.16]",
                    "url": "https://github.com/DataDog/dd-trace-py/pull/21387",
                },
                {
                    "number": 21391,
                    "title": "fix(compat): singledispatchmethod [backport 4.16]",
                    "url": "https://github.com/DataDog/dd-trace-py/pull/21391",
                },
            ],
        )
        self.assertIn("Included since v4.16.1:", filled)
        self.assertIn(
            "- [#21387](https://github.com/DataDog/dd-trace-py/pull/21387): "
            "fix(falcon): support asgi apps [backport 4.16]",
            filled,
        )
        self.assertIn("[#21391](https://github.com/DataDog/dd-trace-py/pull/21391)", filled)

    def test_build_version_bump_pr_body_lists_included_prs(self) -> None:
        prs: list[dict[str, Any]] = [
            {
                "number": 21387,
                "title": "fix(falcon): support asgi apps [backport 4.16]",
                "url": "https://github.com/DataDog/dd-trace-py/pull/21387",
            },
            {
                "number": 21391,
                "title": "fix(compat): singledispatchmethod [backport 4.16]",
                "url": "https://github.com/DataDog/dd-trace-py/pull/21391",
            },
        ]
        with mock.patch(
            "create_gh_release.resolve_previous_published_tag",
            return_value="v4.16.1",
        ):
            with mock.patch(
                "create_gh_release.list_merged_prs_since",
                return_value=prs,
            ) as list_mock:
                body: str = build_version_bump_pr_body(
                    base_branch="4.16",
                    new_version="4.16.2",
                )
        list_mock.assert_called_once_with("v4.16.1", "origin/4.16", base_branch="4.16")
        self.assertNotIn("chore: bump version to 4.16.2", body)
        self.assertTrue(body.startswith("Included since v4.16.1:"))
        self.assertIn("[#21387](https://github.com/DataDog/dd-trace-py/pull/21387)", body)
        self.assertIn("[#21391](https://github.com/DataDog/dd-trace-py/pull/21391)", body)
        self.assertIn(
            f"_note: generated by [scripts/gh/create_gh_release.py]({_RELEASE_SCRIPT_BRANCH_URL})._",
            body,
        )
        # Footer stays after the included list.
        self.assertLess(body.index("Included since"), body.index("note: generated by"))

    def test_build_version_bump_pr_body_omits_section_without_prev_tag(self) -> None:
        with mock.patch(
            "create_gh_release.resolve_previous_published_tag",
            return_value=None,
        ):
            body: str = build_version_bump_pr_body(
                base_branch="main",
                new_version="4.18.0rc1",
                generated_by_extra="bump-rc",
                body_extra="Once merged, run draft-release.",
            )
        self.assertNotIn("Included since", body)
        self.assertNotIn("chore: bump", body)
        self.assertIn("Once merged, run draft-release.", body)
        self.assertIn(
            f"_note: generated by [scripts/gh/create_gh_release.py]({_RELEASE_SCRIPT_BRANCH_URL}) bump-rc._",
            body,
        )

    def test_resolve_runner_display_prefers_gh_login(self) -> None:
        import create_gh_release as mod

        fake_gh: mock.Mock = mock.Mock(returncode=0, stdout="vlad-scherbich\n", stderr="")
        with mock.patch.object(mod, "_RUNNER_DISPLAY_CACHE", None):
            with mock.patch("create_gh_release.subprocess.run", return_value=fake_gh) as run_mock:
                display: str = resolve_runner_display()
        self.assertEqual(display, "<https://github.com/vlad-scherbich|vlad-scherbich>")
        run_mock.assert_called_once()
        argv: list[str] = run_mock.call_args.args[0]
        self.assertEqual(argv[:3], ["gh", "api", "user"])

    def test_resolve_runner_display_falls_back_to_git_name(self) -> None:
        import create_gh_release as mod

        gh_fail: mock.Mock = mock.Mock(returncode=1, stdout="", stderr="boom")
        git_ok: mock.Mock = mock.Mock(returncode=0, stdout="Local Name\n", stderr="")

        def fake_run(cmd: list[str], **_kwargs: Any) -> mock.Mock:
            if cmd and cmd[0] == "gh":
                return gh_fail
            return git_ok

        with mock.patch.object(mod, "_RUNNER_DISPLAY_CACHE", None):
            with mock.patch("create_gh_release.subprocess.run", side_effect=fake_run):
                display: str = resolve_runner_display()
        self.assertEqual(display, "`Local Name`")

    def test_resolve_runner_display_caches(self) -> None:
        import create_gh_release as mod

        fake_gh: mock.Mock = mock.Mock(returncode=0, stdout="cached-user\n", stderr="")
        with mock.patch.object(mod, "_RUNNER_DISPLAY_CACHE", None):
            with mock.patch("create_gh_release.subprocess.run", return_value=fake_gh) as run_mock:
                first: str = resolve_runner_display()
                second: str = resolve_runner_display()
        self.assertEqual(first, second)
        self.assertEqual(first, "<https://github.com/cached-user|cached-user>")
        self.assertEqual(run_mock.call_count, 1)

    def test_preferred_env_names(self) -> None:
        self.assertEqual(SLACK_CHANNEL_PROD_ENV, "DDTRACE_RELEASE_APM_PYTHON_RELEASE_CHANNEL_URL")
        self.assertEqual(SLACK_CHANNEL_PROD_ENV_LEGACY, "DD_RELEASE_SLACK_WEBHOOK_URL_PROD")
        self.assertEqual(SLACK_CHANNEL_GUILD_ENV, "DDTRACE_RELEASE_GUILD_CHANNEL_URL")
        self.assertEqual(SLACK_DM_ENV, "DDTRACE_RELEASE_DM_URL")
        self.assertEqual(SLACK_CHANNEL_DEFAULT, "prod")

    def test_notify_slack_test_dm_only(self) -> None:
        import create_gh_release as mod

        posted: list[tuple[str, str]] = []

        def fake_post(url: str, text: str) -> None:
            posted.append((url, text))

        with mock.patch.dict(
            "os.environ",
            {
                SLACK_DM_ENV: "https://example.test/dm",
                SLACK_CHANNEL_PROD_ENV: "https://example.test/prod",
            },
            clear=True,
        ):
            with mock.patch.object(mod, "post_webhook", fake_post):
                with mock.patch.object(mod, "resolve_runner_display", return_value=_FAKE_RUNNER):
                    mod.notify_slack(
                        version="4.16.2",
                        target_sha="deadbeef",
                        draft_url="https://example/draft",
                        test_dm=True,
                        channel="prod",
                        precheck=False,
                    )
        self.assertEqual(len(posted), 1)
        url: str
        body: str
        url, body = posted[0]
        self.assertEqual(url, "https://example.test/dm")
        self.assertTrue(body.startswith("[TEST] "))
        self.assertIn("*Draft patch release*", body)
        self.assertIn(f"Run by {_FAKE_RUNNER}", body)

    def test_notify_slack_prod_channel(self) -> None:
        import create_gh_release as mod

        posted: list[tuple[str, str]] = []

        def fake_post(url: str, text: str) -> None:
            posted.append((url, text))

        with mock.patch.dict(
            "os.environ",
            {SLACK_CHANNEL_PROD_ENV: "https://example.test/prod"},
            clear=True,
        ):
            with mock.patch.object(mod, "post_webhook", fake_post):
                mod.notify_slack(
                    version="4.17.0",
                    target_sha="cafebabe",
                    draft_url="https://example/final",
                    test_dm=False,
                    channel="prod",
                    precheck=False,
                )
        self.assertEqual(len(posted), 1)
        url: str
        body: str
        url, body = posted[0]
        self.assertEqual(url, "https://example.test/prod")
        self.assertIn("*Draft release*", body)
        self.assertNotIn("[TEST]", body)

    def test_notify_slack_prod_legacy_env(self) -> None:
        import create_gh_release as mod

        posted: list[tuple[str, str]] = []

        def fake_post(url: str, text: str) -> None:
            posted.append((url, text))

        with mock.patch.dict(
            "os.environ",
            {SLACK_CHANNEL_PROD_ENV_LEGACY: "https://example.test/prod-legacy"},
            clear=True,
        ):
            with mock.patch.object(mod, "post_webhook", fake_post):
                mod.notify_slack(
                    version="4.17.0",
                    target_sha="cafebabe",
                    draft_url="https://example/final",
                    test_dm=False,
                    channel="prod",
                    precheck=False,
                )
        self.assertEqual(len(posted), 1)
        url: str
        _body: str
        url, _body = posted[0]
        self.assertEqual(url, "https://example.test/prod-legacy")

    def test_notify_slack_guild_channel_escape_hatch(self) -> None:
        import create_gh_release as mod

        posted: list[tuple[str, str]] = []

        def fake_post(url: str, text: str) -> None:
            posted.append((url, text))

        with mock.patch.dict(
            "os.environ",
            {SLACK_CHANNEL_GUILD_ENV: "https://example.test/guild"},
            clear=True,
        ):
            with mock.patch.object(mod, "post_webhook", fake_post):
                mod.notify_slack(
                    version="4.17.0",
                    target_sha="cafebabe",
                    draft_url="https://example/final",
                    test_dm=False,
                    channel="guild",
                    precheck=False,
                )
        self.assertEqual(len(posted), 1)
        url: str
        body: str
        url, body = posted[0]
        self.assertEqual(url, "https://example.test/guild")
        self.assertIn("*Draft release*", body)
        self.assertNotIn("[TEST]", body)


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

    def test_pending_without_wait_raises_waiting_on(self) -> None:
        from create_gh_release import ensure_ci_green

        mod, original = self._patch_responses(
            state="pending",
            statuses_ndjson='{"context": "dd-gitlab/build", "state": "pending"}\n',
            check_runs_ndjson="",
        )
        try:
            with self.assertRaises(WaitingOn) as ctx:
                ensure_ci_green("abc123deadbeef", wait=False)
            self.assertIn("CI green on abc123deadbe", str(ctx.exception))
        finally:
            mod.run_out = original


class WaitForCiGreenTests(unittest.TestCase):
    """Poll helper: fake gh status via summary_fn + fake clock (no real sleep)."""

    def test_already_green_returns_without_sleep(self) -> None:
        from create_gh_release import wait_for_ci_green

        sleeps: list[float] = []
        clock: list[float] = [0.0]

        def summary(_sha: str) -> tuple[str, list[str], list[str], int]:
            return ("success", [], [], 3)

        def sleep_fn(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        wait_for_ci_green(
            "deadbeef",
            timeout_s=100.0,
            poll_interval_s=10.0,
            sleep_fn=sleep_fn,
            monotonic_fn=lambda: clock[0],
            summary_fn=summary,
        )
        self.assertEqual(sleeps, [])

    def test_pending_then_green_polls_once(self) -> None:
        from create_gh_release import wait_for_ci_green

        sleeps: list[float] = []
        clock: list[float] = [0.0]
        calls: list[int] = [0]

        def summary(_sha: str) -> tuple[str, list[str], list[str], int]:
            calls[0] += 1
            if calls[0] == 1:
                return ("pending", [], ["dd-gitlab/build"], 2)
            return ("success", [], [], 3)

        def sleep_fn(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        wait_for_ci_green(
            "deadbeef",
            timeout_s=100.0,
            poll_interval_s=10.0,
            sleep_fn=sleep_fn,
            monotonic_fn=lambda: clock[0],
            summary_fn=summary,
        )
        self.assertEqual(sleeps, [10.0])
        self.assertEqual(calls[0], 2)

    def test_failure_raises_system_exit(self) -> None:
        from create_gh_release import wait_for_ci_green

        clock: list[float] = [0.0]

        def summary(_sha: str) -> tuple[str, list[str], list[str], int]:
            return ("failure", ["dd-gitlab/build"], [], 1)

        with self.assertRaises(SystemExit) as ctx:
            wait_for_ci_green(
                "deadbeef",
                timeout_s=100.0,
                poll_interval_s=10.0,
                sleep_fn=lambda _s: None,
                monotonic_fn=lambda: clock[0],
                summary_fn=summary,
            )
        self.assertIn("CI failed while waiting", str(ctx.exception))

    def test_timeout_raises_waiting_on(self) -> None:
        from create_gh_release import wait_for_ci_green

        clock: list[float] = [0.0]
        sleeps: list[float] = []

        def summary(_sha: str) -> tuple[str, list[str], list[str], int]:
            return ("pending", [], ["dd-gitlab/build"], 0)

        def sleep_fn(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        with self.assertRaises(WaitingOn) as ctx:
            wait_for_ci_green(
                "deadbeefcafe",
                timeout_s=25.0,
                poll_interval_s=10.0,
                sleep_fn=sleep_fn,
                monotonic_fn=lambda: clock[0],
                summary_fn=summary,
            )
        self.assertIn("CI still pending after 25", str(ctx.exception))
        self.assertIn("deadbeefcafe", str(ctx.exception))
        # 10 + 10 + 5 (remaining) then deadline hit on next loop
        self.assertEqual(sleeps, [10.0, 10.0, 5.0])

    def test_ensure_ci_green_wait_continues_after_pending(self) -> None:
        from create_gh_release import ensure_ci_green

        clock: list[float] = [0.0]
        calls: list[int] = [0]

        def summary(_sha: str) -> tuple[str, list[str], list[str], int]:
            calls[0] += 1
            if calls[0] <= 2:
                return ("pending", [], ["dd-gitlab/build"], 1)
            return ("success", [], [], 2)

        with mock.patch("create_gh_release.get_commit_ci_summary", side_effect=summary):
            ensure_ci_green(
                "abc",
                wait=True,
                timeout_s=100.0,
                poll_interval_s=1.0,
                sleep_fn=lambda s: clock.__setitem__(0, clock[0] + s),
                monotonic_fn=lambda: clock[0],
            )
        self.assertGreaterEqual(calls[0], 3)

    def test_resolve_wait_ci_dry_run_never_waits(self) -> None:
        from create_gh_release import resolve_wait_ci

        dry: Any = mock.Mock(dry_run=True, wait_ci=True)
        self.assertFalse(resolve_wait_ci(dry))
        wet_default: Any = mock.Mock(dry_run=False, wait_ci=None)
        self.assertTrue(resolve_wait_ci(wet_default))
        wet_off: Any = mock.Mock(dry_run=False, wait_ci=False)
        self.assertFalse(resolve_wait_ci(wet_off))

    def test_transient_api_error_retries_then_green(self) -> None:
        from create_gh_release import wait_for_ci_green

        sleeps: list[float] = []
        clock: list[float] = [0.0]
        calls: list[int] = [0]

        def summary(_sha: str) -> tuple[str, list[str], list[str], int]:
            calls[0] += 1
            if calls[0] == 1:
                raise subprocess.CalledProcessError(1, ["gh", "api", "status"])
            return ("success", [], [], 2)

        def sleep_fn(seconds: float) -> None:
            sleeps.append(seconds)
            clock[0] += seconds

        wait_for_ci_green(
            "deadbeef",
            timeout_s=100.0,
            poll_interval_s=10.0,
            sleep_fn=sleep_fn,
            monotonic_fn=lambda: clock[0],
            summary_fn=summary,
        )
        self.assertEqual(sleeps, [10.0])
        self.assertEqual(calls[0], 2)


class LoadLocalEnvTests(unittest.TestCase):
    def test_missing_file_is_noop(self) -> None:
        missing: Path = Path(tempfile.mkdtemp()) / "no-such.env"
        with mock.patch.dict("os.environ", {}, clear=True):
            load_local_env(missing)
            self.assertEqual(os.environ.get(SLACK_CHANNEL_PROD_ENV), None)

    def test_loads_key_val_and_skips_comments(self) -> None:
        tmp: Path = Path(tempfile.mkdtemp()) / ".env.local"
        tmp.write_text(
            dedent(
                f"""
                # comment
                {SLACK_CHANNEL_PROD_ENV}=https://example.test/from-file
                export {SLACK_DM_ENV}="https://example.test/dm"
                """
            ),
            encoding="utf-8",
        )
        with mock.patch.dict("os.environ", {}, clear=True):
            load_local_env(tmp)
            self.assertEqual(os.environ[SLACK_CHANNEL_PROD_ENV], "https://example.test/from-file")
            self.assertEqual(os.environ[SLACK_DM_ENV], "https://example.test/dm")

    def test_existing_env_wins(self) -> None:
        tmp: Path = Path(tempfile.mkdtemp()) / ".env.local"
        tmp.write_text(
            f"{SLACK_CHANNEL_PROD_ENV}=https://example.test/from-file\n",
            encoding="utf-8",
        )
        with mock.patch.dict(
            "os.environ",
            {SLACK_CHANNEL_PROD_ENV: "https://example.test/exported"},
            clear=True,
        ):
            load_local_env(tmp)
            self.assertEqual(os.environ[SLACK_CHANNEL_PROD_ENV], "https://example.test/exported")


if __name__ == "__main__":
    unittest.main()
