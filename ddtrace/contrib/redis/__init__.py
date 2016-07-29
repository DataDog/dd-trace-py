"""
The Redis integration works by creating patched redis connection classes which
will trace network calls. For basic usage:

    import redis
    from ddtrace import tracer
    from ddtrace.contrib.redis import get_traced_redis
    from ddtrace.contrib.redis import get_traced_redis_from

    # Trace the redis.StrictRedis class ...
    TracedStrictRedis = get_traced_redis(tracer, service="my-redis-cache")
    conn = TracedStrictRedis(host="localhost", port=6379)
    conn.set("key", "value")

    # Trace the redis.Redis class
    TracedRedis = get_traced_redis_from(tracer, redis.Redis, service="my-redis-cache")
    conn = TracedRedis(host="localhost", port=6379)
    conn.set("key", "value")
"""


To trace a particular redis class, do the following:

    app = Flask(...)

    traced_app = TraceMiddleware(app, tracer, service="my-flask-app")

    @app.route("/")
    def home():
        return "hello world"
"""


from ..util import require_modules

required_modules = ['redis', 'redis.client']

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .tracers import get_traced_redis, get_traced_redis_from

        __all__ = ['get_traced_redis', 'get_traced_redis_from']
