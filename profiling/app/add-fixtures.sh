#!/usr/bin/env bash
set -ex

export PYENV_ROOT=/pyenv
export PATH="$PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH"
export VENV_DDTRACE=/app/.venv_ddtrace/
export PG_RETRIES=5


export DATABASE_URL="postgresql://postgres:password@localhost"

service postgresql restart

eval "$(pyenv init -)"

source ${VENV_DDTRACE}/bin/activate

# wait for postgres to start up
until pg_isready -h localhost > /dev/null 2>&1 || [ $PG_RETRIES -eq 0 ]; do
  echo "Waiting for postgres server, $((PG_RETRIES--)) remaining attempts..."
  sleep 1
done

# Add fixtures to Postgres

PGPASSWORD=password psql -U postgres -d postgres -c "INSERT INTO users (id, username, email, created_at, updated_at) VALUES (1, 'fresh-tuna-owner', 'buy-fresh-tuna@fishmarket.com', current_timestamp, current_timestamp)";
PGPASSWORD=password psql -U postgres -d postgres -c "INSERT INTO userprofile (id, user_id) VALUES (1, 1)";
for ((n = 1; n <= 100; n++)); do
  PGPASSWORD=password psql -U postgres -d postgres -c "INSERT INTO article (slug, title, description, body, author_id, \"createdAt\", \"updatedAt\") VALUES \
  ('fresh-tuna-1-$n', 'Fresh Tuna Title $n', 'Fresh Tuna Desc $n', 'Fresh Tuna Body $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-2-$n', 'Fresh Tuna Title 2-$n', 'Fresh Tuna Desc 2 $n', 'Fresh Tuna Body 2 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-3-$n', 'Fresh Tuna Title 3-$n', 'Fresh Tuna Desc 3 $n', 'Fresh Tuna Body 3 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-4-$n', 'Fresh Tuna Title 4-$n', 'Fresh Tuna Desc 4 $n', 'Fresh Tuna Body 4 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-5-$n', 'Fresh Tuna Title 5-$n', 'Fresh Tuna Desc 5 $n', 'Fresh Tuna Body 5 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-6-$n', 'Fresh Tuna Title 6-$n', 'Fresh Tuna Desc 6 $n', 'Fresh Tuna Body 6 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-7-$n', 'Fresh Tuna Title 7-$n', 'Fresh Tuna Desc 7 $n', 'Fresh Tuna Body 7 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-8-$n', 'Fresh Tuna Title 8-$n', 'Fresh Tuna Desc 8 $n', 'Fresh Tuna Body 8 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-9-$n', 'Fresh Tuna Title 9-$n', 'Fresh Tuna Desc 9 $n', 'Fresh Tuna Body 9 $n', 1, current_timestamp, current_timestamp),\
  ('fresh-tuna-0-$n', 'Fresh Tuna Title 0-$n', 'Fresh Tuna Desc 0 $n', 'Fresh Tuna Body 0 $n', 1, current_timestamp, current_timestamp)\
  ";
done

deactivate