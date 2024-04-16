import ddtrace
ddtrace.patch_all()
import asyncio
import aiopg
import datetime
import random

dsn = 'dbname=postgres user=postgres password=postgres host=127.0.0.1'

# this function does not result in a traced connection / cursor (aiopg.connect is not patched)

async def go():
    breakpoint()
    conn = await aiopg.connection.connect(dsn)
    async with conn.cursor() as cur:
        # Create database if not exists
        await cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'test_db'")
        exists = await cur.fetchone()
        if not exists:
            await cur.execute('CREATE DATABASE test_db')
        # await conn.commit()

        # Use the new database
        # Not applicable in PostgreSQL as we can't switch databases in a session

        # Create table if not exists
        await cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id serial PRIMARY KEY,
                name varchar(50),
                email varchar(50)
            )
        ''')

        # Insert some data
        random_number = random.randint(1, 1000000)  # generate a random number
        current_time = datetime.datetime.now().strftime("%Y%m%d%H%M%S")  # get current time

        email = f"johndoe{random_number}{current_time}@example.com"  # append random number and time to email

        await cur.execute(f'''
            INSERT INTO users (name, email) VALUES 
            ('John Doe', '{email}')
        ''')
        # await conn.commit()

        # Fetch and print the data
        await cur.execute('SELECT * FROM users')
        print('Users:')
        async for row in cur:
            print(row)


# this function does result in a traced connection, but we get failures when trying to get the cursor


async def go_patched():
    breakpoint()
    conn = await aiopg.connection.connect(dsn)

    async with conn.cursor() as cur:
        # Create database if not exists
        await cur.execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = 'test_db'")
        exists = await cur.fetchone()
        if not exists:
            await cur.execute('CREATE DATABASE test_db')

        # Use the new database
        # Not applicable in PostgreSQL as we can't switch databases in a session

        # Create table if not exists
        await cur.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id serial PRIMARY KEY,
                name varchar(50),
                email varchar(50)
            )
        ''')

        # Insert some data
        random_number = random.randint(1, 1000000)  # generate a random number
        current_time = datetime.datetime.now().strftime("%Y%m%d%H%M%S")  # get current time

        email = f"johndoe{random_number}{current_time}@example.com"  # append random number and time to email

        await cur.execute(f'''
            INSERT INTO users (name, email) VALUES 
            ('John Doe', '{email}')
        ''')

        # Fetch and print the data
        await cur.execute('SELECT * FROM users')
        print('Users:')
        async for row in cur:
            print(row)


loop = asyncio.get_event_loop()
loop.run_until_complete(go())
loop.run_until_complete(go_patched())