#!/usr/bin/env python3

from peewee import CharField
from peewee import IntegerField
from peewee import Model
from peewee import SqliteDatabase


db = SqliteDatabase(":sharedmemory:")


class User(Model):
    id = IntegerField(primary_key=True)
    name = CharField()

    class Meta:
        database = db


db.create_tables([User])


def execute_query(param):
    db.execute_sql(param)


def execute_untainted_query(_):
    db.execute_sql("SELECT * FROM User")
