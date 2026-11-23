from __future__ import annotations

from typing import TYPE_CHECKING
from typing import Any
from typing import Literal
from typing import Optional
from typing import Union

import ddtrace
from ddtrace import config as dd_config
from ddtrace.internal import core
from ddtrace.internal import process_tags
from ddtrace.internal.constants import PROPAGATED_HASH
from ddtrace.internal.database import DatabaseIntegrationConfigEvent
from ddtrace.internal.database import DatabaseQueryAdapter
from ddtrace.internal.database import DatabaseQueryPrepareEvent
from ddtrace.internal.database import prepend_query_comment
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.internal.settings.peer_service import _ps_config
from ddtrace.internal.span_bus import find_span_on_context
from ddtrace.vendor.sqlcommenter import _generate_comment_from_metadata
from ddtrace.vendor.sqlcommenter import generate_sql_comment as _generate_sql_comment

from ..internal import compat
from ..internal.settings._database_monitoring import dbm_config
from ..internal.utils import get_argument_value
from ..internal.utils import set_argument_value


if TYPE_CHECKING:
    from ddtrace.trace import Span


DBM_PARENT_SERVICE_NAME_KEY: Literal["ddps"] = "ddps"
DBM_DATABASE_SERVICE_NAME_KEY: Literal["dddbs"] = "dddbs"
DBM_PEER_HOSTNAME_KEY: Literal["ddh"] = "ddh"
DBM_PEER_DB_NAME_KEY: Literal["dddb"] = "dddb"
DBM_PEER_SERVICE_KEY: Literal["ddprs"] = "ddprs"
DBM_ENVIRONMENT_KEY: Literal["dde"] = "dde"
DBM_VERSION_KEY: Literal["ddpv"] = "ddpv"
DBM_SERVICE_HASH: Literal["ddsh"] = "ddsh"
DBM_TRACE_PARENT_KEY: Literal["traceparent"] = "traceparent"
DBM_TRACE_INJECTED_TAG: Literal["_dd.dbm_trace_injected"] = "_dd.dbm_trace_injected"
DBM_PROPAGATION_MODE_DYNAMIC_SERVICE: Literal["dynamic_service"] = "dynamic_service"
_DBM_INJECTION_MODES = ("full", "service", DBM_PROPAGATION_MODE_DYNAMIC_SERVICE)

log = get_logger(__name__)


def _should_inject_sql_basehash() -> bool:
    return dbm_config.propagation_mode == DBM_PROPAGATION_MODE_DYNAMIC_SERVICE or (
        dbm_config.propagation_mode == "service" and dbm_config.inject_sql_basehash
    )


def default_sql_injector(dbm_comment: str, sql_statement: Union[str, bytes]) -> Union[str, bytes]:
    try:
        return prepend_query_comment(dbm_comment, sql_statement)
    except (TypeError, ValueError):
        log.warning(
            "Linking Database Monitoring profiles to spans is not supported for the following query type: %s. "
            "To disable this feature please set the following environment variable: "
            "DD_DBM_PROPAGATION_MODE=disabled",
            type(sql_statement),
        )
    return sql_statement


class _DBM_Propagator:
    def __init__(
        self,
        sql_pos,
        sql_kw,
        comment_injector=default_sql_injector,
        comment_generator=_generate_sql_comment,
        peer_hostname_tag="out.host",
        peer_db_name_tag="db.name",
        peer_service_tag="peer.service",
        *,
        _adapter: Optional[DatabaseQueryAdapter] = None,
        _activate_listener: bool = True,
    ):
        self.sql_pos = sql_pos
        self.sql_kw = sql_kw
        self.comment_injector = comment_injector
        self.comment_generator = comment_generator
        self.peer_hostname_tag = peer_hostname_tag
        self.peer_db_name_tag = peer_db_name_tag
        self.peer_service_tag = peer_service_tag
        self._adapter = _adapter
        # Direct construction is the legacy activation path; integration hooks own their lifecycle.
        if _activate_listener:
            listen()

    def inject(self, dbspan, args, kwargs):
        # run sampling before injection to propagate correct sampling priority
        if dbspan.context.sampling_priority is None:
            ddtrace.tracer.sample(dbspan._local_root)

        dbm_comment = self._get_dbm_comment(dbspan)
        if dbm_comment is None:
            # injection_mode is disabled
            return args, kwargs

        # the base hash is injected in the comment and on the span tags for correlation purpose
        if _should_inject_sql_basehash() and (base_hash := process_tags.base_hash):
            dbspan._set_attribute(PROPAGATED_HASH, str(base_hash))

        if self._adapter is not None:
            try:
                return self._adapter.apply(dbm_comment, args, kwargs)
            except (TypeError, ValueError):
                log.warning("Unable to apply Database Monitoring query comment", exc_info=True)
                return args, kwargs

        original_sql_statement = get_argument_value(args, kwargs, self.sql_pos, self.sql_kw)
        # add dbm comment to original_sql_statement
        sql_with_dbm_tags = self.comment_injector(dbm_comment, original_sql_statement)
        # replace the original query or procedure with sql_with_dbm_tags
        args, kwargs = set_argument_value(args, kwargs, self.sql_pos, self.sql_kw, sql_with_dbm_tags)
        return args, kwargs

    def _get_dbm_comment(self, db_span: Span) -> Optional[str]:
        """Generate DBM trace injection comment and updates span tags
        This method will set the ``_dd.dbm_trace_injected: "true"`` tag
        on ``db_span`` if the configured injection mode is ``"full"``.
        """
        if dbm_config.propagation_mode == "disabled":
            return None

        # set the following tags if DBM injection mode is full, service, or dynamic_service
        # DEV: This runs on every DB span, so use the cached module-level singleton, which resolves
        # the config once and caches it. A fresh PeerServiceConfig() has _set_defaults_enabled=None
        # and re-reads the config via get_config() on every span; get_config() reports configuration
        # telemetry on each read, so per-span instances leak telemetry config entries unboundedly
        # on high-query services (issue #18800). Keep this on the shared singleton.
        peer_service_enabled = _ps_config.set_defaults_enabled
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
            db_span._set_attribute(DBM_TRACE_INJECTED_TAG, "true")
            dbm_tags[DBM_TRACE_PARENT_KEY] = db_span.context._traceparent

        if _should_inject_sql_basehash() and (base_hash := process_tags.base_hash):
            dbm_tags[DBM_SERVICE_HASH] = str(base_hash)

        sql_comment = self.comment_generator(**dbm_tags)
        if sql_comment:
            # replace leading whitespace with trailing whitespace
            return sql_comment.strip() + " "
        return ""


