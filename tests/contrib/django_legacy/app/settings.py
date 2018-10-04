"""
Settings configuration for the Django web framework. Update this
configuration if you need to change the default behavior of
Django during tests
"""
import os
import sys


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:'
    }
}

# Python dotted path to the WSGI application used by Django's runserver.
WSGI_APPLICATION = 'app.wsgi.application'

CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'unique-snowflake',
    },
    # 'redis': {
    #     'BACKEND': 'django_redis.cache.RedisCache',
    #     'LOCATION': 'redis://127.0.0.1:6379/1',
    # },
    # 'pylibmc': {
    #     'BACKEND': 'django.core.cache.backends.memcached.PyLibMCCache',
    #     'LOCATION': '127.0.0.1:11211',
    # },
    'python_memcached': {
        'BACKEND': 'django.core.cache.backends.memcached.MemcachedCache',
        'LOCATION': '127.0.0.1:11211',
    },
    # 'django_pylibmc': {
    #     'BACKEND': 'django_pylibmc.memcached.PyLibMCCache',
    #     'LOCATION': '127.0.0.1:11211',
    #     'BINARY': True,
    #     'OPTIONS': {
    #         'tcp_nodelay': True,
    #         'ketama': True
    #     }
    # },
}

SITE_ID = 1
SECRET_KEY = 'not_very_secret_in_tests'
USE_I18N = True
USE_L10N = True
STATIC_URL = '/static/'
ROOT_URLCONF = 'tests.contrib.django_legacy.app.views'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            os.path.join(BASE_DIR, 'app', 'templates'),
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

MIDDLEWARE_CLASSES = [
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    # 'django.contrib.auth.middleware.SessionAuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # 'django.middleware.security.SecurityMiddleware',
    'tests.contrib.django_legacy.app.middlewares.CatchExceptionMiddleware',
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'app',
]

# TODO: for some reason this corrects the failing import
# for tests.contrib.django_legacy.utils.tracer
sys.path.insert(0, os.path.abspath(''))

DATADOG_TRACE = {
    # tracer with a DummyWriter
    'TRACER': 'tests.contrib.django_legacy.utils.tracer',
    'ENABLED': True,
    'TAGS': {
        'env': 'test',
    },
}

