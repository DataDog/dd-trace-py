import sys

import pytest


@pytest.mark.skipif(sys.platform != "linux", reason="reproduces a Linux CI platform mismatch")
@pytest.mark.subprocess(
    err=None,
    env={"DD_APPSEC_ENABLED": "false"},
    parametrize={"REPRO_CONTEXT_ORDER": ["platform-first", "config-first"]},
)
def test_writer_builder_call_count_depends_on_platform_mock_import_order():
    import contextlib
    import os
    import sys
    from unittest import mock

    from ddtrace.internal.writer import NativeWriter
    from tests.utils import override_global_config

    order = os.environ["REPRO_CONTEXT_ORDER"]
    platform_context = mock.patch.object(sys, "platform", "darwin")
    config_context = override_global_config({"_telemetry_enabled": False})
    cases = {
        "platform-first": ([platform_context, config_context], [0, 1, 2, 2]),
        "config-first": ([config_context, platform_context], [0, 0, 1, 1]),
    }
    contexts, expected_counts = cases[order]

    with mock.patch.object(sys, "platform", "linux"):
        with mock.patch("ddtrace.internal.native.TraceExporterBuilder") as builder_class:
            builder = mock.Mock()
            builder_class.return_value = builder
            builder.build.return_value = mock.Mock()
            for method_name in (
                "set_language",
                "set_language_version",
                "set_language_interpreter",
                "set_tracer_version",
                "set_git_commit_sha",
                "set_client_computed_top_level",
            ):
                getattr(builder, method_name).return_value = builder

            call_counts = []
            with contextlib.ExitStack() as stack:
                for context in contexts:
                    stack.enter_context(context)
                    call_counts.append(builder.set_restart_after_fork.call_count)
                NativeWriter("http://localhost:8126/v0.5/traces", sync_mode=True)
                call_counts.append(builder.set_restart_after_fork.call_count)
            call_counts.append(builder.set_restart_after_fork.call_count)

            assert call_counts == expected_counts
