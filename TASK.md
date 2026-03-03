# Task 007: Migrate ddtrace/contrib/internal/ integration files to use _env

## Campaign Context

You are one of 5 agents working on: **Centralize all environment variable access in dd-trace-py through `ddtrace.internal.settings._env`**.

You are in **Wave 2**. Other tasks in your wave: Task 003 (settings), Task 004 (root/trace/bootstrap), Task 005 (internal non-settings), Task 006 (appsec/llmobs/ext).
Prior waves completed: Wave 1 created `ddtrace/internal/settings/_env.py` with `EnvConfig` class and `environ`/`getenv`/`set_env` exports.

## Your Workspace

- **Worktree:** `/tmp/orchestrator/env-config-refactor/task-007`
- **Branch:** `campaign/env-config-refactor/task-007`
- **Base:** `campaign/env-config-refactor/target` at commit `af2f4360c31b895f3b0b0d58967fe988070da59b`

You are working in an isolated copy of the repository. Do not navigate outside this directory.

## What to Build

Migrate all `os.environ` and `os.getenv` usage in these 47 contrib/internal integration files to use `ddtrace.internal.settings._env`.

**Migration pattern (same for all files):**
1. Remove `import os` if it was only used for environ/getenv. Keep if used for other os functions.
2. Add `from ddtrace.internal.settings import _env` near the top imports.
3. Replace all `os.environ.*` and `os.getenv()` calls with `_env.environ.*` and `_env.getenv()`.

**Common patterns in contrib files:**
- Most integration patch.py files use `os.getenv("DD_<INTEGRATION>_<SETTING>")` for integration-specific config
- Some use `os.environ.get()` with defaults
- A few set env vars: `os.environ["KEY"] = value` → `_env.environ["KEY"] = value`
- `botocore/patch.py` has extensive env var usage for AWS config propagation
- `ray/utils.py` sets traceparent/tracestate in os.environ
- `pytest/` files use env vars for test visibility configuration

**Special cases:**
- `ddtrace/contrib/internal/ray/utils.py` — Sets env vars for trace context propagation. Use `_env.environ[key] = value`.
- `ddtrace/contrib/internal/subprocess/patch.py` — May manipulate os.environ for subprocess env inheritance. Check carefully — if it needs to pass env to subprocess, it may need `_env.environ` as the env dict.
- `ddtrace/contrib/internal/botocore/patch.py` — Heavy env var usage, be thorough.

**Important:**
- Preserve all existing behavior exactly. Mechanical refactoring only.
- This is a large task (47 files) but all changes follow the identical pattern.
- Read each file to find ALL os.environ/os.getenv usage — some files may have multiple call sites.
- If a file has NO os.environ/os.getenv usage, skip it and note it in PROGRESS.md.

## File Scope

