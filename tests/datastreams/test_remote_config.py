"""Remote-config enable/disable of classic DSM.

Drives ``ddtrace._trace.product.apm_tracing_rc`` with synthetic merged
lib_config payloads (the view the APMTracingCallback dispatches on the
``apm-tracing.rc`` event) and asserts the runtime behavior: processor
lifecycle, kafka hook recording and pathway header injection, the startup
env var matrix, and enabling with no messaging client library installed.
"""

from collections import namedtuple
import copy
import os
import signal
import threading
import time

import pytest

import ddtrace
from ddtrace._trace.product import APMCapabilities
from ddtrace._trace.product import apm_tracing_rc
from ddtrace.internal import core
from ddtrace.internal import datastreams
from ddtrace.internal import forksafe
from ddtrace.internal.datastreams import aiokafka as aiokafka_dsm
from ddtrace.internal.datastreams import botocore as botocore_dsm
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams import exit_signal
from ddtrace.internal.datastreams import google_cloud_pubsub as pubsub_dsm
from ddtrace.internal.datastreams import kombu as kombu_dsm
from ddtrace.internal.datastreams.processor import PROPAGATION_KEY_BASE_64
from ddtrace.internal.native import RemoteConfigCapabilities
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings._config import config
from tests.datastreams.utils import all_pathway_stat_keys


try:
    from ddtrace.internal.datastreams import kafka as kafka_dsm
except ImportError:  # confluent_kafka is not installed in this environment
    kafka_dsm = None

requires_confluent_kafka = pytest.mark.skipif(
    kafka_dsm is None, reason="ddtrace.internal.datastreams.kafka requires confluent_kafka"
)


class FakeMessage:
    def __init__(self, topic="rc-topic", error=None):
        self._topic = topic
        self._error = error

    def topic(self):
        return self._topic

    def error(self):
        return self._error

    def headers(self):
        return []

    def value(self):
        return b"payload"

    def key(self):
        return b"key"

    def offset(self):
        return 3

    def partition(self):
        return 0


class FakeConsumer:
    _group_id = "rc-group"
    _auto_commit = True


class FakeProducer:
    pass


@pytest.fixture(autouse=True)
def isolated_dsm_state():
    """Isolate RC state and the process-level processor between tests."""
    config_item = config._config["_data_streams_enabled"]
    original_rc_value = config_item._rc_value
    original_env_value = config_item._env_value
    # Start from off regardless of this suite's DD_DATA_STREAMS_ENABLED value.
    config_item._rc_value = None
    config_item._env_value = None

    original_processor = datastreams._processor
    datastreams._processor = None

    yield

    config_item._rc_value = original_rc_value
    config_item._env_value = original_env_value
    processor = datastreams._processor
    datastreams._processor = original_processor
    if processor is not None and processor is not original_processor:
        try:
            with processor._lock:
                processor._buckets.clear()
            processor.shutdown(0.5)
        except Exception:
            pass


def _enable_dsm():
    apm_tracing_rc({"data_streams_enabled": True}, config)


def _disable_dsm():
    apm_tracing_rc({"data_streams_enabled": False}, config)


def _produce(kwargs=None):
    """Drive the kafka produce hook with the kafka context items the patch sets."""
    kwargs = {} if kwargs is None else kwargs
    core.discard_item("kafka_cluster_id")
    core.set_item("kafka_topic", "rc-topic")
    try:
        kafka_dsm.dsm_kafka_message_produce(FakeProducer(), (), kwargs, False, None)
    finally:
        core.discard_item("kafka_topic")
        core.discard_item("kafka_cluster_id")
    return kwargs


def _stat_keys(processor):
    return all_pathway_stat_keys(processor)


# -- enable from off ------------------------------------------------------


@requires_confluent_kafka
def test_rc_enable_starts_processor_and_records_stats():
    assert data_streams_processor() is None

    _enable_dsm()

    processor = data_streams_processor()
    assert processor is not None
    assert processor.status == ServiceStatus.RUNNING
    assert config._config["_data_streams_enabled"].source() == "remote_config"

    kwargs = _produce({"headers": {}})
    assert PROPAGATION_KEY_BASE_64 in kwargs["headers"], "pathway header not injected on produce"
    assert any("direction:out" in key[0] for key in _stat_keys(processor)), "no produce stats recorded"


