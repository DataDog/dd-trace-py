## Status: done

## Completed
- [x] Grepped all 47 target files — all had os.environ/os.getenv usage
- [x] Identified 4 files needing `import os` retained (also use os.path/os.sep/etc): ray/patch.py, ray/utils.py, subprocess/patch.py, unittest/patch.py
- [x] Migrated all 47 files: replaced `import os` → `from ddtrace.internal.settings import _env`, replaced `os.getenv(` → `_env.getenv(`, replaced `os.environ` → `_env.environ`
- [x] Verified 0 remaining os.environ/os.getenv references in target files
- [x] Key files verified: django/patch.py, botocore/patch.py, subprocess/patch.py, ray/utils.py

## Blockers
None

## Files Modified
- ddtrace/contrib/internal/aiobotocore/patch.py
- ddtrace/contrib/internal/aiohttp/patch.py
- ddtrace/contrib/internal/aiokafka/patch.py
- ddtrace/contrib/internal/aredis/patch.py
- ddtrace/contrib/internal/asgi/middleware.py
- ddtrace/contrib/internal/boto/patch.py
- ddtrace/contrib/internal/botocore/patch.py
- ddtrace/contrib/internal/bottle/patch.py
- ddtrace/contrib/internal/celery/patch.py
- ddtrace/contrib/internal/cherrypy/patch.py
- ddtrace/contrib/internal/django/patch.py
- ddtrace/contrib/internal/falcon/patch.py
- ddtrace/contrib/internal/fastapi/patch.py
- ddtrace/contrib/internal/graphql/patch.py
- ddtrace/contrib/internal/httplib/patch.py
- ddtrace/contrib/internal/httpx/patch.py
- ddtrace/contrib/internal/jinja2/patch.py
- ddtrace/contrib/internal/kafka/patch.py
- ddtrace/contrib/internal/kombu/patch.py
- ddtrace/contrib/internal/mariadb/patch.py
- ddtrace/contrib/internal/mcp/patch.py
- ddtrace/contrib/internal/molten/patch.py
- ddtrace/contrib/internal/mysql/patch.py
- ddtrace/contrib/internal/mysqldb/patch.py
- ddtrace/contrib/internal/psycopg/patch.py
- ddtrace/contrib/internal/pymemcache/client.py
- ddtrace/contrib/internal/pymysql/patch.py
- ddtrace/contrib/internal/pyodbc/patch.py
- ddtrace/contrib/internal/pytest/_plugin_v2.py
- ddtrace/contrib/internal/pytest/_report_links.py
- ddtrace/contrib/internal/pytest/_xdist.py
- ddtrace/contrib/internal/pytest/plugin.py
- ddtrace/contrib/internal/ray/patch.py
- ddtrace/contrib/internal/ray/utils.py
- ddtrace/contrib/internal/redis/patch.py
- ddtrace/contrib/internal/rediscluster/patch.py
- ddtrace/contrib/internal/requests/patch.py
- ddtrace/contrib/internal/selenium/patch.py
- ddtrace/contrib/internal/snowflake/patch.py
- ddtrace/contrib/internal/sqlite3/patch.py
- ddtrace/contrib/internal/starlette/patch.py
- ddtrace/contrib/internal/subprocess/patch.py
- ddtrace/contrib/internal/tornado/patch.py
- ddtrace/contrib/internal/unittest/patch.py
- ddtrace/contrib/internal/urllib3/patch.py
- ddtrace/contrib/internal/valkey/patch.py
- ddtrace/contrib/internal/yaaredis/patch.py

## Skipped (no os.environ/os.getenv usage)
None — all 47 files had usage and were migrated.

## Notes
- Files that use os for non-environ purposes (ray/patch.py, ray/utils.py, subprocess/patch.py, unittest/patch.py) had `import os` retained and `from ddtrace.internal.settings import _env` added after it.
- All other 43 files had `import os` replaced entirely with `from ddtrace.internal.settings import _env`.
- subprocess/patch.py has inline `import os  # nosec` inside function bodies (not at module level) — these were left untouched as they are in-scope imports for subprocess execution, not for env access.
