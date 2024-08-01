import os


def test_flask_openapi3_instrumentation(ddtrace_run_python_code_in_subprocess):
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
    out, err, status, pid = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, (out, err)
    assert err == b"", (out, err)
