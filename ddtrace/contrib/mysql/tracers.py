import logging

import mysql.connector


# deprecated
def get_traced_mysql_connection(*args, **kwargs):
    logging.warn("get_traced_mysql_connection is deprecated")
    return mysql.connector.MySQLConnection