@requires_confluent_kafka
def test_rc_enable_injects_into_list_headers():
    _enable_dsm()

    kwargs = _produce({"headers": [("app-header", b"value")]})

    header_keys = [key for key, _ in kwargs["headers"]]
    assert header_keys == ["app-header", PROPAGATION_KEY_BASE_64]


def test_rc_enable_never_raises_when_hook_import_fails(monkeypatch):
    def failing_import():
        raise ImportError("incompatible messaging client")

    monkeypatch.setattr(datastreams, "_import_integration_modules", failing_import)

    _enable_dsm()

    assert config._data_streams_enabled is True
    # The processor is still created lazily on the first hook call.
    assert data_streams_processor() is not None


@requires_confluent_kafka
def test_rc_enable_hooks_registered_dispatch_path():
    # The dsm handlers are subscribed unconditionally, so enabling at runtime is
    # enough for a dispatched event to record stats.
    _enable_dsm()
    processor = data_streams_processor()

    kwargs = {}
    core.set_item("kafka_topic", "rc-topic")
    try:
        core.dispatch("kafka.produce.start", (FakeProducer(), (), kwargs, False, None))
    finally:
        core.discard_item("kafka_topic")

    assert PROPAGATION_KEY_BASE_64 in kwargs["headers"]
    assert any("direction:out" in key[0] for key in _stat_keys(processor))


@requires_confluent_kafka
def test_rc_enable_consume_records_stats():
    _enable_dsm()
    processor = data_streams_processor()

    core.set_item("kafka_topic", "rc-topic")
    try:
        kafka_dsm.dsm_kafka_message_consume(FakeConsumer(), FakeMessage(), None)
    finally:
        core.discard_item("kafka_topic")

    assert any("direction:in" in key[0] for key in _stat_keys(processor))


# -- disable after enabled -------------------------------------------------


@requires_confluent_kafka
def test_rc_disable_stops_processor_and_records_nothing():
    _enable_dsm()
    processor = data_streams_processor()
    _produce({"headers": {}})
    assert _stat_keys(processor), "no stats recorded while enabled"

    _disable_dsm()

    assert data_streams_processor() is None
    assert processor.status == ServiceStatus.STOPPED
    assert config._data_streams_enabled is False

    kwargs = _produce({"headers": {}})
    assert PROPAGATION_KEY_BASE_64 not in kwargs.get("headers", {})
    assert "callback" not in kwargs, "the no-op handler must not even wrap the delivery callback"
    # The shutdown flushed (and cleared) the buckets; nothing was recorded after.
    assert _stat_keys(processor) == set()


def test_rc_disable_never_raises_when_processor_already_stopped():
    _enable_dsm()
    processor = data_streams_processor()
    processor.stop()  # already stopped: shutdown() raises ServiceStatusError

    _disable_dsm()

    assert datastreams._processor is None
    assert data_streams_processor() is None


class SignalingLock:
    """Wraps the processor lock and signals once a thread starts waiting on it."""

    def __init__(self, lock):
        self._lock = lock
        self.waiting = threading.Event()

    def __enter__(self):
        self.waiting.set()
        return self._lock.__enter__()

    def __exit__(self, *exc_info):
        return self._lock.__exit__(*exc_info)


def _call_while_lock_is_held(monkeypatch, target):
    """Run target in a thread blocked on the processor lock; return a release-and-join callable."""
    lock = SignalingLock(threading.Lock())
    monkeypatch.setattr(datastreams, "_processor_lock", lock)
    lock._lock.acquire()
    results = []
    thread = threading.Thread(target=lambda: results.append(target()))
    thread.start()
    assert lock.waiting.wait(5), "the thread never reached the processor lock"

    def release_and_join():
        lock._lock.release()
        thread.join(5)
        assert not thread.is_alive()
        return results[0]

    return release_and_join


def test_rc_disable_while_creation_waits_on_lock_creates_no_processor(monkeypatch):
    config._config["_data_streams_enabled"]._rc_value = True
    release_and_join = _call_while_lock_is_held(monkeypatch, data_streams_processor)

    config._config["_data_streams_enabled"]._rc_value = False

    assert release_and_join() is None
    assert datastreams._processor is None


