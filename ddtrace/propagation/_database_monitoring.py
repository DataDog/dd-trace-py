from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Dict
from typing import List
from typing import Literal
from typing import Optional
from typing import Tuple
from typing import Union

import ddtrace
from ddtrace import config as dd_config
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.settings.peer_service import PeerServiceConfig
from ddtrace.vendor.sqlcommenter import generate_sql_comment as _generate_sql_comment

from ..internal import compat
from ..internal.utils import get_argument_value
from ..internal.utils import set_argument_value
from ..settings._database_monitoring import dbm_config


if TYPE_CHECKING:
    from ddtrace.trace import Span


DBM_PARENT_SERVICE_NAME_KEY: Literal["ddps"] = "ddps"
DBM_DATABASE_SERVICE_NAME_KEY: Literal["dddbs"] = "dddbs"
DBM_PEER_HOSTNAME_KEY: Literal["ddh"] = "ddh"
DBM_PEER_DB_NAME_KEY: Literal["dddb"] = "dddb"
DBM_PEER_SERVICE_KEY: Literal["ddprs"] = "ddprs"
DBM_ENVIRONMENT_KEY: Literal["dde"] = "dde"
DBM_VERSION_KEY: Literal["ddpv"] = "ddpv"
DBM_TRACE_PARENT_KEY: Literal["traceparent"] = "traceparent"
DBM_TRACE_INJECTED_TAG: Literal["_dd.dbm_trace_injected"] = "_dd.dbm_trace_injected"

log = get_logger(__name__)


def default_sql_injector(dbm_comment: str, sql_statement: Union[str, bytes]) -> Union[str, bytes]:
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
    def __init__(
        self,
        sql_pos: int,
        sql_kw: str,
        comment_injector: Callable[[str, Union[str, bytes]], Union[str, bytes]] = default_sql_injector,
        comment_generator: Callable[..., str] = _generate_sql_comment,
        peer_hostname_tag: str = "out.host",
        peer_db_name_tag: str = "db.name",
        peer_service_tag: str = "peer.service",
    ) -> None:
        self.sql_pos = sql_pos
        self.sql_kw = sql_kw
        self.comment_injector = comment_injector
        self.comment_generator = comment_generator
        self.peer_hostname_tag = peer_hostname_tag
        self.peer_db_name_tag = peer_db_name_tag
        self.peer_service_tag = peer_service_tag

    def inject(self, dbspan: "Span", args: Any, kwargs: Any) -> Tuple[Any, Any]:
        # run sampling before injection to propagate correct sampling priority
        if hasattr(ddtrace, "tracer") and hasattr(ddtrace.tracer, "sample"):
            if dbspan.context.sampling_priority is None:
                ddtrace.tracer.sample(dbspan._local_root)
        else:
            log.error("ddtrace.tracer.sample is not available, unable to sample span.")

        dbm_comment = self._get_dbm_comment(dbspan)
        if dbm_comment is None:
            # injection_mode is disabled
            return args, kwargs

        original_sql_statement = get_argument_value(args, kwargs, self.sql_pos, self.sql_kw)
        if original_sql_statement is None:
            return args, kwargs

        # add dbm comment to original_sql_statement
        sql_with_dbm_tags = self.comment_injector(dbm_comment, original_sql_statement)
        # replace the original query or procedure with sql_with_dbm_tags
        args, kwargs = set_argument_value(args, kwargs, self.sql_pos, self.sql_kw, sql_with_dbm_tags)
        return args, kwargs

    def _get_dbm_comment(self, db_span: "Span") -> Optional[str]:
        """Generate DBM trace injection comment and updates span tags
        This method will set the ``_dd.dbm_trace_injected: "true"`` tag
        on ``db_span`` if the configured injection mode is ``"full"``.
        """
        if dbm_config.propagation_mode == "disabled":
            return None

        # set the following tags if DBM injection mode is full or service
        peer_service_enabled = PeerServiceConfig().set_defaults_enabled
        service_name_key = db_span.service
        if peer_service_enabled:
            db_name = db_span.get_tags().get("db.name")
            service_name_key = compat.ensure_text(db_name) if db_name else db_span.service

        dbm_tags = {
            DBM_PARENT_SERVICE_NAME_KEY: dd_config.service,
            DBM_ENVIRONMENT_KEY: dd_config.env,
            DBM_VERSION_KEY: dd_config.version,
            DBM_DATABASE_SERVICE_NAME_KEY: service_name_key,
        }

        peer_db_name = db_span.get_tag(self.peer_db_name_tag)
        if peer_db_name:
            dbm_tags[DBM_PEER_DB_NAME_KEY] = peer_db_name

        peer_hostname = db_span.get_tag(self.peer_hostname_tag)
        if peer_hostname:
            dbm_tags[DBM_PEER_HOSTNAME_KEY] = peer_hostname

        peer_service = db_span.get_tag(self.peer_service_tag)
        if peer_service:
            dbm_tags[DBM_PEER_SERVICE_KEY] = peer_service

        if dbm_config.propagation_mode == "full":
            db_span.set_tag_str(DBM_TRACE_INJECTED_TAG, "true")
            dbm_tags[DBM_TRACE_PARENT_KEY] = db_span.context._traceparent

        sql_comment = self.comment_generator(**dbm_tags)
        if sql_comment:
            # replace leading whitespace with trailing whitespace
            return sql_comment.strip() + " "
        return ""


def handle_dbm_injection(
    int_config: Any, span: "Span", args: List[Any], kwargs: Dict[str, Any]
) -> Tuple["Span", List[Any], Dict[str, Any]]:
    dbm_propagator = getattr(int_config, "_dbm_propagator", None)
    if dbm_propagator:
        args, kwargs = dbm_propagator.inject(span, args, kwargs)

    return span, args, kwargs


def handle_dbm_injection_asyncpg(
    int_config: Any, method: Callable, span: "Span", args: List[Any], kwargs: Dict[str, Any]
) -> Tuple["Span", List[Any], Dict[str, Any]]:
    # bind_execute_many uses prepared statements which we want to avoid injection for
    if method.__name__ != "bind_execute_many":
        return handle_dbm_injection(int_config, span, args, kwargs)
    return span, args, kwargs


_DBM_STANDARD_EVENTS = {
    "aiomysql.execute",
    "dbapi.execute",
    "django-database.execute",
    "mysql.execute",
    "mysqldb.execute",
    "psycopg.execute",
    "pymysql.execute",
    "pymongo.execute",
}


def listen() -> None:
    if dbm_config.propagation_mode in ["full", "service"]:
        for event in _DBM_STANDARD_EVENTS:
            core.on(event, handle_dbm_injection, "result")
        core.on("asyncpg.execute", handle_dbm_injection_asyncpg, "result")


def unlisten() -> None:
    for event in _DBM_STANDARD_EVENTS:
        core.reset_listeners(event, handle_dbm_injection)
    core.reset_listeners("asyncpg.execute", handle_dbm_injection_asyncpg)


listen()
