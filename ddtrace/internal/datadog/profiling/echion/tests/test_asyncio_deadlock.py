from tests.utils import run_target


def test_asyncio_deadlock():
    result, data = run_target("target_async_deadlock")
    assert result.returncode == 0, result.stderr.decode()
