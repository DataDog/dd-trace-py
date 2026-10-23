import os


def test_flask_openapi3_instrumentation(python_process):
    code = """
from flask_openapi3 import Info, Tag
from flask_openapi3 import OpenAPI

info = Info(title="my API", version="1.0.0")
app = OpenAPI(__name__, info=info)

@app.get("/")
def hello_world():
    return 'Hello, World!'
"""
    env = os.environ.copy()
    _process_result = python_process(code, env=env, ddtrace_run=True)
    out = _process_result.stdout
    err = _process_result.stderr
    status = _process_result.returncode
    assert status == 0, (out, err)
    assert err == b"", (out, err)
