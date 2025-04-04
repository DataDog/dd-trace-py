import os

import psycopg2


POSTGRES_HOST = os.getenv("TEST_POSTGRES_HOST", "127.0.0.1")


def get_connection():
    connection = psycopg2.connect(
        user="postgres", password="postgres", host=POSTGRES_HOST, port="5432", database="postgres"
    )
    return connection


def close_connection(connection):
    if connection:
        connection.close()
