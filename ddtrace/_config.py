from ddtrace import config
from ddtrace.internal.remoteconfig import RemoteConfig
from ddtrace.internal.telemetry import telemetry_lifecycle_writer
from ddtrace.internal.utils.formats import asbool


def remoteconfig_callback(metadata, cfg):
    """Handle remote configuration received for the APM_TRACING RC product."""
    if not cfg:
        # TODO: reset back to original config
        return

    features = cfg["lib_config"]
    new_cfg = self._config.copy()

    # if features["tracing_debug"] is None:
    #     new_cfg["debug_enabled"] = debug_mode
    # else:
    #     new_cfg["debug_enabled"] = features["tracing_debug"]

    # if features["tracing_sample_rate"] is None:
    #     # TODO: handle user-defined values for the fallback (eg. tracer.configure())
    #     new_cfg["tracing_sample_rate"] = os.getenv("DD_TRACE_SAMPLE_RATE")
    # else:
    #     new_cfg["tracing_sample_rate"] = features["tracing_sample_rate"]

    if features["tracing_service_mapping"] is None:
        new_cfg.service_mapping.rc_value = None
    else:
        new_cfg.service_mapping.rc_value = {m["from_name"]: m["to_name"] for m in features["tracing_service_mapping"]}

    enable_runtime_metrics = features["runtime_metrics_enabled"]
    if enable_runtime_metrics is None:
        enable_runtime_metrics = asbool(os.getenv("DD_RUNTIME_METRICS_ENABLED", False))

    if features["tracing_http_header_tags"] is None:
        # TODO
        pass
    else:
        new_cfg["http_header_tags"] = features["tracing_http_header_tags"]

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker  # noqa

    if enable_runtime_metrics:
        RuntimeWorker.enable()
    else:
        RuntimeWorker.disable()

    self._config = new_cfg
    # telemetry_lifecycle_writer.add_event(
    #     {
    #         "configuration": [
    #             {"name": "service", "value": config.service},
    #             {"name": "env", "value": config.env},
    #         ],
    #         "remote_config": {},
    #     },
    #     "app-client-configuration-change",
    # )


# Subscribe to remote config updates.
RemoteConfig.register("APM_TRACING", remoteconfig_callback)
