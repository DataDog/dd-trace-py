#!/usr/bin/env python3

from sqliteframe import Database
from sqliteframe import Integer
from sqliteframe import String
from sqliteframe import table


db = Database("sqlite:///:memory:")


@table(db)
class User:
    id = Integer(primary_key=True)
    name = String()


def execute_query(param):
    with db.connection():
        return db.execute(param)


def execute_untainted_query(_):
    with db.connection():
        return db.execute("SELECT * FROM User")
