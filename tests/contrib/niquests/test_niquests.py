import niquests
import pytest

from ddtrace.contrib.internal.niquests.patch import patch
from ddtrace.contrib.internal.niquests.patch import unpatch
from ddtrace.internal.compat import is_wrapted
from tests.utils import override_config


# host:port of httpbin container
HOST = "localhost"
PORT = 8001

DEFAULT_HEADERS = {
    "User-Agent": "python-niquests/x.xx.x",
}


def get_url(path):
    # type: (str) -> str
    return "http://{}:{}{}".format(HOST, PORT, path)


@pytest.fixture(autouse=True)
def patch_niquests():
    patch()
    try:
        yield
    finally:
        unpatch()


def test_patching():
    """
    When patching niquests library
        We wrap the correct methods
    When unpatching niquests library
        We unwrap the correct methods
    """
    assert is_wrapted(niquests.Session.send)
    assert is_wrapted(niquests.AsyncSession.send)
    assert is_wrapted(niquests.Session.gather)
    assert is_wrapted(niquests.AsyncSession.gather)

    unpatch()
    assert not is_wrapted(niquests.Session.send)
    assert not is_wrapted(niquests.AsyncSession.send)
    assert not is_wrapted(niquests.Session.gather)
    assert not is_wrapted(niquests.AsyncSession.gather)


@pytest.mark.snapshot(ignores=["meta.http.useragent"])
def test_niquests_service_name():
    """
    When using split_by_domain
        We set the span service name as a text type and not binary
    """
    client = niquests.Session()

    with override_config("niquests", {"split_by_domain": True}):
        resp = client.get(get_url("/status/200"))
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_200(snapshot_context):
    url = get_url("/status/200")

    with snapshot_context():
        resp = niquests.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200

    with snapshot_context():
        async with niquests.AsyncSession() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_configure_service_name(snapshot_context):
    url = get_url("/status/200")

    with override_config("niquests", {"service_name": "test-niquests-service-name"}):
        with snapshot_context():
            resp = niquests.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200

        with snapshot_context():
            async with niquests.AsyncSession() as client:
                resp = await client.get(url, headers=DEFAULT_HEADERS)
                assert resp.status_code == 200


def test_get_404(snapshot_context):
    url = get_url("/status/404")

    with snapshot_context():
        resp = niquests.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 404


def test_get_500(snapshot_context):
    url = get_url("/status/500")

    with snapshot_context():
        resp = niquests.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 500


def test_distributed_tracing_enabled(snapshot_context):
    url = get_url("/headers")

    with snapshot_context():
        resp = niquests.get(url, headers=DEFAULT_HEADERS)
        assert resp.status_code == 200


def test_distributed_tracing_disabled(snapshot_context):
    url = get_url("/headers")

    with override_config("niquests", {"distributed_tracing": False}):
        with snapshot_context():
            resp = niquests.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


def test_split_by_domain_enabled(snapshot_context):
    url = get_url("/status/200")

    with override_config("niquests", {"split_by_domain": True}):
        with snapshot_context():
            resp = niquests.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_async_get_200(snapshot_context):
    url = get_url("/status/200")

    with snapshot_context():
        async with niquests.AsyncSession() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 200


@pytest.mark.asyncio
async def test_async_get_404(snapshot_context):
    url = get_url("/status/404")

    with snapshot_context():
        async with niquests.AsyncSession() as client:
            resp = await client.get(url, headers=DEFAULT_HEADERS)
            assert resp.status_code == 404


def test_gather_multiplexed(snapshot_context):
    url = get_url("/status/200")

    with snapshot_context():
        with niquests.Session(multiplexed=True) as session:
            # Make multiple concurrent requests
            resp1 = session.get(url, headers=DEFAULT_HEADERS)
            resp2 = session.get(url, headers=DEFAULT_HEADERS)
            resp3 = session.get(url, headers=DEFAULT_HEADERS)

            # Gather all responses
            session.gather()

            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert resp3.status_code == 200


@pytest.mark.asyncio
async def test_gather_multiplexed_async(snapshot_context):
    url = get_url("/status/200")

    with snapshot_context():
        async with niquests.AsyncSession(multiplexed=True) as session:
            # Make multiple concurrent requests
            resp1 = await session.get(url, headers=DEFAULT_HEADERS)
            resp2 = await session.get(url, headers=DEFAULT_HEADERS)
            resp3 = await session.get(url, headers=DEFAULT_HEADERS)

            # Gather all responses
            await session.gather()

            assert resp1.status_code == 200
            assert resp2.status_code == 200
            assert resp3.status_code == 200


def test_gather_max_fetch(snapshot_context):
    url = get_url("/status/200")

    with snapshot_context():
        with niquests.Session(multiplexed=True) as session:
            # Make multiple concurrent requests
            resp1 = session.get(url, headers=DEFAULT_HEADERS)
            resp2 = session.get(url, headers=DEFAULT_HEADERS)
            session.get(url, headers=DEFAULT_HEADERS)  # Third request not fetched

            # Gather with max_fetch limit (only fetches first 2)
            session.gather(max_fetch=2)

            assert resp1.status_code == 200
            assert resp2.status_code == 200


def test_gather_non_multiplexed(snapshot_context):
    url = get_url("/status/200")

    with snapshot_context():
        with niquests.Session(multiplexed=False) as session:
            resp = session.get(url, headers=DEFAULT_HEADERS)

            # gather() is a no-op when multiplexed=False
            session.gather()

            assert resp.status_code == 200


def test_tracer_disabled(ddtrace_run_python_code_in_subprocess):
    code = """
import niquests
from ddtrace import tracer

tracer.enabled = False

resp = niquests.get("http://localhost:8001/status/200")
assert resp.status_code == 200
"""
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, (out, err)


def test_unpatched_session(ddtrace_run_python_code_in_subprocess):
    code = """
import niquests
from ddtrace.contrib.internal.niquests.patch import unpatch
from ddtrace import tracer

unpatch()

resp = niquests.get("http://localhost:8001/status/200")
assert resp.status_code == 200
"""
    out, err, status, _ = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, (out, err)
