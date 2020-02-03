import os

ALLOWED_HOSTS = [
    "testserver",
]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "unique-snowflake",},
    "redis": {"BACKEND": "django_redis.cache.RedisCache", "LOCATION": "redis://127.0.0.1:6379/1",},
    "pylibmc": {"BACKEND": "django.core.cache.backends.memcached.PyLibMCCache", "LOCATION": "127.0.0.1:11211",},
    "python_memcached": {
        "BACKEND": "django.core.cache.backends.memcached.MemcachedCache",
        "LOCATION": "127.0.0.1:11211",
    },
    "django_pylibmc": {
        "BACKEND": "django_pylibmc.memcached.PyLibMCCache",
        "LOCATION": "127.0.0.1:11211",
        "BINARY": True,
        "OPTIONS": {"tcp_nodelay": True, "ketama": True},
    },
}

SITE_ID = 1
SECRET_KEY = "not_very_secret_in_tests"
USE_I18N = True
USE_L10N = True
STATIC_URL = "/static/"
ROOT_URLCONF = "tests.contrib.django.django1_app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates"),],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

MIDDLEWARE_CLASSES = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.auth.middleware.SessionAuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.security.SecurityMiddleware",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
]

# Allows for testing django instrumentation before migration to tracer config api
if os.environ.get("TEST_DATADOG_DJANGO_MIGRATION"):
    INSTALLED_APPS.append("ddtrace.contrib.django")
    DATADOG_TRACE = {
        "AGENT_HOSTNAME": "host-test",
        "AGENT_PORT": 1234,
        "AUTO_INSTRUMENT": True,
        "INSTRUMENT_CACHE": True,
        "INSTRUMENT_DATABASE": True,
        "INSTRUMENT_TEMPLATE": True,
        "DEFAULT_DATABASE_PREFIX": "db-test-",
        "DEFAULT_SERVICE": "django-test",
        "DEFAULT_CACHE_SERVICE": "cache-test",
        "ENABLED": True,
        "DISTRIBUTED_TRACING": True,
        "ANALYTICS_ENABLED": True,
        "ANALYTICS_SAMPLE_RATE": True,
        "TRACE_QUERY_STRING": True,
        "TAGS": {"env": "env-test"},
        "TRACER": "ddtrace.tracer",
    }
