import ddtrace.auto  # noqa: F401


"""do not move this import"""

import os  # noqa: E402
import sys  # noqa: E402

from flask import Flask  # noqa: E402
import requests  # noqa: E402 F401

from ddtrace.settings.asm import config as asm_config  # noqa: E402
from ddtrace.version import get_version  # noqa: E402


app = Flask(__name__)


@app.route("/")
def hello_world():
    res = []
    for m in sys.modules:
        if m.startswith("ddtrace.appsec"):
            res.append(m)
    return {
        "appsec": list(sorted(res)),
        "asm_config": {
            k: getattr(asm_config, k) for k in dir(asm_config) if isinstance(getattr(asm_config, k), (int, bool, float))
        },
        "aws": "AWS_LAMBDA_FUNCTION_NAME" in os.environ,
        "version": get_version(),
    }


if __name__ == "__main__":
    app.run(debug=True, port=8475)
