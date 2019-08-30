"""
Settings configuration for the Django web framework. Update this
configuration if you need to change the default behavior of
Django during tests
"""
import os
import django


BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:'
    }
}

SITE_ID = 1
SECRET_KEY = 'not_very_secret_in_tests'
USE_I18N = True
USE_L10N = True
STATIC_URL = '/static/'
<<<<<<< HEAD:tests/contrib/djangorestframework_old/app/settings.py
ROOT_URLCONF = 'tests.contrib.djangorestframework_old.app.views'
=======
ROOT_URLCONF = 'tests.contrib.djangorestframework.app.views'
>>>>>>> origin/master:tests/contrib/djangorestframework/app/settings.py

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

if (1, 10) <= django.VERSION < (2, 0):
    MIDDLEWARE = [
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.contrib.auth.middleware.SessionAuthenticationMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
        'django.middleware.clickjacking.XFrameOptionsMiddleware',
        'django.middleware.security.SecurityMiddleware',

        'tests.contrib.django_old.app.middlewares.CatchExceptionMiddleware',
    ]

# Django 2.0 has different defaults
elif django.VERSION >= (2, 0):
    MIDDLEWARE = [
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
        'django.middleware.clickjacking.XFrameOptionsMiddleware',
        'django.middleware.security.SecurityMiddleware',

        'tests.contrib.django_old.app.middlewares.CatchExceptionMiddleware',
    ]

# Pre 1.10 style
else:
    MIDDLEWARE_CLASSES = [
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.middleware.common.CommonMiddleware',
        'django.middleware.csrf.CsrfViewMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'django.contrib.auth.middleware.SessionAuthenticationMiddleware',
        'django.contrib.messages.middleware.MessageMiddleware',
        'django.middleware.clickjacking.XFrameOptionsMiddleware',
        'django.middleware.security.SecurityMiddleware',

        'tests.contrib.django_old.app.middlewares.CatchExceptionMiddleware',
    ]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',

    # tracer app
    'ddtrace.contrib.django',

    # djangorestframework
    'rest_framework'
]

DATADOG_TRACE = {
    # tracer with a DummyWriter
    'TRACER': 'tests.contrib.django_old.utils.tracer',
    'ENABLED': True,
    'TAGS': {
        'env': 'test',
    },
}

REST_FRAMEWORK = {
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAdminUser',
    ],

<<<<<<< HEAD:tests/contrib/djangorestframework_old/app/settings.py
    'EXCEPTION_HANDLER': 'tests.contrib.djangorestframework_old.app.exceptions.custom_exception_handler'
=======
    'EXCEPTION_HANDLER': 'tests.contrib.djangorestframework.app.exceptions.custom_exception_handler'
>>>>>>> origin/master:tests/contrib/djangorestframework/app/settings.py
}
