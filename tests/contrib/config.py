"""
testing config.
"""

import os


# an env var that will be present during circle ci builds
CIRCLECI_ENVVAR="CIRCLE_BUILD_NUM"

PG_CONFIG = {
    'host' : 'localhost',
    'port' : 5432,
    'user' : 'dog',
    'password' : 'dog',
    'dbname' : 'dogdata',
}

CIRCLECI_PG_CONFIG = {
    'host' : 'localhost',
    'port' : 5432,
    'user' : 'test',
    'password' : 'test',
    'dbname' : 'test',
}



def get_pg_config():
    return CIRCLECI_PG_CONFIG if (CIRCLECI_ENVVAR in os.environ) else PG_CONFIG