**Modify:**
- `ddtrace/contrib/internal/aiobotocore/patch.py`
- `ddtrace/contrib/internal/aiohttp/patch.py`
- `ddtrace/contrib/internal/aiokafka/patch.py`
- `ddtrace/contrib/internal/aredis/patch.py`
- `ddtrace/contrib/internal/asgi/middleware.py`
- `ddtrace/contrib/internal/boto/patch.py`
- `ddtrace/contrib/internal/botocore/patch.py`
- `ddtrace/contrib/internal/bottle/patch.py`
- `ddtrace/contrib/internal/celery/patch.py`
- `ddtrace/contrib/internal/cherrypy/patch.py`
- `ddtrace/contrib/internal/django/patch.py`
- `ddtrace/contrib/internal/falcon/patch.py`
- `ddtrace/contrib/internal/fastapi/patch.py`
- `ddtrace/contrib/internal/graphql/patch.py`
- `ddtrace/contrib/internal/httplib/patch.py`
- `ddtrace/contrib/internal/httpx/patch.py`
- `ddtrace/contrib/internal/jinja2/patch.py`
- `ddtrace/contrib/internal/kafka/patch.py`
- `ddtrace/contrib/internal/kombu/patch.py`
- `ddtrace/contrib/internal/mariadb/patch.py`
- `ddtrace/contrib/internal/mcp/patch.py`
- `ddtrace/contrib/internal/molten/patch.py`
- `ddtrace/contrib/internal/mysql/patch.py`
- `ddtrace/contrib/internal/mysqldb/patch.py`
- `ddtrace/contrib/internal/psycopg/patch.py`
- `ddtrace/contrib/internal/pymemcache/client.py`
- `ddtrace/contrib/internal/pymysql/patch.py`
- `ddtrace/contrib/internal/pyodbc/patch.py`
- `ddtrace/contrib/internal/pytest/_plugin_v2.py`
- `ddtrace/contrib/internal/pytest/_report_links.py`
- `ddtrace/contrib/internal/pytest/_xdist.py`
- `ddtrace/contrib/internal/pytest/plugin.py`
- `ddtrace/contrib/internal/ray/patch.py`
- `ddtrace/contrib/internal/ray/utils.py`
- `ddtrace/contrib/internal/redis/patch.py`
- `ddtrace/contrib/internal/rediscluster/patch.py`
- `ddtrace/contrib/internal/requests/patch.py`
- `ddtrace/contrib/internal/selenium/patch.py`
- `ddtrace/contrib/internal/snowflake/patch.py`
- `ddtrace/contrib/internal/sqlite3/patch.py`
- `ddtrace/contrib/internal/starlette/patch.py`
- `ddtrace/contrib/internal/subprocess/patch.py`
- `ddtrace/contrib/internal/tornado/patch.py`
- `ddtrace/contrib/internal/unittest/patch.py`
- `ddtrace/contrib/internal/urllib3/patch.py`
- `ddtrace/contrib/internal/valkey/patch.py`
- `ddtrace/contrib/internal/yaaredis/patch.py`

**Read (do not modify):**
- `ddtrace/internal/settings/_env.py` — The new env wrapper to use
- `ddtrace/contrib/internal/django/patch.py` — Reference for pattern (read before modifying)
- `ddtrace/contrib/internal/botocore/patch.py` — Reference for heavy usage (read before modifying)

### Deviation Rules

1. **Auto-fix trivial blockers:** Fix and note in PROGRESS.md.
2. **Stay in scope:** Do NOT modify files outside the scope above.
3. **Stop when stuck:** After 3 attempts, write blocker to PROGRESS.md, set Status to `blocked`, stop.
4. **No scope creep:** Note opportunities in PROGRESS.md but do NOT implement.

## Success Criteria

```
python -c "from ddtrace.contrib.internal.django.patch import patch; from ddtrace.contrib.internal.redis.patch import patch as rpatch; from ddtrace.contrib.internal.botocore.patch import patch as bpatch; print('OK')"
```

**Do NOT run these commands yourself.**

## Constraints

- **Model:** sonnet
- **Max turns:** 40

### Context Budget

Stay under **50% context window usage**. This is a very large task (47 files). Work efficiently:
1. First, grep to find which files actually have os.environ/os.getenv usage.
2. Skip files that don't have any usage — note them in PROGRESS.md as "no changes needed".
3. For files that do have usage, read, modify, move on. Don't re-read.
4. If you're running low on context, commit partial progress, update PROGRESS.md with what's done and what remains, set Status to `done`, and stop.

## Progress Tracking

Update `PROGRESS.md` in the root of your worktree as you work:

```markdown
## Status: {in_progress | done | blocked}

## Completed
- [x] {thing you finished}

## In Progress
- [ ] {thing you're working on}

## Blockers
{any issues preventing progress}

## Files Modified
- {list of actual files you created or changed}

## Skipped (no os.environ/os.getenv usage)
- {list of files that didn't need changes}
```

When you are finished, set Status to `done` and stop.
