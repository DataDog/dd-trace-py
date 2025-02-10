# -*- coding: utf-8 -*-
from unittest import mock

import pytest
import valkey

import ddtrace
from ddtrace.contrib.internal.valkey.patch import patch
from ddtrace.contrib.internal.valkey.patch import unpatch
from ddtrace.internal.schema import DEFAULT_SPAN_SERVICE_NAME
from ddtrace.trace import Pin
from tests.opentracer.utils import init_tracer
from tests.utils import DummyTracer
from tests.utils import TracerTestCase
from tests.utils import snapshot

from ..config import VALKEY_CONFIG


class TestValkeyPatch(TracerTestCase):
    TEST_PORT = VALKEY_CONFIG["port"]

    def setUp(self):
        super(TestValkeyPatch, self).setUp()
        patch()
        r = valkey.Valkey(port=self.TEST_PORT)
        r.flushall()
        Pin._override(r, tracer=self.tracer)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestValkeyPatch, self).tearDown()

    def command_test_rowcount(self, raw_command, row_count, expect_result=True, **kwargs):
        command_args_as_list = raw_command.split(" ")

        command_name = command_args_as_list[0].lower()

        if hasattr(self.r, command_name):
            func = getattr(self.r, command_name)

            try:
                # try to run function with kwargs, may fail due to valkey version
                result = yield func(*command_args_as_list[1:], **kwargs)
                for k in kwargs.keys():
                    raw_command += " " + str(kwargs[k])
            except Exception:
                # try without keyword arguments
                result = func(*command_args_as_list[1:])

            if expect_result:
                assert result is not None
            else:
                empty_result = [None, [], {}, b""]
                if isinstance(result, list):
                    result = [x for x in result if x]
                assert result in empty_result

            command_span = self.get_spans()[-1]

            assert command_span.name == "valkey.command"
            assert command_span.get_tag("valkey.raw_command") == raw_command
            assert command_span.get_metric("db.row_count") == row_count

    def test_long_command(self):
        self.r.mget(*range(1000))

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]

        self.assert_is_measured(span)
        assert span.service == "valkey"
        assert span.name == "valkey.command"
        assert span.span_type == "valkey"
        assert span.error == 0
        meta = {
            "out.host": "localhost",
        }
        metrics = {
            "network.destination.port": self.TEST_PORT,
            "out.valkey_db": 0,
        }
        for k, v in meta.items():
            assert span.get_tag(k) == v
        for k, v in metrics.items():
            assert span.get_metric(k) == v

        assert span.get_tag("valkey.raw_command").startswith("MGET 0 1 2 3")
        assert span.get_tag("valkey.raw_command").endswith("...")
        assert span.get_tag("component") == "valkey"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "valkey"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_service_name_v1(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        span = spans[0]
        assert span.service == DEFAULT_SPAN_SERVICE_NAME

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_operation_name_v0_schema(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        span = spans[0]
        assert span.name == "valkey.command"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_operation_name_v1_schema(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        span = spans[0]
        assert span.name == "valkey.command"

    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        self.assert_is_measured(span)
        assert span.service == "valkey"
        assert span.name == "valkey.command"
        assert span.span_type == "valkey"
        assert span.error == 0
        assert span.get_metric("out.valkey_db") == 0
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("valkey.raw_command") == "GET cheese"
        assert span.get_tag("component") == "valkey"
        assert span.get_tag("span.kind") == "client"
        assert span.get_tag("db.system") == "valkey"
        assert span.get_metric("valkey.args_length") == 2
        assert span.resource == "GET"

    def test_connection_error(self):
        with mock.patch.object(
            valkey.connection.ConnectionPool,
            "get_connection",
            side_effect=valkey.exceptions.ConnectionError("whatever"),
        ):
            with pytest.raises(valkey.exceptions.ConnectionError):
                self.r.get("foo")

    def test_pipeline_traced(self):
        with self.r.pipeline(transaction=False) as p:
            p.set("blah", 32)
            p.rpush("foo", "éé")
            p.hgetall("xxx")
            p.execute()

        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        self.assert_is_measured(span)
        assert span.service == "valkey"
        assert span.name == "valkey.command"
        assert span.resource == "SET\nRPUSH\nHGETALL"
        assert span.span_type == "valkey"
        assert span.error == 0
        assert span.get_metric("out.valkey_db") == 0
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("valkey.raw_command") == "SET blah 32\nRPUSH foo éé\nHGETALL xxx"
        assert span.get_tag("component") == "valkey"
        assert span.get_tag("span.kind") == "client"
        assert span.get_metric("valkey.pipeline_length") == 3
        assert span.get_metric("valkey.pipeline_length") == 3

    def test_pipeline_immediate(self):
        with self.r.pipeline() as p:
            p.set("a", 1)
            p.immediate_execute_command("SET", "a", 1)
            p.execute()

        spans = self.get_spans()
        assert len(spans) == 2
        span = spans[0]
        self.assert_is_measured(span)
        assert span.service == "valkey"
        assert span.name == "valkey.command"
        assert span.resource == "SET"
        assert span.span_type == "valkey"
        assert span.error == 0
        assert span.get_metric("out.valkey_db") == 0
        assert span.get_tag("out.host") == "localhost"
        assert span.get_tag("component") == "valkey"
        assert span.get_tag("span.kind") == "client"

    def test_meta_override(self):
        r = self.r
        pin = Pin.get_from(r)
        if pin:
            pin._clone(tags={"cheese": "camembert"}).onto(r)

        r.get("cheese")
        spans = self.get_spans()
        assert len(spans) == 1
        span = spans[0]
        assert span.service == "valkey"
        assert "cheese" in span.get_tags() and span.get_tag("cheese") == "camembert"

    def test_patch_unpatch(self):
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        r = valkey.Valkey(port=VALKEY_CONFIG["port"])
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = valkey.Valkey(port=VALKEY_CONFIG["port"])
        r.get("key")

        spans = tracer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = valkey.Valkey(port=VALKEY_CONFIG["port"])
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

    def test_opentracing(self):
        """Ensure OpenTracing works with valkey."""
        ot_tracer = init_tracer("valkey_svc", self.tracer)

        with ot_tracer.start_active_span("valkey_get"):
            us = self.r.get("cheese")
            assert us is None

        spans = self.get_spans()
        assert len(spans) == 2
        ot_span, dd_span = spans

        # confirm the parenting
        assert ot_span.parent_id is None
        assert dd_span.parent_id == ot_span.span_id

        assert ot_span.name == "valkey_get"
        assert ot_span.service == "valkey_svc"

        self.assert_is_measured(dd_span)
        assert dd_span.service == "valkey"
        assert dd_span.name == "valkey.command"
        assert dd_span.span_type == "valkey"
        assert dd_span.error == 0
        assert dd_span.get_metric("out.valkey_db") == 0
        assert dd_span.get_tag("out.host") == "localhost"
        assert dd_span.get_tag("valkey.raw_command") == "GET cheese"
        assert dd_span.get_tag("component") == "valkey"
        assert dd_span.get_tag("span.kind") == "client"
        assert dd_span.get_tag("db.system") == "valkey"
        assert dd_span.get_metric("valkey.args_length") == 2
        assert dd_span.resource == "GET"

    def test_valkey_rowcount_all_keys_valid(self):
        self.r.set("key1", "value1")

        get1 = self.r.get("key1")

        assert get1 == b"value1"

        spans = self.get_spans()
        get_valid_key_span = spans[1]

        assert get_valid_key_span.name == "valkey.command"
        assert get_valid_key_span.get_tag("valkey.raw_command") == "GET key1"
        assert get_valid_key_span.get_metric("db.row_count") == 1

        get_commands = ["GET key", "GETEX key", "GETRANGE key 0 2"]
        list_get_commands = ["LINDEX lkey 0", "LRANGE lkey 0 3", "RPOP lkey", "LPOP lkey"]
        hashing_get_commands = [
            "HGET hkey field1",
            "HGETALL hkey",
            "HKEYS hkey",
            "HMGET hkey field1 field2",
            "HRANDFIELD hkey",
            "HVALS hkey",
        ]
        multi_key_get_commands = ["MGET key key2", "MGET key key2 key3", "MGET key key2 key3 key4"]

        for command in get_commands:
            self.r.set("key", "value")
            self.command_test_rowcount(command, 1)
        for command in list_get_commands:
            self.r.lpush("lkey", "1", "2", "3", "4", "5")
            self.command_test_rowcount(command, 1)
            if command == "RPOP lkey":  # lets get multiple values from the set and ensure rowcount is still 1
                self.command_test_rowcount(command, 1, count=2)
        for command in hashing_get_commands:
            self.r.hset("hkey", "field1", "value1")
            self.r.hset("hkey", "field2", "value2")
            self.command_test_rowcount(command, 1)
        for command in multi_key_get_commands:
            self.r.mset({"key": "value", "key2": "value2", "key3": "value3", "key4": "value4"})
            self.command_test_rowcount(command, len(command.split(" ")) - 1)

    def test_valkey_rowcount_some_keys_valid(self):
        self.r.mset({"key": "value", "key2": "value2"})

        get_both_valid = self.r.mget("key", "key2")
        get_one_missing = self.r.mget("key", "missing_key")

        assert get_both_valid == [b"value", b"value2"]
        assert get_one_missing == [b"value", None]

        spans = self.get_spans()
        get_both_valid_span = spans[1]
        get_one_missing_span = spans[2]

        assert get_both_valid_span.name == "valkey.command"
        assert get_both_valid_span.get_tag("valkey.raw_command") == "MGET key key2"
        assert get_both_valid_span.get_metric("db.row_count") == 2

        assert get_one_missing_span.name == "valkey.command"
        assert get_one_missing_span.get_tag("valkey.raw_command") == "MGET key missing_key"
        assert get_one_missing_span.get_metric("db.row_count") == 1

        multi_key_get_commands = [
            "MGET key key2",
            "MGET key missing_key",
            "MGET key key2 missing_key",
            "MGET key missing_key missing_key2 key2",
        ]

        for command in multi_key_get_commands:
            command_keys = command.split(" ")[1:]
            self.command_test_rowcount(command, len([key for key in command_keys if "missing_key" not in key]))

    def test_valkey_rowcount_no_keys_valid(self):
        get_missing = self.r.get("missing_key")

        assert get_missing is None

        spans = self.get_spans()
        get_missing_key_span = spans[0]

        assert get_missing_key_span.name == "valkey.command"
        assert get_missing_key_span.get_tag("valkey.raw_command") == "GET missing_key"
        assert get_missing_key_span.get_metric("db.row_count") == 0

        get_commands = ["GET key", "GETDEL key", "GETEX key", "GETRANGE key 0 2"]
        list_get_commands = ["LINDEX lkey 0", "LRANGE lkey 0 3", "RPOP lkey", "LPOP lkey"]
        hashing_get_commands = [
            "HGET hkey field1",
            "HGETALL hkey",
            "HKEYS hkey",
            "HMGET hkey field1 field2",
            "HRANDFIELD hkey",
            "HVALS hkey",
        ]
        multi_key_get_commands = ["MGET key key2", "MGET key key2 key3", "MGET key key2 key3 key4"]

        for command in get_commands:
            self.command_test_rowcount(command, 0, expect_result=False)
        for command in list_get_commands:
            self.command_test_rowcount(command, 0, expect_result=False)
            if command == "RPOP lkey":  # lets get multiple values from the set and ensure rowcount is still 1
                self.command_test_rowcount(command, 0, expect_result=False, count=2)
        for command in hashing_get_commands:
            self.command_test_rowcount(command, 0, expect_result=False)
        for command in multi_key_get_commands:
            self.command_test_rowcount(command, 0, expect_result=False)

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    def test_user_specified_service_default(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "valkey"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"))
    def test_user_specified_service_v0(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "valkey"

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"))
    def test_user_specified_service_v1(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "mysvc"

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(DD_VALKEY_SERVICE="myvalkey", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0")
    )
    def test_env_user_specified_valkey_service_v0(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "myvalkey", span.service

        self.reset()

        # Global config
        with self.override_config("valkey", dict(service="cfg-valkey")):
            self.r.get("cheese")
            span = self.get_spans()[0]
            assert span.service == "cfg-valkey", span.service

        self.reset()

        # Manual override
        Pin._override(self.r, service="mysvc", tracer=self.tracer)
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "mysvc", span.service

    @TracerTestCase.run_in_subprocess(
        env_overrides=dict(
            DD_SERVICE="app-svc", DD_VALKEY_SERVICE="env-specified-valkey-svc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"
        )
    )
    def test_service_precedence_v0(self):
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "env-specified-valkey-svc", span.service

        self.reset()

        # Do a manual override
        Pin._override(self.r, service="override-valkey", tracer=self.tracer)
        self.r.get("cheese")
        span = self.get_spans()[0]
        assert span.service == "override-valkey", span.service


class TestValkeyPatchSnapshot(TracerTestCase):
    TEST_PORT = VALKEY_CONFIG["port"]

    def setUp(self):
        super(TestValkeyPatchSnapshot, self).setUp()
        patch()
        r = valkey.Valkey(port=self.TEST_PORT)
        self.r = r

    def tearDown(self):
        unpatch()
        super(TestValkeyPatchSnapshot, self).tearDown()
        self.r.flushall()

    @snapshot()
    def test_long_command(self):
        self.r.mget(*range(1000))

    @snapshot()
    def test_basics(self):
        us = self.r.get("cheese")
        assert us is None

    @snapshot()
    def test_unicode(self):
        us = self.r.get("😐")
        assert us is None

    @snapshot()
    def test_pipeline_traced(self):
        with self.r.pipeline(transaction=False) as p:
            p.set("blah", 32)
            p.rpush("foo", "éé")
            p.hgetall("xxx")
            p.execute()

    @snapshot()
    def test_pipeline_immediate(self):
        with self.r.pipeline() as p:
            p.set("a", 1)
            p.immediate_execute_command("SET", "a", 1)
            p.execute()

    @snapshot()
    def test_meta_override(self):
        r = self.r
        pin = Pin.get_from(r)
        if pin:
            pin._clone(tags={"cheese": "camembert"}).onto(r)

        r.get("cheese")

    def test_patch_unpatch(self):
        tracer = DummyTracer()

        # Test patch idempotence
        patch()
        patch()

        r = valkey.Valkey(port=VALKEY_CONFIG["port"])
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

        # Test unpatch
        unpatch()

        r = valkey.Valkey(port=VALKEY_CONFIG["port"])
        r.get("key")

        spans = tracer.pop()
        assert not spans, spans

        # Test patch again
        patch()

        r = valkey.Valkey(port=VALKEY_CONFIG["port"])
        Pin.get_from(r)._clone(tracer=tracer).onto(r)
        r.get("key")

        spans = tracer.pop()
        assert spans, spans
        assert len(spans) == 1

    @snapshot()
    def test_opentracing(self):
        """Ensure OpenTracing works with valkey."""
        ot_tracer = init_tracer("valkey_svc", ddtrace.tracer)

        with ot_tracer.start_active_span("valkey_get"):
            us = self.r.get("cheese")
            assert us is None

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="mysvc"))
    @snapshot()
    def test_user_specified_service(self):
        from ddtrace import config

        assert config.service == "mysvc"

        self.r.get("cheese")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_VALKEY_SERVICE="myvalkey"))
    @snapshot()
    def test_env_user_specified_valkey_service(self):
        self.r.get("cheese")

        self.reset()

        # Global config
        with self.override_config("valkey", dict(service="cfg-valkey")):
            self.r.get("cheese")

        self.reset()

        # Manual override
        Pin._override(self.r, service="mysvc", tracer=self.tracer)
        self.r.get("cheese")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_SERVICE="app-svc", DD_VALKEY_SERVICE="env-valkey"))
    @snapshot()
    def test_service_precedence(self):
        self.r.get("cheese")

        self.reset()

        # Do a manual override
        Pin._override(self.r, service="override-valkey", tracer=self.tracer)
        self.r.get("cheese")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_VALKEY_CMD_MAX_LENGTH="10"))
    @snapshot()
    def test_custom_cmd_length_env(self):
        self.r.get("here-is-a-long-key-name")

    @snapshot()
    def test_custom_cmd_length(self):
        with self.override_config("valkey", dict(cmd_max_length=7)):
            self.r.get("here-is-a-long-key-name")

    @TracerTestCase.run_in_subprocess(env_overrides=dict(DD_VALKEY_RESOURCE_ONLY_COMMAND="false"))
    @snapshot()
    def test_full_command_in_resource_env(self):
        self.r.get("put_key_in_resource")
        p = self.r.pipeline(transaction=False)
        p.set("pipeline-cmd1", 1)
        p.set("pipeline-cmd2", 2)
        p.execute()

    @snapshot()
    def test_full_command_in_resource_config(self):
        with self.override_config("valkey", dict(resource_only_command=False)):
            self.r.get("put_key_in_resource")
            p = self.r.pipeline(transaction=False)
            p.set("pipeline-cmd1", 1)
            p.set("pipeline-cmd2", 2)
            p.execute()
