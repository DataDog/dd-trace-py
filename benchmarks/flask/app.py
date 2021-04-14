import os


if os.getenv("DDTRACE_PATCH_ALL"):
    import ddtrace

    ddtrace.patch_all()

from flask import Flask


app = Flask(__name__)


@app.route("/")
def index():
    return "Hello, World"


if __name__ == "__main__":
    test_client = app.test_client()

    for _ in range(100):
        test_client.get("/")
