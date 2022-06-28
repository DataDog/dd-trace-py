import json
import os.path
import itertools

import contextlib
import httpretty

from glob import glob

import pytest

from ddtrace.remoteconfig._client import Client


ROOT_DIR = os.path.dirname(os.path.realpath(__file__))


def assert_is_subset(subset, superset, path=[]):
    if isinstance(subset, dict):
        assert all(
            key in superset and assert_is_subset(val, superset[key], path=path + [key]) for key, val in subset.items()
        )
        return True
    if isinstance(subset, list):
        assert all(
            any(assert_is_subset(subitem, superitem, path=path + [i]) for i, superitem in enumerate(superset))
            for subitem in subset
        )
        return True
    return subset == superset


@pytest.fixture
def mocked_remote_config_agent():
    httpretty.enable()

    @contextlib.contextmanager
    def inner(scenario):

        requests = sorted(glob(os.path.join(ROOT_DIR, "payloads/{}-req-[0-9].json".format(scenario))))
        responses = sorted(glob(os.path.join(ROOT_DIR, "payloads/{}-res-[0-9].json".format(scenario))))

        assert len(requests), "scenario was not found"
        assert len(requests) == len(responses), "must have the same number of requests than responses"

        expected_data = []

        def assert_request():
            last_request = httpretty.last_request()
            assert last_request is not None, "no request received"
            data = json.loads(last_request.body)
            assert_is_subset(expected_data[-1], data)

        counter = itertools.count()

        def cb(request, uri, response_headers):
            count = next(counter)
            if count >= len(requests):
                return [500, response_headers, b"too many requests"]
            with open(requests[count], "r") as f:
                expected_data.append(json.loads(f.read()))
            with open(responses[count], "r") as f:
                response = f.read()
            return [200, response_headers, response]

        url = "http://localhost:8888/"
        httpretty.register_uri(
            httpretty.POST, url + "v0.7/config", body=cb,
        )
        try:
            yield url, len(requests), assert_request
        finally:
            httpretty.reset()

    try:
        yield inner
    finally:
        httpretty.disable()


@pytest.mark.parametrize("scenario", ["base"])
def test_functional(mocked_remote_config_agent, scenario):
    with mocked_remote_config_agent(scenario) as (url, expected_request_count, assert_request):
        c = Client(agent_url=url)
        c.register_product("ASM", lambda *x: None)
        for _ in range(expected_request_count):
            c.request()
            assert_request()
