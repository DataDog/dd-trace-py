from typing import TYPE_CHECKING

from envier import En
from envier import validators

from ddtrace.vendor.sqlcommenter import generate_sql_comment as _generate_sql_comment

from . import _config as dd_config


if TYPE_CHECKING:
    from ddtrace import Span

DBM_PARENT_SERVICE_NAME_KEY = "ddps"
DBM_DATABASE_SERVICE_NAME_KEY = "dddbs"
DBM_ENVIRONMENT_KEY = "dde"
DBM_VERSION_KEY = "ddpv"
DBM_TRACE_PARENT_KEY = "traceparent"


class DatabaseMonitoringConfig(En):
    __prefix__ = "dd_trace"

    injection_mode = En.v(
        str,
        "sql_comment_injection_mode",
        default="disabled",
        help="Valid Injection Modes: disabled, service, and full",
        validator=validators.choice(["disabled", "full", "service"]),
    )


dbm_config = DatabaseMonitoringConfig()


def _get_dbm_comment(db_span):
    # type: (Span) -> str
    if dbm_config.injection_mode == "disabled":
        return ""

    dbm_tags = {
        DBM_PARENT_SERVICE_NAME_KEY: dd_config.service,
        DBM_ENVIRONMENT_KEY: dd_config.env,
        DBM_VERSION_KEY: dd_config.version,
        DBM_DATABASE_SERVICE_NAME_KEY: db_span.service,
    }

    if dbm_config.injection_mode == "full":
        # TODO: add _dd.dbm_trace_injected tag to db_span
        dbm_tags[DBM_TRACE_PARENT_KEY] = db_span.context._traceparent

    return _generate_sql_comment(**dbm_tags)
