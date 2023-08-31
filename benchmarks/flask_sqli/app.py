import sqlite3

from flask import Flask
from flask import Response
from flask import request


app = Flask(__name__)

con = sqlite3.connect(":memory:", check_same_thread=False)
cur = con.cursor()


@app.route("/", methods=["GET"])
def index():
    return Response("OK")


@app.route("/sqli", methods=["POST"])
def sqli():
    sql = "SELECT 1 FROM sqlite_master WHERE name = '" + request.form["username"] + "'"
    cur.execute(sql)
    return Response("OK")
