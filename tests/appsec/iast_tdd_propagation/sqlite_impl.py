#!/usr/bin/env python3

import sqlite3


conn = sqlite3.connect(":memory:", check_same_thread=False)
cursor = conn.cursor()


def execute_query(param):
    cursor.execute(param)
