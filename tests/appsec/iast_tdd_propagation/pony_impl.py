#!/usr/bin/env python3

from pony import orm


db = orm.Database()


class User(db.Entity):
    name = orm.Required(str)


db.bind(provider="sqlite", filename=":sharedmemory:", create_db=True)
db.generate_mapping(create_tables=True)


def execute_query(param):
    with orm.db_session:
        return User.select_by_sql(param)


def execute_untainted_query(_):
    with orm.db_session:
        return User.select_by_sql("SELECT * from User")
