#! /bin/sh
# list here how you can wait a service to be up and running

# import the .env file preserving already set variables
CURRENT_ENV=$(env | grep "TEST_*")
export $(cat .env)
export $CURRENT_ENV

echo "Waiting for backing services..."

# postgresql
until PGPASSWORD=$TEST_POSTGRES_PASSWORD PGUSER=$TEST_POSTGRES_USER PGDATABASE=$TEST_POSTGRES_DB psql -h localhost -p $TEST_POSTGRES_PORT -c "select 1" &> /dev/null ; do sleep 0.2 ; done
# cassandra
until nc -z localhost $TEST_CASSANDRA_PORT &> /dev/null ; do sleep 0.2 ; done

# confirm
echo "All backing services up and running!"