class FakeProcessor:
    def __init__(self):
        self.shutdown_while_locked = None

    def shutdown(self, timeout):
        self.shutdown_while_locked = datastreams._processor_lock._lock.locked()


def test_rc_stop_waits_for_a_processor_being_created_and_shuts_it_down_unlocked(monkeypatch):
    release_and_join = _call_while_lock_is_held(monkeypatch, datastreams.stop_data_streams_processor)
    # Stands in for a processor the lock holder finishes constructing.
    processor = FakeProcessor()
    datastreams._processor = processor

    release_and_join()

    assert datastreams._processor is None
    assert processor.shutdown_while_locked is False


def test_processor_lock_is_fork_safe():
    assert isinstance(datastreams._processor_lock, forksafe.ResetObject)


def test_fork_child_does_not_inherit_a_held_processor_lock(run_python_code_in_subprocess):
    code = """
import os
import threading

from ddtrace.internal import datastreams

locked = threading.Event()
release = threading.Event()


def hold_lock():
    with datastreams._processor_lock:
        locked.set()
        release.wait()


holder = threading.Thread(target=hold_lock)
holder.start()
locked.wait()

pid = os.fork()
if pid == 0:
    os._exit(0 if datastreams._processor_lock.acquire(timeout=2) else 1)

release.set()
holder.join()
_, status = os.waitpid(pid, 0)
assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status
print("OK")
"""
    worktree_root = os.path.dirname(os.path.dirname(os.path.abspath(ddtrace.__file__)))
    env = os.environ.copy()
    env["PYTHONPATH"] = worktree_root + os.pathsep + env.get("PYTHONPATH", "")
    out, err, status, _ = run_python_code_in_subprocess(code, env=env, timeout=30)
    assert status == 0, (out, err)
    assert b"OK" in out


@requires_confluent_kafka
def test_rc_key_removed_falls_back_to_startup_value():
    # A later payload without the key (or the record deleted) drops the RC
    # override: the env var/default governs again.
    _enable_dsm()
    assert data_streams_processor() is not None

    apm_tracing_rc({}, config)

    assert config._data_streams_enabled is False
    assert data_streams_processor() is None


# -- every hook is a no-op after a disable ------------------------------------
# The hook modules below do not import their client library, so the handlers
# are called directly with fake arguments.

SQS_QUEUE_URL = "https://sqs.us-east-1.amazonaws.com/123456789012/rc-queue"
FakeTopicPartition = namedtuple("FakeTopicPartition", ["topic", "partition"])


class FakeKombuQueue:
    name = "rc-queue"


class FakeKombuConsumer:
    queues = [FakeKombuQueue()]


class FakeKombuMessage:
    body = b"payload"
    headers: dict = {}


class FakePubsubMessage:
    data = b"payload"
    attributes: dict = {}
    ordering_key = ""


class FakeAIOKafkaConsumer:
    _group_id = "rc-group"
    _enable_auto_commit = True


class FakeAIOKafkaRecord:
    topic = "rc-topic"
    partition = 0
    offset = 3
    value = b"payload"
    key = b"key"
    headers: list = []


class FakeRecordMetadata:
    topic = "rc-topic"
    partition = 0
    offset = 3


class FakeGroupCoordinator:
    group_id = "rc-group"


def _span_context():
    span = ddtrace.tracer.trace("dsm-rc-test")
    span.finish()
    return core.context_with_data("dsm-rc-test", _inner_span=span)


def _mutate(hook, carrier, *args):
    """Call hook and return the carrier before and after the call."""
    before = copy.deepcopy(carrier)
    hook(*args)
    return before, carrier


def _kinesis_produce():
    carrier = {}
    record = {"Data": b"payload", "PartitionKey": "pk"}
    return _mutate(botocore_dsm.handle_kinesis_produce, carrier, None, "rc-stream", carrier, record)


def _eventbridge_produce():
    carrier = {}
    entry = {"EventBusName": "rc-bus", "DetailType": "rc-detail", "Detail": "{}"}
    return _mutate(botocore_dsm.handle_eventbridge_produce, carrier, None, None, "events", carrier, {}, entry)