def handle_dbm_injection(int_config, span, args, kwargs):
    if "_dbm_propagator" not in int_config and (adapter := int_config.get("_query_adapter")) is not None:
        _configure_integration(DatabaseIntegrationConfigEvent(int_config, adapter))
    dbm_propagator = getattr(int_config, "_dbm_propagator", None)
    if dbm_propagator:
        args, kwargs = dbm_propagator.inject(span, args, kwargs)

    return span, args, kwargs


def handle_dbm_injection_asyncpg(int_config, method, span, args, kwargs):
    # bind_execute_many uses prepared statements which we want to avoid injection for
    if method.__name__ != "bind_execute_many":
        return handle_dbm_injection(int_config, span, args, kwargs)
    return span, args, kwargs


def handle_query_prepare_event(event: DatabaseQueryPrepareEvent) -> None:
    if not event.supports_query_rewriting or not _is_active(event.context.event.integration_config):
        return
    span = find_span_on_context(event.context)
    if span is None:
        return
    _, args, kwargs = handle_dbm_injection(event.context.event.integration_config, span, event.args, event.kwargs)
    event.args, event.kwargs = args, kwargs


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


_active_integrations: set[str] = set()
_legacy_listening = False


def _is_active(integration_config: IntegrationConfig) -> bool:
    name = integration_config.integration_name
    return (
        _legacy_listening
        or name in _active_integrations
        or (name == "django-database" and "django" in _active_integrations)
    )


def _configure_integration(event: DatabaseIntegrationConfigEvent) -> None:
    cfg = event.integration_config
    if not _is_active(cfg) or "_dbm_propagator" in cfg:
        return
    adapter = event.adapter
    generator = _generate_comment_from_metadata if adapter.comment_format == "metadata" else _generate_sql_comment
    cfg["_dbm_propagator"] = _DBM_Propagator(
        adapter.position,
        adapter.keyword,
        adapter.inject,
        generator,
        _adapter=adapter,
        _activate_listener=False,
    )


def _handle_integration_injection(
    int_config: IntegrationConfig, span: Span, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Span, tuple[Any, ...], dict[str, Any]]:
    if _is_active(int_config):
        return handle_dbm_injection(int_config, span, args, kwargs)
    return span, args, kwargs


def _handle_asyncpg_injection(
    int_config: IntegrationConfig, method: Any, span: Span, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> tuple[Span, tuple[Any, ...], dict[str, Any]]:
    if _is_active(int_config):
        return handle_dbm_injection_asyncpg(int_config, method, span, args, kwargs)
    return span, args, kwargs


def _register_listeners() -> None:
    core.on(DatabaseIntegrationConfigEvent.event_name, _configure_integration)
    if dbm_config.propagation_mode in _DBM_INJECTION_MODES:
        core.on(DatabaseQueryPrepareEvent.event_name, handle_query_prepare_event)
        for event in _DBM_STANDARD_EVENTS:
            core.on(event, _handle_integration_injection, "result")
        core.on("asyncpg.execute", _handle_asyncpg_injection, "result")


def _unregister_listeners() -> None:
    core.reset_listeners(DatabaseIntegrationConfigEvent.event_name, _configure_integration)
    core.reset_listeners(DatabaseQueryPrepareEvent.event_name, handle_query_prepare_event)
    for event in _DBM_STANDARD_EVENTS:
        core.reset_listeners(event, _handle_integration_injection)
    core.reset_listeners("asyncpg.execute", _handle_asyncpg_injection)


def listen() -> None:
    global _legacy_listening
    _legacy_listening = dbm_config.propagation_mode in _DBM_INJECTION_MODES
    _register_listeners()


def unlisten() -> None:
    global _legacy_listening
    _legacy_listening = False
    # Preserve explicit legacy teardown, even when the caller restored the propagation mode first.
    _unregister_listeners()


class _IntegrationHooks:
    def __init__(self, integration: str) -> None:
        self.integration = integration

    def listen(self) -> None:
        _active_integrations.add(self.integration)
        _register_listeners()

    def unlisten(self) -> None:
        _active_integrations.discard(self.integration)
        if not _active_integrations and not _legacy_listening:
            _unregister_listeners()


aiomysql_hooks = _IntegrationHooks("aiomysql")
asyncpg_hooks = _IntegrationHooks("asyncpg")
django_hooks = _IntegrationHooks("django")
mysql_hooks = _IntegrationHooks("mysql")
mysqldb_hooks = _IntegrationHooks("mysqldb")
psycopg_hooks = _IntegrationHooks("psycopg")
pymongo_hooks = _IntegrationHooks("pymongo")
pymysql_hooks = _IntegrationHooks("pymysql")
