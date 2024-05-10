import os

from ..internal.logger import get_logger


log = get_logger(__name__)

ENV_VAR_MAPPINGS = {
    "OTEL_SERVICE_NAME": "DD_SERVICE",
    "OTEL_LOG_LEVEL": "DD_TRACE_DEBUG",
    "OTEL_PROPAGATORS": "DD_TRACE_PROPAGATION_STYLE",
    "OTEL_TRACES_SAMPLER": "DD_TRACE_SAMPLE_RATE",
    "OTEL_TRACES_EXPORTER": "DD_TRACE_ENABLED",
    "OTEL_METRICS_EXPORTER": "DD_RUNTIME_METRICS_ENABLED",
    "OTEL_LOGS_EXPORTER": "none",  # This should be ignored
    "OTEL_RESOURCE_ATTRIBUTES": "DD_TAGS",
    "OTEL_SDK_DISABLED": "DD_TRACE_ENABLED",
}

OTEL_UNIFIED_TAG_MAPPINGS = {
    "deployment.environment": "DD_ENV",
    "service.name": "DD_SERVICE",
    "service.version": "DD_VERSION",
}


def otel_remapping():
    """Checks for the existence of both OTEL and Datadog tracer environment variables and remaps them accordingly.
    Datadog Environment variables take precedence over OTEL, but if there isn't a Datadog value present,
    then OTEL values take their place.
    """

    """
    Step 1: Set OTEL Propagators
    Step 2: Set OTEL Trace Sampler
    Step 3: Set OTEL Service Name

    Create functions and do each environment

    ------
    Create function that calls another function (template: outerfunction:

    def set_otel_env(otel_env, dd_env, otel_config_remapper):
        otel_value = os.environ.get(otel_env, '').strip().lower()
        dd_value = os.environ.get(dd_env, '').strip().lower()
        if dd_value == '':
            dd_remap_value = otel_config_remapper(otel_value, dd_value)
            if dd_remap_value is not None:
                os.environ[dd_env] = dd_remap_value
    )

    Finish testing first
    """

    for otel_env, dd_env in ENV_VAR_MAPPINGS.items():
        otel_value = os.environ.get(otel_env, "").strip().lower()
        dd_value = os.environ.get(dd_env, "").strip().lower()

        if otel_env in os.environ:
            if dd_value == "":
                if otel_env == "OTEL_PROPAGATORS":
                    ## There are a series of unsupported OTEL propagator values;
                    ## it's best to loop through and filter for the ones we do support,
                    ## and if none appear then log a warning.
                    accepted_styles = set()
                    for style in otel_value.split(","):
                        style = style.strip().lower()
                        if style in ["b3", "b3multi", "b3single", "datadog", "tracecontext", "none"]:
                            if style == "b3single":
                                style = "b3"
                            accepted_styles.add(style)
                        else:
                            log.warning("Following style not supported by ddtrace: {}", style)
                    otel_value = ",".join(accepted_styles)

                if otel_env == "OTEL_TRACES_SAMPLER":
                    if otel_value in ["always_on", "always_off", "traceidratio"]:
                        if otel_value == "always_on":
                            otel_value = "1.0"
                        elif otel_value == "always_off":
                            otel_value = "0.0"
                        elif otel_value == "traceidratio":
                            otel_value = os.environ.get("OTEL_TRACES_SAMPLER_ARG")
                        os.environ["DD_TRACE_SAMPLE_IGNORE_PARENT"] = "True"

                    elif otel_value in ["parentbased_always_on", "parentbased_always_off", "parentbased_traceidratio"]:
                        if otel_value == "parentbased_always_on":
                            otel_value = "1.0"
                        elif otel_value == "parentbased_always_off":
                            otel_value = "0.0"
                        elif otel_value == "parentbased_traceidratio":
                            otel_value = os.environ.get("OTEL_TRACES_SAMPLER_ARG")
                        os.environ["DD_TRACE_SAMPLE_IGNORE_PARENT"] = "False"

                if otel_env == "OTEL_TRACES_EXPORTER":
                    if otel_value in ["true", "none", ""]:
                        otel_value = "False"
                    else:
                        log.warning("An unrecognized exporter %s is being used.", otel_value)

                if otel_env == "OTEL_METRICS_EXPORTER":
                    if otel_value in ["true", "none", ""]:
                        otel_value = "False"
                    else:
                        log.warning("An unrecognized exporter %s is being used.", otel_value)

                if otel_env == "OTEL_LOGS_EXPORTER":
                    log.warning("Unsupported exporter detected")
                    continue

                if otel_env == "OTEL_RESOURCE_ATTRIBUTES":
                    dd_tags = []
                    otel_tags = os.environ.get(otel_value)
                    otel_user_tag_dict = dict()
                    for ot_tag in otel_tags:
                        tag_pair = ot_tag.split("=")
                        otel_user_tag_dict[tag_pair[0]] = tag_pair[1]

                    for key, value in OTEL_UNIFIED_TAG_MAPPINGS:
                        otel_key = key
                        dd_key = value
                        if otel_key in otel_user_tag_dict.keys():
                            dd_tags.append("{}:{}".format(dd_key, otel_user_tag_dict[otel_key]))

                    for key, value in otel_user_tag_dict:
                        if dd_tags < 11 and key != "deployment.environment" or "service.name" or "service.version":
                            dd_tags.append("{}:{}".format(key, value))

                    if len(otel_user_tag_dict) > 10:
                        log.warning(
                            "To preserve metrics cardinality, only the following first 10 tags have been processed %s",
                            dd_tags,
                        )

                    otel_value = dd_tags
                os.environ[dd_env] = otel_value
