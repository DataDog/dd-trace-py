import pytest
import ray
from ray.util.tracing import tracing_helper

from tests.utils import TracerTestCase
from tests.utils import override_config


RAY_SNAPSHOT_IGNORES = [
    # Ray-specific dynamic values that change between runs
    "meta.ray.job_id",
    "meta.ray.node_id",
    "meta.ray.worker_id",
    "meta.ray.actor_id",
    "meta.ray.task_id",
    "meta.ray.submission_id",
    "meta.ray.hostname",
    "meta.ray.pid",
    "meta.tracestate",
    "meta.traceparent",
    "meta.error.message",
    "meta.ray.job.message",
    "meta.error.stack",
    "meta._dd.base_service",
    "meta._dd.hostname",
    # Actor method empty arguments are encoded differently between ray versions
    "meta.ray.actor_method.args",
    # Base service sometimes gets set to a different value in CI than in the local environment,
    # ignore it to make the tests pass in both environments
    "meta._dd.base_service",
    "meta._dd.hostname",
    "metrics._dd.partial_version",
    "metrics._dd.was_long_running",
]


class TestRayIntegration(TracerTestCase):
    """Test Ray integration with actual cluster setup and job submission"""

    @pytest.fixture(autouse=True, scope="class")
    def ray_cluster(self):
        # Configure Ray with minimal resource usage for CI
        ray.init(
            _tracing_startup_hook="ddtrace.contrib.ray:setup_tracing",
            local_mode=True,
            # Limit resources to reduce CI load
            num_cpus=1,
            num_gpus=0,
            object_store_memory=78643200,
            # Disable dashboard to save memory
            include_dashboard=False,
            # Set log level to reduce I/O
            log_to_driver=False,
        )
        tracing_helper._global_is_tracing_enabled = False
        yield
        ray.shutdown()

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_task", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_task(self):
        @ray.remote
        def add_one(x):
            return x + 1

        futures = [add_one.remote(i) for i in range(2)]  # Reduced from 4 to 2 tasks
        results = ray.get(futures)
        assert results == [1, 2], f"Unexpected results: {results}"

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_actor", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_actor(self):
        @ray.remote
        class Counter:
            def __init__(self, **kwargs):
                self.value = 0

            def increment(self):
                self.value += 1
                return self.value

            def get_value(self):
                return self.value

            def increment_and_get(self):
                self.increment()
                return self.get_value()

            def increment_get_and_double(self):
                value = self.increment_and_get()
                return value * 2

        counter_actor = Counter.remote()
        current_value = ray.get(counter_actor.increment_get_and_double.remote())

        assert current_value == 2, f"Unexpected result: {current_value}"

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_nested_tasks", ignores=RAY_SNAPSHOT_IGNORES)
    def test_nested_tasks(self):
        @ray.remote
        def add_one(x):
            return x + 1

        @ray.remote
        def submit_addition_task(x):
            futures = add_one.remote(x + 1)
            return ray.get(futures)

        future = submit_addition_task.remote(2)
        results = ray.get(future)
        assert results == 4, f"Unexpected results: {results}"

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_actor_and_task", ignores=RAY_SNAPSHOT_IGNORES)
    def test_actor_and_task(self):
        @ray.remote
        def compute_value(x):
            return x + 1

        @ray.remote
        def batch_compute(values):
            futures = [compute_value.remote(val) for val in values]
            return ray.get(futures)

        @ray.remote
        class ComputationManager:
            def __init__(self, **kwargs):
                self.computation_count = 0
                self.results = []

            def increment_count(self):
                self.computation_count += 1
                return self.computation_count

            def get_count(self):
                return self.computation_count

            def add_result(self, result):
                self.results.append(result)
                return len(self.results)

            def get_results(self):
                return self.results

            def compute_and_store(self, values):
                self.increment_count()

                future = batch_compute.remote(values)
                results = ray.get(future)

                for result in results:
                    self.add_result(result)

                return {
                    "computation_count": self.get_count(),
                    "results": set(results),
                    "total_stored": len(self.get_results()),
                }

        manager = ComputationManager.remote()
        result = ray.get(manager.compute_and_store.remote([2, 3, 4]))
        assert result == {
            "computation_count": 1,
            "results": {3, 4, 5},
            "total_stored": 3,
        }, f"Unexpected results: {result['results']}"

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_actor_interactions", ignores=RAY_SNAPSHOT_IGNORES)
    def test_actor_interactions(self):
        @ray.remote
        class Sender:
            def __init__(self, **kwargs):
                self.sent_count = 0

            def send_message(self, receiver, message):
                self.sent_count += 1
                future = receiver.receive_message.remote(message)
                return ray.get(future)

            def get_sent_count(self):
                return self.sent_count

        @ray.remote
        class Receiver:
            def __init__(self, **kwargs):
                self.received_messages = []

            def receive_message(self, message):
                self.received_messages.append(message)
                return f"received: {message}"

            def get_messages(self):
                return self.received_messages

        @ray.remote
        def run_test():
            sender = Sender.remote()
            receiver = Receiver.remote()

            result = ray.get(sender.send_message.remote(receiver, "hello"))
            sent_count = ray.get(sender.get_sent_count.remote())
            messages = ray.get(receiver.get_messages.remote())
            return result, sent_count, messages

        result, sent_count, messages = ray.get(run_test.remote())

        assert result == "received: hello"
        assert sent_count == 1
        assert messages == ["hello"]

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_core_api_deactivated", ignores=RAY_SNAPSHOT_IGNORES)
    def test_core_api_deactivated(self):
        @ray.remote
        def add_one(x):
            return x + 1

        done, running = ray.wait([add_one.remote(42)], num_returns=1, timeout=60)
        assert running == [], f"Expected no running tasks, got {len(running)}"
        assert ray.get(done) == [43], f"Expected done to be [43], got {done}"

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_get", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_get(self):
        with override_config("ray", dict(trace_core_api=True)):

            @ray.remote
            def add_one(x):
                return x + 1

            results = ray.get([add_one.remote(x) for x in range(1)])
            assert results == [1], f"Expected [1], got {results}"

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_wait", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_wait(self):
        with override_config("ray", dict(trace_core_api=True)):

            @ray.remote
            def add_one(x):
                return x + 1

            done, running = ray.wait([add_one.remote(42)], num_returns=1, timeout=60)
            assert running == [], f"Expected no running tasks, got {len(running)}"
            assert ray.get(done) == [43], f"Expected done to be [43], got {done}"

    @pytest.mark.snapshot(token="tests.contrib.ray.test_ray.test_simple_put", ignores=RAY_SNAPSHOT_IGNORES)
    def test_simple_put(self):
        with override_config("ray", dict(trace_core_api=True)):

            @ray.remote
            def add_one(x):
                return x + 1

            answer = 42
            object_ref = ray.put(answer)
            futures = [add_one.remote(object_ref)]
            results = ray.get(futures)
            assert results == [43], f"Unexpected results: {results}"

    @pytest.mark.snapshot(
        token="tests.contrib.ray.test_ray.test_args_kwargs", ignores=RAY_SNAPSHOT_IGNORES, wait_for_num_traces=2
    )
    def test_args_kwargs(self):
        with override_config("ray", dict(trace_args_kwargs=True)):

            @ray.remote
            def add_one(x, y=1):
                return x + y

            results = ray.get(add_one.remote(1, y=2))
            assert results == 3, f"Unexpected results: {results}"

            @ray.remote
            class Counter:
                def __init__(self, **kwargs):
                    self.value = 0

                def increment(self, x, y=1):
                    self.value += x + y
                    return self.value

            counter_actor = Counter.remote()
            current_value = ray.get(counter_actor.increment.remote(1, y=2))

            assert current_value == 3, f"Unexpected result: {current_value}"
