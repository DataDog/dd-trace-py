import json
import os
import pathlib
import subprocess
import time
from urllib.error import HTTPError
from urllib.error import URLError
from urllib.request import urlopen

import pytest


MODULES_ALWAYS_LOADED = ["ddtrace.appsec", "ddtrace.appsec._capabilities", "ddtrace.appsec._constants"]
MODULE_ASM_ONLY = ["ddtrace.appsec._processor", "ddtrace.appsec._ddwaf"]
MODULE_IAST_ONLY = [
    "ddtrace.appsec._iast",
    "ddtrace.appsec._iast._taint_tracking._native",
    "ddtrace.appsec._iast._stacktrace",
]


@pytest.mark.parametrize("appsec_enabled", ["true", "false"])
@pytest.mark.parametrize("iast_enabled", ["true", None])
@pytest.mark.parametrize("aws_lambda", ["any", None])
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

    process = subprocess.Popen(
        ["python", str(flask_app)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    for i in range(16):
        time.sleep(1)
        try:
            with urlopen("http://localhost:8475") as response:
                assert response.status == 200
                payload = response.read().decode()
                data = json.loads(payload)
                assert "appsec" in data
                # appsec is always enabled
                for m in MODULES_ALWAYS_LOADED:
                    assert m in data["appsec"], f"{m} not in {data['appsec']}"
                for m in MODULE_ASM_ONLY:
                    if appsec_enabled == "true" and not aws_lambda:
                        assert m in data["appsec"], f"{m} not in {data['appsec']}"
                    else:
                        assert m not in data["appsec"], f"{m} in {data['appsec']}"
                for m in MODULE_IAST_ONLY:
                    if iast_enabled and not aws_lambda:
                        assert m in data["appsec"], f"{m} not in {data['appsec']}"
                    else:
                        assert m not in data["appsec"], f"{m} in {data['appsec']}"
            process.terminate()
            process.wait()
            break
        except HTTPError as e:
            process.terminate()
            process.wait()
            raise AssertionError(e.read().decode())
        except URLError:
            continue
        except AssertionError:
            process.terminate()
            process.wait()
            raise
    else:
        process.terminate()
        process.wait()
        raise AssertionError("Server did not start")