def _sqs_produce():
    carrier = {}
    params = {"QueueUrl": SQS_QUEUE_URL, "MessageBody": "payload"}
    return _mutate(botocore_dsm.handle_sqs_sns_produce, carrier, None, None, "sqs", carrier, params)


def _sqs_prepare():
    params = {"QueueUrl": SQS_QUEUE_URL}
    return _mutate(botocore_dsm.handle_sqs_prepare, params, params)


def _sqs_receive():
    botocore_dsm.handle_sqs_receive(None, {"QueueUrl": SQS_QUEUE_URL}, {"Messages": [{"Body": "payload"}]})


def _kinesis_receive():
    botocore_dsm.handle_kinesis_receive(None, {"StreamName": "rc-stream"}, time.time(), None, {"Data": b"payload"})


def _kombu_produce():
    # Positional _publish args: body, headers and routing key at their kombu indexes.
    args = [b"payload", None, None, None, {}, None, "rc-queue", None, None, ""]
    return _mutate(kombu_dsm.handle_kombu_produce, args, args, {}, None)


def _kombu_consume():
    kombu_dsm.handle_kombu_consume(FakeKombuConsumer(), FakeKombuMessage(), None)


def _pubsub_send():
    kwargs = {}
    return _mutate(pubsub_dsm.dsm_pubsub_send, kwargs, ("projects/p/topics/rc-topic", b"payload"), kwargs, None)


def _pubsub_receive():
    pubsub_dsm.dsm_pubsub_receive("projects/p/subscriptions/rc-sub", FakePubsubMessage(), None)


def _aiokafka_send_start():
    headers = []
    return _mutate(
        aiokafka_dsm.dsm_aiokafka_send_start, headers, "rc-topic", b"payload", b"key", headers, _span_context(), 0
    )


def _aiokafka_send_completed():
    aiokafka_dsm.dsm_aiokafka_send_completed(None, (None, None, None), FakeRecordMetadata())


def _aiokafka_consume():
    aiokafka_dsm.dsm_aiokafka_message_consume(FakeAIOKafkaConsumer(), _span_context(), None, FakeAIOKafkaRecord(), None)


def _aiokafka_getmany():
    messages = {FakeTopicPartition("rc-topic", 0): [FakeAIOKafkaRecord()]}
    aiokafka_dsm.dsm_aiokafka_many_messages_consume(FakeAIOKafkaConsumer(), _span_context(), messages)


def _aiokafka_commit():
    offsets = {FakeTopicPartition("rc-topic", 0): 3}
    aiokafka_dsm.dsm_aiokafka_message_commit(FakeGroupCoordinator(), (None, offsets), {})


def _kafka_consume():
    core.set_item("kafka_topic", "rc-topic")
    try:
        kafka_dsm.dsm_kafka_message_consume(FakeConsumer(), FakeMessage(), None)
    finally:
        core.discard_item("kafka_topic")


def _kafka_commit():
    kafka_dsm.dsm_kafka_message_commit(FakeConsumer(), (FakeMessage(),), {})


def _recorded_anything(processor):
    with processor._lock:
        return any(
            bucket.pathway_stats or bucket.latest_produce_offsets or bucket.latest_commit_offsets
            for bucket in processor._buckets.values()
        )


# Each hook returns (carrier before, carrier after) when it mutates a carrier, else None.
HOOK_CASES = {
    "botocore-kinesis-produce": (_kinesis_produce, True),
    "botocore-eventbridge-produce": (_eventbridge_produce, True),
    "botocore-sqs-produce": (_sqs_produce, True),
    "botocore-sqs-prepare": (_sqs_prepare, False),
    "botocore-sqs-receive": (_sqs_receive, True),
    "botocore-kinesis-receive": (_kinesis_receive, True),
    "kombu-produce": (_kombu_produce, True),
    "kombu-consume": (_kombu_consume, True),
    "pubsub-send": (_pubsub_send, True),
    "pubsub-receive": (_pubsub_receive, True),
    "aiokafka-send-start": (_aiokafka_send_start, True),
    "aiokafka-send-completed": (_aiokafka_send_completed, True),
    "aiokafka-consume": (_aiokafka_consume, True),
    "aiokafka-getmany": (_aiokafka_getmany, True),
    "aiokafka-commit": (_aiokafka_commit, True),
    "kafka-consume": (_kafka_consume, True),
    "kafka-commit": (_kafka_commit, True),
}


