from flask import Flask


app = Flask(__name__)


@app.route("/")
def entry_point():
    from ddtrace import tracer

    span = tracer.current_span()
    if span:
        for _ in range(100):
            span.set_tag("asd", "a" * 100)
    return "Hello World!"
