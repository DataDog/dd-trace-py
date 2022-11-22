from os import environ
import random
import string

from flask import Flask


app = Flask(__name__)


@app.route("/")
def entry_point():
    if environ.get("DUPLICATE_TAGS_SCENARIO", None):
        from ddtrace import tracer

        span = tracer.current_span()
        if span:
            for _ in range(100):
                span.set_tag(_, "a" * 100)
    elif environ.get("UNIQUE_TAGS_SCENARIO", None):
        from ddtrace import tracer

        span = tracer.current_span()
        if span:
            for num in range(100):
                span.set_tag(str(num), "".join(random.choices(string.ascii_letters, k=100)))

    return "Hello World!"
