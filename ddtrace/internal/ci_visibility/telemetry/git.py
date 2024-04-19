from typing import Optional

from ddtrace.internal.ci_visibility.telemetry.constants import CIVISIBILITY_TELEMETRY_NAMESPACE as _NAMESPACE
from ddtrace.internal.ci_visibility.telemetry.constants import ERROR_TYPES
from ddtrace.internal.ci_visibility.telemetry.constants import GIT_TELEMETRY
from ddtrace.internal.ci_visibility.telemetry.constants import GIT_TELEMETRY_COMMANDS
from ddtrace.internal.logger import get_logger
from ddtrace.internal.telemetry import telemetry_writer


log = get_logger(__name__)


def record_git_command(command: GIT_TELEMETRY_COMMANDS, duration: float, exit_code: Optional[int]) -> None:
    log.debug("Recording git command telemetry: %s, %s, %s", command, duration, exit_code)
    tags = (("command", command),)
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.COMMAND_COUNT, 1, tags)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.COMMAND_MS, duration, tags)
    if exit_code is not None and exit_code != 0:
        error_tags = (("command", command), ("exit_code", str(exit_code)))
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.COMMAND_ERRORS, 1, error_tags)


def record_search_commits(duration: float, error: Optional[ERROR_TYPES] = None) -> None:
    log.debug("Recording search commits telemetry: %s, %s", duration, error)
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SEARCH_COMMITS_COUNT, 1)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.SEARCH_COMMITS_MS, duration)
    if error is not None:
        error_tags = (("error_type", str(error)),)
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SEARCH_COMMITS_ERRORS, 1, error_tags)


def record_objects_pack_request(duration: float, error: Optional[ERROR_TYPES] = None) -> None:
    log.debug("Recording objects pack request telmetry: %s, %s", duration, error)
    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_COUNT, 1)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_MS, duration)
    if error is not None:
        error_tags = (("error", error),)
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_ERRORS, 1, error_tags)


def record_objects_pack_data(num_files: int, num_bytes: int) -> None:
    log.debug("Recording objects pack data telemetry: %s, %s", num_files, num_bytes)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_BYTES, num_bytes)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.OBJECTS_PACK_FILES, num_files)


def record_settings(
    duration: float,
    coverage_enabled: Optional[bool] = False,
    skipping_enabled: Optional[bool] = False,
    require_git: Optional[bool] = False,
    itr_enabled: Optional[bool] = False,
    error: Optional[ERROR_TYPES] = None,
) -> None:
    log.debug(
        "Recording settings telemetry: %s, %s, %s, %s, %s, %s",
        duration,
        coverage_enabled,
        skipping_enabled,
        require_git,
        itr_enabled,
        error,
    )
    # Telemetry "booleans" are true if they exist, otherwise false
    response_tags = []
    if coverage_enabled:
        response_tags.append(("coverage_enabled", "1"))
    if skipping_enabled:
        response_tags.append(("itrskip_enabled", "1"))
    if require_git:
        response_tags.append(("require_git", "1"))
    if itr_enabled:
        response_tags.append(("itrskip_enabled", "1"))

    telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SETTINGS_COUNT, 1)
    telemetry_writer.add_distribution_metric(_NAMESPACE, GIT_TELEMETRY.SETTINGS_MS, duration)

    telemetry_writer.add_count_metric(
        _NAMESPACE, GIT_TELEMETRY.SETTINGS_RESPONSE, 1, tuple(response_tags) if response_tags else None
    )
    if error is not None:
        error_tags = (("error", error),)
        telemetry_writer.add_count_metric(_NAMESPACE, GIT_TELEMETRY.SETTINGS_ERRORS, 1, error_tags)
