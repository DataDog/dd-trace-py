import mock
import pytest
from pytest_mock import MockerFixture

from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._constants import WAF_ACTIONS
import ddtrace.appsec._contrib.dbapi.subscribers as dbapi_subscribers
from ddtrace.contrib._events.dbapi import DbApiEvent
from ddtrace.contrib._events.dbapi import DbApiSpanNamePrefix
from ddtrace.internal._exceptions import BlockingException


def _event(query: str) -> DbApiEvent:
    return DbApiEvent(query, DbApiSpanNamePrefix.SQLITE)


def test_dbapi_subscriber_raises_blocking_exception(mocker: MockerFixture) -> None:
    waf_result = mock.Mock(actions={WAF_ACTIONS.BLOCK_ACTION: {}})
    mocker.patch.object(dbapi_subscribers, "get_rasp_capability", return_value=True)
    call_waf = mocker.patch.object(dbapi_subscribers, "call_waf_callback", return_value=waf_result)
    blocking_config = {"status_code": 403}
    mocker.patch.object(dbapi_subscribers, "get_blocked", return_value=blocking_config)

    with pytest.raises(BlockingException) as exc_info:
        dbapi_subscribers.AppSecDbApiSubscriber.on_event(_event("SELECT * FROM users"))

    assert exc_info.value.args == (
        blocking_config,
        EXPLOIT_PREVENTION.BLOCKING,
        EXPLOIT_PREVENTION.TYPE.SQLI,
        "SELECT * FROM users",
    )
    call_waf.assert_called_once_with(
        {
            EXPLOIT_PREVENTION.ADDRESS.SQLI: "SELECT * FROM users",
            EXPLOIT_PREVENTION.ADDRESS.SQLI_TYPE: "sqlite",
        },
        crop_trace="on_event",
        rule_type=EXPLOIT_PREVENTION.TYPE.SQLI,
    )


@pytest.mark.parametrize(
    ("span_name_prefix", "dialect"),
    [
        (DbApiSpanNamePrefix.DB, "generic"),
        (DbApiSpanNamePrefix.MARIADB, "generic"),
        (DbApiSpanNamePrefix.MYSQL, "mysql"),
        (DbApiSpanNamePrefix.ORACLE, "oracle"),
        (DbApiSpanNamePrefix.POSTGRES, "postgresql"),
        (DbApiSpanNamePrefix.PYMYSQL, "mysql"),
        (DbApiSpanNamePrefix.PYODBC, "generic"),
        (DbApiSpanNamePrefix.SQL, "generic"),
        (DbApiSpanNamePrefix.SQLITE, "sqlite"),
        (DbApiSpanNamePrefix.VERTICA, "generic"),
    ],
)
def test_dbapi_subscriber_maps_span_name_prefix_to_dialect(
    mocker: MockerFixture, span_name_prefix: DbApiSpanNamePrefix, dialect: str
) -> None:
    mocker.patch.object(dbapi_subscribers, "get_rasp_capability", return_value=True)
    call_waf = mocker.patch.object(dbapi_subscribers, "call_waf_callback", return_value=None)

    dbapi_subscribers.AppSecDbApiSubscriber.on_event(DbApiEvent("SELECT 1", span_name_prefix))

    call_waf.assert_called_once_with(
        {
            EXPLOIT_PREVENTION.ADDRESS.SQLI: "SELECT 1",
            EXPLOIT_PREVENTION.ADDRESS.SQLI_TYPE: dialect,
        },
        crop_trace="on_event",
        rule_type=EXPLOIT_PREVENTION.TYPE.SQLI,
    )


def test_dbapi_subscriber_ignores_empty_query(mocker: MockerFixture) -> None:
    mocker.patch.object(dbapi_subscribers, "get_rasp_capability", return_value=True)
    call_waf = mocker.patch.object(dbapi_subscribers, "call_waf_callback")

    dbapi_subscribers.AppSecDbApiSubscriber.on_event(_event(""))

    call_waf.assert_not_called()


def test_dbapi_subscriber_ignores_query_without_sqli_capability(mocker: MockerFixture) -> None:
    mocker.patch.object(dbapi_subscribers, "get_rasp_capability", return_value=False)
    call_waf = mocker.patch.object(dbapi_subscribers, "call_waf_callback")

    dbapi_subscribers.AppSecDbApiSubscriber.on_event(_event("SELECT 1"))

    call_waf.assert_not_called()
