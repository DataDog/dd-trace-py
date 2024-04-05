from flask import Flask, render_template

from api import ApiClient

app = Flask(__name__)

BACKEND_HOST = '127.0.0.1'
BACKEND_PORT = '6000'

app.config["api"] = ApiClient(f"{BACKEND_HOST}:{BACKEND_PORT}")


@app.route("/")
def home():
    api = app.config["api"]
    # return """
        # -api.sayHello: %s\n
        # -api.getAll(length=10): %s\n
        # -api.getStream(length=5): %s\n
        # -api.getMap(1, "one"): %s\n
        # -api.GetMsgMap(1, "one"); %s\n
        # """ % (
            # api.sayHello("Pepito"),  # intercept_unary_unary + intercept_client_call
            # api.getAll(length=10),  # intercept_unary_unary + intercept_client_call
            # list(api.getStream(length=5)),  # intercept_unary_stream + intercept_client_call
            # api.getMap({1: "fooone"}),
            # api.getMsgMap({1: "foo"}),
    # )

    # ret = api.sayHello("Pepito")
    # return "-api.sayHello: %s" % ret

    # ret = api.getMap({1: "fooone"})
    # return "-api.getMap: %s" % ret

    ret = api.getMsgMap({1: "fooone"})
    return "-api.getMsgMap: %s" % ret



# Message class: .site-packages/google/protobuf/message.py
# Response: UnaryOutcome object. Has a _response member. Can have:
# - message:
# - items{}:

# Response: _MultiThreadedRendezvouz object
