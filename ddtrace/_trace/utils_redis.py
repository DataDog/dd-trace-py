from ddtrace.ext import db
from ddtrace.ext import redis as redisx


def _extract_conn_tags(conn_kwargs):
    try:
        conn_tags = {
            net.TARGET_HOST: conn_kwargs["host"],
            net.TARGET_PORT: conn_kwargs["port"],
            net.SERVER_ADDRESS: conn_kwargs["host"],
            redisx.DB: conn_kwargs.get("db") or 0,
        }
        client_name = conn_kwargs.get("client_name")
        if client_name:
            conn_tags[redisx.CLIENT_NAME] = client_name
        return conn_tags
    except Exception:
        return {}
