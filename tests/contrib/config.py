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

if os.getenv('CIRCLECI'):
    PG_CONFIG = CIRCLECI_PG_CONFIG
