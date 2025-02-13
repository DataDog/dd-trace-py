import json
import os
import pathlib
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from ddtrace.settings.asm import config as asm_config


MODULES_ALWAYS_LOADED = ["ddtrace.appsec", "ddtrace.appsec._capabilities", "ddtrace.appsec._constants"]
MODULE_ASM_ONLY = ["ddtrace.appsec._processor", "ddtrace.appsec._ddwaf"]
MODULE_IAST_ONLY = [
    "ddtrace.appsec._iast",
    "ddtrace.appsec._iast._taint_tracking._native",
    "ddtrace.appsec._iast._stacktrace",
]


@pytest.mark.parametrize("appsec_enabled", ["true", "false"])
@pytest.mark.parametrize("iast_enabled", ["true", None])
@pytest.mark.parametrize("aws_lambda", [None, "any"])
def test_loading(appsec_enabled, iast_enabled, aws_lambda):
    flask_app = pathlib.Path(__file__).parent / "mini.py"
    env = os.environ.copy()
    if appsec_enabled:
        env["DD_APPSEC_ENABLED"] = appsec_enabled
    else:
        env.pop("DD_APPSEC_ENABLED", None)
    if iast_enabled:
        env["DD_IAST_ENABLED"] = iast_enabled
    else:
        env.pop("DD_IAST_ENABLED", None)
    if aws_lambda:
        env["AWS_LAMBDA_FUNCTION_NAME"] = aws_lambda
    else:
        env.pop("AWS_LAMBDA_FUNCTION_NAME", None)

    # Disable debug logging as it creates too large buffer to handle
    env["DD_TRACE_DEBUG"] = "false"

    print(f"\nStarting server {sys.executable} {str(flask_app)}", flush=True)

    process = subprocess.Popen([sys.executable, str(flask_app)], env=env)
    try:
        print("process started", flush=True)
        for i in range(24):
            time.sleep(1)
            try:
                with urlopen("http://localhost:8475", timeout=1) as response:
                    print(f"got a response {response.status}", flush=True)
                    assert response.status == 200
                    payload = response.read().decode()
                    data = json.loads(payload)
                    print("got data", flush=True)

                    assert "appsec" in data
                    # appsec is always enabled
                    for m in MODULES_ALWAYS_LOADED:
                        assert m in data["appsec"], f"{m} not in {data['appsec']}"
                    for m in MODULE_ASM_ONLY:
                        if appsec_enabled == "true" and not aws_lambda:
                            assert m in data["appsec"], f"{m} not in {data['appsec']} data:{data}"
                        else:
                            assert m not in data["appsec"], f"{m} in {data['appsec']} data:{data}"
                    for m in MODULE_IAST_ONLY:
                        if iast_enabled and not aws_lambda and asm_config._iast_supported:
                            assert m in data["appsec"], f"{m} not in {data['appsec']}"
                        else:
                            assert m not in data["appsec"], f"{m} in {data['appsec']}"
                print(f"Test passed {i}", flush=True)
                return
            except HTTPError as e:
                print(f"HTTP error {i} [{e.status}]", flush=True)
                raise AssertionError(e.status, e.read().decode())
            except AssertionError:
                print(f"Test failed {i}", flush=True)
                raise
            except BaseException:
                print(f"Server not started yet {i}", flush=True)
                continue
    finally:
        try:
            urlopen("http://localhost:8475/shutdown", timeout=1)
        except BaseException:
            time.sleep(1)
        process.wait()

    raise AssertionError("Server did not start.")