@pytest.mark.parametrize("case", HOOK_CASES.keys())
def test_rc_disable_makes_hook_a_noop(case):
    call_hook, records_stats = HOOK_CASES[case]
    if case.startswith("kafka-") and kafka_dsm is None:
        pytest.skip("ddtrace.internal.datastreams.kafka requires confluent_kafka")

    # While enabled the hook acts, so the disabled assertions below are meaningful.
    _enable_dsm()
    processor = data_streams_processor()
    with processor._lock:
        processor._buckets.clear()
    carriers = call_hook()
    assert _recorded_anything(processor) is records_stats
    if carriers is not None:
        before, after = carriers
        assert after != before, "the enabled hook left its carrier untouched"

    # Cleared before the disable so its final flush has nothing to send (and no backoff).
    with processor._lock:
        processor._buckets.clear()
    _disable_dsm()

    carriers = call_hook()
    assert not _recorded_anything(processor)
    assert datastreams._processor is None, "the disabled hook created a processor"
    if carriers is not None:
        before, after = carriers
        assert after == before, "the disabled hook changed its carrier"


# -- exit signal handler across re-enables ----------------------------------


def _exit_signal_handlers():
    return signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)


def test_rc_reenable_keeps_one_exit_signal_handler():
    _enable_dsm()
    first = data_streams_processor()
    handlers = _exit_signal_handlers()
    assert first._atexit_func in exit_signal.shutdown_callbacks

    _disable_dsm()
    assert first._atexit_func not in exit_signal.shutdown_callbacks
    _enable_dsm()
    second = data_streams_processor()
    assert second is not first
    assert second._atexit_func in exit_signal.shutdown_callbacks
    _disable_dsm()

    # No handler was chained on top of the first one.
    assert _exit_signal_handlers() == handlers
    assert second._atexit_func not in exit_signal.shutdown_callbacks


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX SIGTERM handling")
def test_sigterm_after_rc_reenable_shuts_down_only_the_live_processor(run_python_code_in_subprocess):
    code = """
import os
import signal

from ddtrace._trace.product import apm_tracing_rc
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.datastreams.processor import DataStreamsProcessor
from ddtrace.internal.settings._config import config

original_shutdown = DataStreamsProcessor.shutdown

def shutdown(self, timeout):
    print("shutdown", self.label, flush=True)
    original_shutdown(self, timeout)

DataStreamsProcessor.shutdown = shutdown

apm_tracing_rc({"data_streams_enabled": True}, config)
data_streams_processor().label = "first"
apm_tracing_rc({"data_streams_enabled": False}, config)
apm_tracing_rc({"data_streams_enabled": True}, config)
second = data_streams_processor()
second.label = "second"
second._flush_stats_with_backoff = lambda payload: print("flushed", flush=True)
second.set_checkpoint(["direction:out", "topic:rc-topic", "type:kafka"])

os.kill(os.getpid(), signal.SIGTERM)
"""
    env = os.environ.copy()
    env.pop("DD_DATA_STREAMS_ENABLED", None)
    env["_DD_TRACE_STATS_WRITER_INTERVAL"] = "1000"
    out, err, status, _ = run_python_code_in_subprocess(code, env=env, timeout=30)
    assert status == -signal.SIGTERM, (out, err)
    assert out.decode().split("\n")[:-1] == ["shutdown first", "shutdown second", "flushed"], (out, err)


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX SIGTERM handling")
@pytest.mark.parametrize("trace_enabled", [None, "false"])
def test_sigterm_flushes_once_after_rc_enable_from_a_worker_thread(run_python_code_in_subprocess, trace_enabled):
    # The remote config worker thread cannot install signal handlers: the
    # handler installed on the main thread at product start must cover it.
    code = """
import os
import signal
import threading

import ddtrace.auto  # noqa: F401

from ddtrace.internal.core.event_hub import dispatch
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.settings._config import config

assert data_streams_processor() is None

def enable_from_remote_config():
    dispatch("apm-tracing.rc", ({"data_streams_enabled": True}, config))
    processor = data_streams_processor()
    processor._flush_stats_with_backoff = lambda payload: print("flushed", flush=True)
    processor.set_checkpoint(["direction:out", "topic:rc-topic", "type:kafka"])

worker = threading.Thread(target=enable_from_remote_config)
worker.start()
worker.join()

os.kill(os.getpid(), signal.SIGTERM)
"""
    env = os.environ.copy()
    env.pop("DD_DATA_STREAMS_ENABLED", None)
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
    env["_DD_TRACE_STATS_WRITER_INTERVAL"] = "1000"
    env.pop("DD_TRACE_ENABLED", None)
    if trace_enabled is not None:
        env["DD_TRACE_ENABLED"] = trace_enabled
    out, err, status, _ = run_python_code_in_subprocess(code, env=env, timeout=30)
    assert status == -signal.SIGTERM, (out, err)
    assert out.decode().split("\n")[:-1] == ["flushed"], (out, err)


