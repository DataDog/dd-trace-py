from flask import Flask
from consumer import add


app = Flask(__name__)


@app.route("/")
def hello():
    result = add.apply_async((4, 4))
    value = result.get()
    return str(value)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
