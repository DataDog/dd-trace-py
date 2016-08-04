"""
testing config.
"""

import os

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

if 'CIRCLECI' in os.environ:
    PG_CONFIG = CIRCLECI_PG_CONFIG

def get_pg_config():
    print os.environ
    return CIRCLECI_PG_CONFIG if ('CIRCLECI' in os.environ) else PG_CONFIG

