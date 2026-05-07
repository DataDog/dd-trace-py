#!/usr/bin/env python3

import asyncio

from tortoise import Tortoise
from tortoise import connections


loop = asyncio.get_event_loop()


async def init():
    await Tortoise.init(db_url="sqlite://:memory:", modules={"models": ["tortoise_models"]})
    # Generate the schema
    await Tortoise.generate_schemas()


def execute_query(param):
    # Need to get a connection. Unless explicitly specified, the name should be 'default'
    conn = connections.get("default")

    # Now we can execute queries in the normal autocommit mode
    loop.run_until_complete(conn.execute_query(param))


def execute_untainted_query(_):
    # Need to get a connection. Unless explicitly specified, the name should be 'default'
    conn = connections.get("default")

    # Now we can execute queries in the normal autocommit mode
    loop.run_until_complete(conn.execute_query("SELECT * FROM User"))


loop.run_until_complete(init())
