from typing import Optional

from ddtrace.internal.ci_visibility.telemetry.constants import CIVISIBILITY_TELEMETRY_NAMESPACE as _NAMESPACE
from ddtrace.internal.ci_visibility.telemetry.constants import ERROR_TYPES
from ddtrace.internal.ci_visibility.telemetry.constants import GIT_TELEMETRY
from ddtrace.internal.ci_visibility.telemetry.constants import GIT_TELEMETRY_COMMANDS
from ddtrace.internal.telemetry import telemetry_writer


def record_git_command(command: GIT_TELEMETRY_COMMANDS, duration: float, exit_code: Optional[int]) -> None:
    tags = (("command", command.value),)
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.COMMAND_COUNT.value, 1, tags)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.COMMAND_MS.value, duration, tags)
    if exit_code is not None and exit_code != 0:
        error_tags = (("command", command.value), ("exit_code", str(exit_code)))
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.COMMAND_ERRORS.value, 1, error_tags)


def record_search_commits(duration: float, error: Optional[ERROR_TYPES] = None) -> None:
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SEARCH_COMMITS_COUNT.value, 1)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.SEARCH_COMMITS_MS.value, duration)
    if error is not None:
        error_tags = (("error_type", str(error)),)
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SEARCH_COMMITS_ERRORS.value, 1, error_tags)


def record_objects_pack(duration: float, num_bytes: int, files: int, error: Optional[ERROR_TYPES] = None) -> None:
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_COUNT.value, 1)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_MS.value, duration)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_BYTES.value, num_bytes)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_FILES.value, files)
    if error is not None:
        error_tags = (("error", error.value),)
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_ERRORS.value, 1, error_tags)


def record_settings(
    duration: float, coverage_enabled: bool, skipping_enabled: bool, error: Optional[ERROR_TYPES] = None
) -> None:
    print("SETTINGS - %s %s %s %s" % (duration, coverage_enabled, skipping_enabled, error))
    response_tags = (
        ("coverage_enabled", "1" if coverage_enabled else "0"),
        ("itrskip_enabled", "1" if skipping_enabled else "0"),
    )
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SETTINGS_COUNT.value, 1)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.SETTINGS_MS.value, duration)
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SETTINGS_RESPONSE.value, 1, response_tags)
    if error is not None:
        error_tags = (("error", error.value),)
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SETTINGS_ERRORS.value, 1, error_tags)
