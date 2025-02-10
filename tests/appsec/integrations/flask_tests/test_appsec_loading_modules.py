import json
import os
import pathlib
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.error import URLError
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

    # Disable debug logging as it creates too large buffer to handle
    env["DD_TRACE_DEBUG"] = "false"

    print(f"\nStarting server {sys.executable} {str(flask_app)}", flush=True)

    process = subprocess.Popen(
        [sys.executable, str(flask_app)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )

    print("process started", flush=True)
    for i in range(12):
        time.sleep(1)
        try:
            with urlopen("http://localhost:8475", timeout=1) as response:
                print(f"got a response {response.status}", flush=True)
                assert response.status == 200
                payload = response.read().decode()
                data = json.loads(payload)
                print("got data", flush=True)
                if appsec_enabled == "true" and not aws_lambda:
                    for k, v in data["env"].items():
                        print(f"ENV {k}={v}", flush=True)
                    for k, v in data["asm_config"].items():
                        print(f"CONFIG {k}->{v}", flush=True)

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
            process.terminate()
            if "win" not in sys.platform:
                _, _ = process.communicate()
            process.wait()
            return
        except HTTPError as e:
            print(f"HTTP error {i}", flush=True)
            process.terminate()
            if "win" not in sys.platform:
                out, err = process.communicate()
            else:
                out, err = process.stdout.read(), process.stderr.read()
            process.wait()
            raise AssertionError(e.read().decode(), err, out)
        except (URLError, TimeoutError):
            print(f"Server not started yet {i}", flush=True)
            continue
        except BaseException:
            print(f"Test failed {i}", flush=True)
            process.terminate()
            if "win" not in sys.platform:
                out, err = process.communicate()
            else:
                out, err = process.stdout.read(), process.stderr.read()
            process.wait()
            print(f"\nSTDERR {err}", flush=True)
            print(f"\nSTDOUT {out}", flush=True)
            raise
    process.terminate()
    if "win" not in sys.platform:
        out, err = process.communicate()
    else:
        out, err = process.stdout.read(), process.stderr.read()
    process.wait()
    raise AssertionError(f"Server did not start.\nSTDERR:{err}\nSTDOUT:{out}")
