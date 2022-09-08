#!/usr/bin/env bash
set -ex

# cat /var/log/postgresql/postgresql-*.log

# Replace peer auth with password auth
sed -i 's/peer/trust/g' /etc/postgresql/14/main/pg_hba.conf
service postgresql restart

psql -U postgres -c "ALTER USER postgres PASSWORD 'password';"
sed -i 's/trust/md5/g' /etc/postgresql/14/main/pg_hba.conf
service postgresql restart