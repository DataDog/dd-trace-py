from typing import TYPE_CHECKING
from typing import Union  # noqa

from ddtrace.internal.logger import get_logger
from ddtrace.vendor.sqlcommenter import generate_sql_comment as _generate_sql_comment

from ..internal.utils import get_argument_value
from ..internal.utils import set_argument_value
from ..settings import _config as dd_config
from ..settings._database_monitoring import dbm_config


if TYPE_CHECKING:
    from typing import Optional

    from ddtrace import Span

DBM_PARENT_SERVICE_NAME_KEY = "ddps"
DBM_DATABASE_SERVICE_NAME_KEY = "dddbs"
DBM_ENVIRONMENT_KEY = "dde"
DBM_VERSION_KEY = "ddpv"
DBM_TRACE_PARENT_KEY = "traceparent"
DBM_TRACE_INJECTED_TAG = "_dd.dbm_trace_injected"


log = get_logger(__name__)


def default_sql_injector(dbm_comment, sql_statement):
    # type: (str, Union[str, bytes]) -> Union[str, bytes]
    try:
        if isinstance(sql_statement, bytes):
            return dbm_comment.encode("utf-8", errors="strict") + sql_statement
        return dbm_comment + sql_statement
    except (TypeError, ValueError):
        log.warning(
            "Linking Database Monitoring profiles to spans is not supported for the following query type: %s. "
            "To disable this feature please set the following environment variable: "
            "DD_DBM_PROPAGATION_MODE=disabled",
            type(sql_statement),
        )
    return sql_statement


class _DBM_Propagator(object):
    def __init__(self, sql_pos, sql_kw, sql_injector=default_sql_injector):
        self.sql_pos = sql_pos
        self.sql_kw = sql_kw
        self.sql_injector = sql_injector

    def inject(self, dbspan, args, kwargs):
        dbm_comment = self._get_dbm_comment(dbspan)
        if dbm_comment is None:
            # injection_mode is disabled
            return args, kwargs

        original_sql_statement = get_argument_value(args, kwargs, self.sql_pos, self.sql_kw)
        # add dbm comment to original_sql_statement
        sql_with_dbm_tags = self.sql_injector(dbm_comment, original_sql_statement)
        # replace the original query or procedure with sql_with_dbm_tags
        args, kwargs = set_argument_value(args, kwargs, self.sql_pos, self.sql_kw, sql_with_dbm_tags)
        return args, kwargs

    def _get_dbm_comment(self, db_span):
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

        sql_comment = _generate_sql_comment(**dbm_tags)
        if sql_comment:
            # replace leading whitespace with trailing whitespace
            return sql_comment.strip() + " "
        return ""