# -- startup env var matrix ------------------------------------------------


@pytest.mark.subprocess(
    parametrize={
        "DD_DATA_STREAMS_ENABLED": [None, "true", "false"],
        "_DD_TEST_RC_DATA_STREAMS": [None, "true", "false"],
    }
)
def test_startup_env_var_matrix(DD_DATA_STREAMS_ENABLED, _DD_TEST_RC_DATA_STREAMS):
    # Runs in a subprocess per combination: the env var is read at import time.
    # The parametrize names are delivered to this subprocess as env vars.
    import os

    from ddtrace._trace.product import apm_tracing_rc
    from ddtrace.internal import datastreams
    from ddtrace.internal.settings._config import config

    startup_value = {"true": True, "false": False}.get(os.environ.get("DD_DATA_STREAMS_ENABLED"), False)
    assert config._data_streams_enabled is startup_value

    rc_value = {"true": True, "false": False}.get(os.environ.get("_DD_TEST_RC_DATA_STREAMS"))

    apm_tracing_rc({"data_streams_enabled": rc_value} if rc_value is not None else {}, config)

    expected = startup_value if rc_value is None else rc_value
    assert config._data_streams_enabled is expected
    assert (datastreams.data_streams_processor() is not None) is expected

    if rc_value is not None:
        # Removing the RC key restores the env var as the governing value.
        apm_tracing_rc({}, config)
        assert config._data_streams_enabled is startup_value
        assert (datastreams.data_streams_processor() is not None) is startup_value


# -- enable with no messaging library installed ----------------------------


def test_rc_enable_with_no_messaging_libs_is_noop(monkeypatch):
    # No messaging client library importable: no hook module is imported, no
    # crash; the processor still starts (it is client-agnostic).
    monkeypatch.setattr(datastreams, "required_module_to_integration", {"_dd_missing_lib_": "_dd_missing_integration"})

    _enable_dsm()

    import sys

    assert "ddtrace.internal.datastreams._dd_missing_integration" not in sys.modules
    assert data_streams_processor() is not None


# -- fork after RC enable ---------------------------------------------------


def test_fork_child_reports_own_windows(run_python_code_in_subprocess):
    code = """
import os
import sys
import time

import ddtrace

from ddtrace._trace.product import apm_tracing_rc
from ddtrace.internal import datastreams
from ddtrace.internal.datastreams import data_streams_processor
from ddtrace.internal.settings._config import config

apm_tracing_rc({"data_streams_enabled": True}, config)
parent_processor = data_streams_processor()
assert parent_processor is not None

pid = os.fork()
if pid == 0:
    try:
        child_processor = data_streams_processor()
        assert child_processor is parent_processor, "child lost the RC-enabled processor"
        with child_processor._lock:
            child_processor._buckets.clear()
        child_processor.set_checkpoint(["direction:out", "topic:child-topic", "type:kafka"])

        keys = set()
        with child_processor._lock:
            for bucket in child_processor._buckets.values():
                keys.update(bucket.pathway_stats.keys())
        assert any("topic:child-topic" in key[0] for key in keys), keys

        # The periodic flush thread restarts in the child after the fork, so
        # the child reports its own windows.
        deadline = time.time() + 5
        worker = child_processor._worker
        while time.time() < deadline and (worker is None or not worker.is_started()):
            time.sleep(0.05)
            worker = child_processor._worker
        assert worker is not None and worker.is_started(), "flush thread did not restart in child"
        print("CHILD-OK")
        os._exit(0)
    except Exception:
        import traceback

        traceback.print_exc()
        os._exit(1)

_, status = os.waitpid(pid, 0)
assert os.WIFEXITED(status), status
assert os.WEXITSTATUS(status) == 0, status
print("OK")
"""
    worktree_root = os.path.dirname(os.path.dirname(os.path.abspath(ddtrace.__file__)))
    env = os.environ.copy()
    env["PYTHONPATH"] = worktree_root + os.pathsep + env.get("PYTHONPATH", "")
    env["_DD_TRACE_STATS_WRITER_INTERVAL"] = "1000"
    env["DD_DATA_STREAMS_ENABLED"] = None  # enabled via RC below, not at startup
    out, err, status, _ = run_python_code_in_subprocess(code, env=env, timeout=30)
    assert status == 0, (out, err)
    assert b"OK" in out


