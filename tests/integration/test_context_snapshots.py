import pytest

from tests.integration.utils import AGENT_VERSION
from tests.utils import snapshot


pytestmark = pytest.mark.skipif(AGENT_VERSION != "testagent", reason="Tests only compatible with a testagent")


@snapshot()
def test_context_multiprocess(run_python_code_in_subprocess):
    # Testing example from our docs:
    # https://ddtrace.readthedocs.io/en/stable/advanced_usage.html#tracing-across-processes
    code = """
import ddtrace.auto

from multiprocessing import Process
import time

from ddtrace.trace import tracer


def _target(ctx):
    tracer.context_provider.activate(ctx)
    with tracer.trace("proc"):
        time.sleep(0.1)
    tracer.shutdown()


def main():
    with tracer.trace("work"):
        proc = Process(target=_target, args=(tracer.current_trace_context(), ))
        proc.start()
        time.sleep(0.25)
        proc.join()


if __name__ == "__main__":
    main()
    """

    stdout, stderr, status, _ = run_python_code_in_subprocess(code=code)
    assert status == 0, (stdout, stderr)
    assert stdout == b"", stderr
    assert stderr == b"", stdout
