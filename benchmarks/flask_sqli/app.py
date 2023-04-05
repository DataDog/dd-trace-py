import sqlite3

from flask import Flask
from flask import request
from flask import Response


app = Flask(__name__)

con = sqlite3.connect(":memory:")
cur = con.cursor()


@app.route("/", methods=['GET'])
def index():
    return Response("OK")


@app.route("/sqli", methods=["POST"])
def sqli():
    try:
        sql = (
            "SELECT * FROM IAST_USER WHERE USERNAME = '"
            + request.form["username"]
            + "' AND PASSWORD = '"
            + request.form["password"]
            + "'"
        )
        cur.execute(sql)
    except:
        pass
    return Response("OK")
