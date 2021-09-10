import os

from ddtrace import tracer
from ddtrace.filters import TraceFilter


class PingFilter(TraceFilter):
    def process_trace(self, trace):
        # Filter out all traces with trace_id = 1
        # This is done to prevent certain traces from being included in snapshots and
        # accomplished by propagating an http trace id of 1 with the request to Django.
        return None if trace and trace[0].trace_id == 1 else trace


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)


ALLOWED_HOSTS = [
    "testserver",
    "localhost",
]

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASES = {
    "default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"},
    "postgres": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": "postgres",
        "USER": "postgres",
        "PASSWORD": "postgres",
        "HOST": "127.0.0.1",
        "PORT": 5432,
    },
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "unique-snowflake",
    },
    "redis": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": "redis://127.0.0.1:6379/1",
    },
    "pylibmc": {
        "BACKEND": "django.core.cache.backends.memcached.PyLibMCCache",
        "LOCATION": "127.0.0.1:11211",
    },
    "python_memcached": {
        "BACKEND": "django.core.cache.backends.memcached.MemcachedCache",
        "LOCATION": "127.0.0.1:11211",
    },
}

SITE_ID = 1
SECRET_KEY = "not_very_secret_in_tests"
USE_I18N = True
USE_L10N = True
STATIC_URL = "/static/"
ROOT_URLCONF = "tests.contrib.django.django_app.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [
            os.path.join(BASE_DIR, "templates"),
        ],
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

MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "tests.contrib.django.middleware.ClsMiddleware",
    "tests.contrib.django.middleware.fn_middleware",
    "tests.contrib.django.middleware.EverythingMiddleware",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
]
