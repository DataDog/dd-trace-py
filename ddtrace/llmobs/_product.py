"""LLMObs product — Remote Config support for `llmobs_enabled` / `llmobs_ml_app`.

Wires into the APM_TRACING RC product via the "apm-tracing.rc" event hub so
that operators can enable/disable LLMObs and set the ml_app name at runtime
without restarting the service.
"""

import enum
import typing as t

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

requires = ["apm-tracing-rc"]


class APMCapabilities(enum.IntFlag):
    APM_TRACING_LLMOBS = 1 << 48


def enabled() -> bool:
    from ddtrace import config

    return config._llmobs_enabled


def start() -> None:
    from ddtrace.llmobs import LLMObs

    LLMObs.enable(_auto=True)


def restart(join: bool = False) -> None:
    pass


def stop(join: bool = False) -> None:
    pass


def post_preload() -> None:
    """Track LLM integrations detected in the environment."""
    from ddtrace import config
    from ddtrace.internal.module import is_module_installed
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.llmobs._constants import SUPPORTED_LLMOBS_INTEGRATIONS

    llm_oneclick_supported: bool = False
    if config._remote_config_enabled:
        for module_name in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            if is_module_installed(module_name):
                llm_oneclick_supported = True
                break

    telemetry_writer.add_configuration("llmobs_oneclick_supported", llm_oneclick_supported, origin="code")


def apm_tracing_rc(lib_config: dict, dd_config: t.Any) -> None:
    """Handle APM_TRACING RC payloads for LLMObs fields.

    Expected lib_config shape:
      llmobs.enabled      (bool) — enable or disable LLMObs at runtime.
      llmobs.ml_app_name  (str)  — ML application name; applied alongside enablement.

    Config values are written via the standard remote_config source so they
    participate in the normal config precedence chain.
    """
    from ddtrace.llmobs import LLMObs

    llmobs_config = lib_config.get("llmobs") or {}

    enabled_config = dd_config._config["_llmobs_enabled"]
    ml_app_config = dd_config._config["_llmobs_ml_app"]

    # Gate state on the concrete payload value, not value() — value()'s fallback
    # to default False silently disabled programmatically-enabled LLMObs.
    ml_app_name = llmobs_config.get("ml_app_name")
    if ml_app_config._rc_value != ml_app_name:
        ml_app_config.set_value(ml_app_name, "remote_config")

    enabled = llmobs_config.get("enabled")
    if enabled_config._rc_value != enabled:
        enabled_config.set_value(enabled, "remote_config")

    if enabled is True and not LLMObs.enabled:
        log.debug("Enabling LLMObs via Remote Config (ml_app=%r)", ml_app_config.value())
        LLMObs.enable(ml_app=ml_app_config.value(), _auto=True)
    elif enabled is False and LLMObs.enabled:
        log.debug("Disabling LLMObs via Remote Config: %r", llmobs_config)
        LLMObs.disable()
