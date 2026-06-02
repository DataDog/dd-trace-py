# Description: This script is used to create a sqlite3 database
# with the same schema as the one needed for the Django test app.
import sqlite3


cursor = sqlite3.connect("tests/appsec/contrib_appsec/db.sqlite3")

try:
    cursor.execute("drop table app_customuser")
    cursor.execute("drop table django_session")
except Exception:
    pass


cursor.execute(
    "create table app_customuser (id text primary key, username text, first_name text, last_name text,"
    " email text, password text, last_login text, is_superuser bool, is_staff bool, is_active bool, date_joined text)"
)

cursor.execute("create table django_session (session_key text, session_data text, expire_date text)")
