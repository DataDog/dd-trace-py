import hashlib
import random

from flask import Flask


import ddtrace.auto  # noqa: F401  # isort: skip


app = Flask(__name__)


@app.route("/")
def index():
    rand_numbers = [random.random() for _ in range(20)]
    m = hashlib.md5()
    m.update(b"Insecure hash")
    rand_numbers.append(m.digest())
    return "OK", 200
