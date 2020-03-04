import os
import subprocess

import requests

import pytest

from . import conftest


@pytest.mark.parametrize(
    "proxy_port", range(conftest.BACKEND_PORT + 1, conftest.BACKEND_PORT + 6),
)
def test_profile_sent(proxy_port, toxiproxy, backend, monkeypatch):
    url = "http://localhost:%d/" % proxy_port
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    monkeypatch.setenv("DD_PROFILING_API_KEY", "f00b4r")
    monkeypatch.setenv("DD_PROFILING_API_URL", url)
    output = subprocess.check_output(
        ["python", os.path.join(os.path.dirname(__file__), "solver.py")], stderr=subprocess.STDOUT
    )
    # 8002 is close which output errors since it does not accept data
    if proxy_port == 8004:
        assert output.startswith(b"ERROR:ddtrace.profiling.scheduler:Unable to export")
    else:
        assert output == b"Solved!\n"
        profiles = requests.get("http://localhost:%d" % backend).json()
        assert len(profiles) >= 1
        for profile in profiles:
            assert profile["format"] == ["pprof"]
            assert profile["type"] == ["cpu+alloc+exceptions"]
            assert "language:python" in profile["tags[]"]
