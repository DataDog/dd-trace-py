from .internal.utils.formats import parse_tags_str
from .internal.utils.http import normalize_header_name
from .sampler import DatadogSampler
from .settings.config import _ConfigItem
from .settings.config import _ConfigSourceEnv
from .settings.config import _ConfigSourceEnvMulti
from .settings.config import _ConfigSourceProgrammatic


def _service_from_tags(s):
    if s is None:
        return None
    tags = parse_tags_str(s)
    return tags.get("service")


def _parse_tags_str(s):
    if s is None:
        return None
    return parse_tags_str(s)


def _parse_trace_sampling_rules(s):
    # This is a method, so we need a `self` argument, but the self argument is unused.
    return DatadogSampler._parse_rules_from_env_variable(None, s)


def _parse_http_header_tags(s):
    return {normalize_header_name(k): v for k, v in parse_tags_str(s or "").items()}


def _default_config():
    return (
        _ConfigItem(
            key="service",
            type="Optional[str]",
            default=None,
            environ=_ConfigSourceEnvMulti(
                _ConfigSourceEnv(
                    name="DD_TAGS",
                    factory=_service_from_tags,
                    examples=["service:my-web-service", "service:my-web-service,env:prod"],
                ),
                _ConfigSourceEnv(name="DD_SERVICE", examples=["my-web-service"]),
            ),
            metadata={
                "description": "Service name to be used for the application. This is the primary key used in the Datadog product for data submitted from this library. See `Unified Service Tagging`_ for more information.",
                "version_added": {
                    "v0.36.0": "",
                },
            },
        ),
        _ConfigItem(
            key="trace_sample_rate",
            default=1.0,
            type="float",
            environ=_ConfigSourceEnv(
                name="DD_TRACE_SAMPLE_RATE",
                factory=float,
            ),
            metadata={
                "description": "Global sampling rate for traces. Setting this to 0.1 will sample 10% of traces.",
                "version_added": {
                    "v0.33.0": "``DD_TRACE_SAMPLE_RATE`` added",
                    "v1.16.0": "``config.trace_sample_rate`` added",
                },
            },
        ),
        _ConfigItem(
            key="trace_rate_limit",
            default=100,
            type="int",
            environ=_ConfigSourceEnv(
                name="DD_TRACE_RATE_LIMIT",
                factory=float,
            ),
            metadata={
                "description": "Maximum number of traces to sample per second. Setting this to 100 will sample a maximum of 100 traces per second.",
                "version_added": {
                    "v0.33.0": "``DD_TRACE_RATE_LIMIT`` added",
                    "v1.16.0": "``config.trace_rate_limit`` added",
                },
            },
        ),
        _ConfigItem(
            key="trace_sampling_rules",
            default=list,
            type="List[ddtrace.sampler.SamplingRule]",
            environ=_ConfigSourceEnv(
                name="DD_TRACE_SAMPLING_RULES",
                factory=_parse_trace_sampling_rules,
                examples=[
                    '[{"sample_rate":0.5,"service":"my-service"}]',
                    '[{"sample_rate":0.9,"service":"my-flask-app","name":"flask.request"}]',
                ],
            ),
            metadata={
                "description": "Rules for sampling traces based on service and span name.",
                "version_added": {
                    "v0.55.0": "``DD_TRACE_SAMPLING_RULES`` added",
                    "v1.16.0": "``config.trace_sampling_rules`` added",
                },
            },
        ),
        _ConfigItem(
            key="trace_http_header_tags",
            default=dict,
            type="Dict[str, str]",
            programmatic=_ConfigSourceProgrammatic(
                factory=lambda h: {normalize_header_name(k): v for k, v in h.items()}
            ),
            environ=_ConfigSourceEnv(
                name="DD_TRACE_HEADER_TAGS",
                factory=_parse_http_header_tags,
                examples=[
                    "X-HEADER:tag1,X-Other-Header:tag2",
                    "X-HEADER,X-Other-Header",
                ],
            ),
            metadata={
                "description": "Headers to tag on traces. Tags will be applied to the TODO span. When a tag value is not provided for a header then a default prefix will be included",
                "version_added": {
                    "TODO": "",
                },
            },
        ),
    )
