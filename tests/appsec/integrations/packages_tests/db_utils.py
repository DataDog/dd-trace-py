import os

import psycopg2
import pymysql


POSTGRES_HOST = os.getenv("TEST_POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("TEST_POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("TEST_POSTGRES_DB", "postgres")
POSTGRES_USER = os.getenv("TEST_POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("TEST_POSTGRES_PASSWORD")

MYSQL_HOST = os.getenv("TEST_MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("TEST_MYSQL_PORT", "3306"))
MYSQL_DB = os.getenv("TEST_MYSQL_DB", "test")
MYSQL_USER = os.getenv("TEST_MYSQL_USER")
MYSQL_PASSWORD = os.getenv("TEST_MYSQL_PASSWORD")


def get_psycopg2_connection():
    params = {
        "host": POSTGRES_HOST,
        "port": POSTGRES_PORT,
        "database": POSTGRES_DB,
        "options": "-c statement_timeout=1000",
    }
    if POSTGRES_USER is not None:
        params["user"] = POSTGRES_USER
    if POSTGRES_PASSWORD is not None:
        params["password"] = POSTGRES_PASSWORD
    connection = psycopg2.connect(**params)
    return connection


def get_pymysql_connection():
    params = {
        "host": MYSQL_HOST,
        "port": MYSQL_PORT,
        "database": MYSQL_DB,
    }
    if MYSQL_USER is not None:
        params["user"] = MYSQL_USER
    if MYSQL_PASSWORD is not None:
        params["password"] = MYSQL_PASSWORD
    connection = pymysql.connect(**params)
    return connection


def close_connection(connection):
    if connection:
        connection.close()