# -- payload size without protobuf/avro instrumentation ---------------------


@requires_confluent_kafka
def test_rc_enabled_records_stats_without_payload_size_for_opaque_messages():
    # Protobuf is only patched when DSM is on at startup, so a message enabled
    # via RC carries no byte size: the handler records stats with size 0, no crash.
    _enable_dsm()
    processor = data_streams_processor()

    core.set_item("kafka_topic", "rc-topic")
    try:
        kwargs = {"headers": {}, "value": object(), "key": object()}
        kafka_dsm.dsm_kafka_message_produce(FakeProducer(), (), kwargs, False, None)
    finally:
        core.discard_item("kafka_topic")

    assert PROPAGATION_KEY_BASE_64 in kwargs["headers"]
    assert any("direction:out" in key[0] for key in _stat_keys(processor))


# -- no-op overhead while inactive ------------------------------------------


@requires_confluent_kafka
def test_dsm_off_handler_is_a_cheap_noop():
    # With DSM inactive the produce handler must return before any argument
    # extraction: the caller's kwargs are left untouched.
    kwargs = {"headers": {}}
    result = _produce(kwargs)
    assert result == {"headers": {}}


# -- end-to-end poller chain -------------------------------------------------


def test_apm_tracing_callback_end_to_end_enables_dsm():
    # APMTracingCallback.__call__ -> "apm-tracing.rc" dispatch -> apm_tracing_rc,
    # the chain the remote config poller drives in production.
    from ddtrace.internal.core.event_hub import on
    from ddtrace.internal.core.event_hub import reset as reset_listener
    from ddtrace.internal.remoteconfig.products.apm_tracing import APMTracingCallback
    from tests.utils import remote_config_build_payload as build_payload

    on("apm-tracing.rc", apm_tracing_rc, "test-dsm-remote-config")
    try:
        payload = build_payload(
            "APM_TRACING",
            {"service_target": {"service": "*", "env": "*"}, "lib_config": {"data_streams_enabled": True}},
            "config",
        )
        APMTracingCallback()([payload])
    finally:
        reset_listener("apm-tracing.rc", apm_tracing_rc)

    processor = data_streams_processor()
    assert processor is not None
    assert config._config["_data_streams_enabled"].source() == "remote_config"


# -- capability and accepted values ------------------------------


def test_data_streams_capability_bit_is_pinned():
    assert int(RemoteConfigCapabilities.ApmTracingDataStreamsEnabled) == 20
    assert RemoteConfigCapabilities.ApmTracingDataStreamsEnabled in APMCapabilities


@pytest.mark.parametrize(
    "value,expected_enabled",
    [
        (True, True),
        (False, False),
        ("true", True),
        ("TRUE", True),
        ("false", False),
        ("False", False),
    ],
)
def test_data_streams_enabled_accepts_bools_and_true_false_strings(value, expected_enabled):
    apm_tracing_rc({"data_streams_enabled": value}, config)

    assert config._config["_data_streams_enabled"].source() == "remote_config"
    assert config._data_streams_enabled is expected_enabled
    assert (data_streams_processor() is not None) is expected_enabled
