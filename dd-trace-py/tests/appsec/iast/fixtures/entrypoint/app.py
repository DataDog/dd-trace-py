# Normal flask app. No IAST propagation
from flask import Flask


def add_test(a, b):
    return a + b


app = Flask(__name__)

if __name__ == "__main__":
    app.run()
