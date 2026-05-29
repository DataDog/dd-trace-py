from pathlib import Path
import re
import typing as t

from ddtrace import config as ddconfig
from ddtrace.internal import gitmetadata
from ddtrace.internal.constants import DEFAULT_SERVICE_NAME
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings._core import field
from ddtrace.internal.settings._registry import Config
from ddtrace.internal.utils.config import get_application_name
from ddtrace.version import __version__


DEFAULT_GLOBAL_RATE_LIMIT = 100.0


def _derive_tags(c: DDConfig) -> str:
    _tags = dict(
        debugger_version=__version__,
        env=ddconfig.env,
        host=get_hostname(),
        version=ddconfig.version,
    )
    _tags.update(ddconfig.tags)

    # Add git metadata tags, if available
    gitmetadata.add_tags(_tags)

    return ",".join([":".join((k, v)) for (k, v) in _tags.items() if v is not None])


def normalize_ident(ident: str) -> str:
    return ident.strip().lower().replace("_", "")


def validate_type_patterns(types: set[str]) -> None:
    for typ in types:
        for s in typ.strip().split("."):
            s = s.strip().replace("*", "a")
            if not (
                s.isidentifier()
                or s
                in {
                    "<locals>",
                }
            ):
                raise ValueError(f"Invalid redaction type pattern {typ}: {s} is not a valid identifier")


class DynamicInstrumentationConfig(DDConfig):
    __prefix__ = "dd.dynamic_instrumentation"

    # Plain declared variables: Python type + default come from the configuration
    # registry via field() (no hand-duplicated type/default, no descriptions at runtime).
    enabled = field()
    metrics = field("metrics.enabled")
    max_payload_size = field()
    upload_timeout = field("upload.timeout")
    upload_interval_seconds = field("upload.interval_seconds")
    diagnostics_interval = field("diagnostics.interval")

    # Custom-cast variables (non-canonical collection type / map / validator) stay
    # hand-declared; field() is only for the plain registry-typed scalars above.
    redacted_identifiers = DDConfig.v(
        set,
        "redacted_identifiers",
        map=normalize_ident,
        default=set(),
    )

    redacted_types = DDConfig.v(
        set,
        "redacted_types",
        map=str.strip,
        default=set(),
        validator=validate_type_patterns,
    )

    redacted_types_re = DDConfig.d(
        t.Optional[re.Pattern],
        lambda c: (
            re.compile(f"^(?:{'|'.join((_.replace('.', '[.]').replace('*', '.*') for _ in c.redacted_types))})$")
            if c.redacted_types
            else None
        ),
    )

    redaction_excluded_identifiers = DDConfig.v(
        set,
        "redaction_excluded_identifiers",
        map=normalize_ident,
        default=set(),
    )

    probe_file = DDConfig.v(
        t.Optional[Path],
        "probe_file",
        default=None,
    )

    # Derivations.
    service_name = DDConfig.d(str, lambda _: ddconfig.service or get_application_name() or DEFAULT_SERVICE_NAME)
    _intake_url = DDConfig.d(str, lambda _: agent_config.trace_agent_url)
    global_rate_limit = DDConfig.d(float, lambda _: DEFAULT_GLOBAL_RATE_LIMIT)
    _tags_in_qs = DDConfig.d(bool, lambda _: True)
    tags = DDConfig.d(str, _derive_tags)


Config.register(DynamicInstrumentationConfig, "dynamic_instrumentation")
