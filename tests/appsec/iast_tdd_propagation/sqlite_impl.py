#!/usr/bin/env python3

import sqlite3


conn = sqlite3.connect(":memory:", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("CREATE TABLE User (id INTEGER PRIMARY KEY, name TEXT)")


def execute_query(param):
    cursor.execute(param)


def execute_untainted_query(_):
    cursor.execute("SELECT 1")
