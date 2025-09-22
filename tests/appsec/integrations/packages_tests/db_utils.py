import os

import psycopg2
import pymysql


POSTGRES_HOST = os.getenv("TEST_POSTGRES_HOST", "127.0.0.1")
MYSQL_HOST = os.getenv("TEST_MYSQL_HOST", "127.0.0.1")


def get_psycopg2_connection():
    user = os.getenv("TEST_POSTGRES_USER")
    password = os.getenv("TEST_POSTGRES_PASSWORD")
    port = int(os.getenv("TEST_POSTGRES_PORT", "5432"))
    database = os.getenv("TEST_POSTGRES_DB", "postgres")

    kwargs = {
        "host": POSTGRES_HOST,
        "port": port,
        "database": database,
        "options": "-c statement_timeout=1000",
    }
    if user:
        kwargs["user"] = user
    if password:
        kwargs["password"] = password

    connection = psycopg2.connect(**kwargs)
    return connection


def get_pymysql_connection():
    user = os.getenv("TEST_MYSQL_USER")
    password = os.getenv("TEST_MYSQL_PASSWORD")
    port = int(os.getenv("TEST_MYSQL_PORT", "3306"))
    database = os.getenv("TEST_MYSQL_DB", "test")

    kwargs = {"host": MYSQL_HOST, "port": port, "database": database}
    if user:
        kwargs["user"] = user
    if password:
        kwargs["password"] = password

    connection = pymysql.connect(**kwargs)
    return connection


def close_connection(connection):
    if connection:
        connection.close()
