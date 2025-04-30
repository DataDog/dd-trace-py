import os

import psycopg2
import pymysql


POSTGRES_HOST = os.getenv("TEST_POSTGRES_HOST", "127.0.0.1")
MYSQL_HOST = os.getenv("TEST_MYSQL_HOST", "127.0.0.1")


def get_psycopg2_connection():
    connection = psycopg2.connect(
        user="postgres", password="postgres", host=POSTGRES_HOST, port="5432", database="postgres"
    )
    return connection


def get_pymysql_connection():
    connection = pymysql.connect(user="test", password="test", host=MYSQL_HOST, port=3306, database="test")
    return connection


def close_connection(connection):
    if connection:
        connection.close()
