import logging

import mysql.connector

logger = logging.getLogger(__name__)

# deprecated
def get_traced_mysql_connection(*args, **kwargs):
    logger.warn("get_traced_mysql_connection is deprecated")
    return mysql.connector.MySQLConnection
