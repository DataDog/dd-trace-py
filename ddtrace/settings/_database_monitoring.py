from typing import TYPE_CHECKING

from envier import En
from envier import validators

from ddtrace.vendor.sqlcommenter import generate_sql_comment as _generate_sql_comment

from . import _config as dd_config


if TYPE_CHECKING:
    from typing import Optional

    from ddtrace import Span

DBM_PARENT_SERVICE_NAME_KEY = "ddps"
DBM_DATABASE_SERVICE_NAME_KEY = "dddbs"
DBM_ENVIRONMENT_KEY = "dde"
DBM_VERSION_KEY = "ddpv"
DBM_TRACE_PARENT_KEY = "traceparent"
DBM_TRACE_INJECTED_TAG = "_dd.dbm_trace_injected"


class DatabaseMonitoringConfig(En):
    __prefix__ = "dd_dbm"

    propagation_mode = En.v(
        str,
        "propagation_mode",
        default="disabled",
        help="Valid Injection Modes: disabled, service, and full",
        validator=validators.choice(["disabled", "full", "service"]),
    )


dbm_config = DatabaseMonitoringConfig()


def _get_dbm_comment(db_span):
    # type: (Span) -> Optional[str]
    """Generate DBM trace injection comment and updates span tags
    This method will set the ``_dd.dbm_trace_injected: "true"`` tag
    on ``db_span`` if the configured injection mode is ``"full"``.
    """
    if dbm_config.propagation_mode == "disabled":
        return None

    # set the following tags if DBM injection mode is full or service
    dbm_tags = {
        DBM_PARENT_SERVICE_NAME_KEY: dd_config.service,
        DBM_ENVIRONMENT_KEY: dd_config.env,
        DBM_VERSION_KEY: dd_config.version,
        DBM_DATABASE_SERVICE_NAME_KEY: db_span.service,
    }

    if dbm_config.propagation_mode == "full":
        db_span.set_tag_str(DBM_TRACE_INJECTED_TAG, "true")
        dbm_tags[DBM_TRACE_PARENT_KEY] = db_span.context._traceparent

    return _generate_sql_comment(**dbm_tags)
