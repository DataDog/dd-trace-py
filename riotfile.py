from riot import Venv, latest

venv = Venv(
    pkgs={
        "mock": latest,
        "pytest": latest,
        "coverage": latest,
        "pytest-cov": latest,
        "opentracing": latest,
    },
    venvs=[
        Venv(
            name="black",
            command="black --check .",
            venvs=[
                Venv(
                    pys=3.8,
                    pkgs={
                        "black": "==20.8b1",
                    },
                ),
            ],
        ),
        Venv(
            name="flake8",
            command="flake8 ddtrace/ tests/",
            venvs=[
                Venv(
                    pys=3.8,
                    pkgs={
                        "flake8": ">=3.8,<3.9",
                        "flake8-blind-except": latest,
                        "flake8-builtins": latest,
                        "flake8-docstrings": latest,
                        "flake8-logging-format": latest,
                        "flake8-rst-docstrings": latest,
                        "pygments": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="tracer",
            command="pytest tests/tracer/",
            venvs=[
                Venv(
                    pys=[
                        2.7,
                        3.5,
                        3.6,
                        3.7,
                        3.8,
                        3.9,
                    ],
                    pkgs={"msgpack": latest},
                ),
            ],
        ),
        Venv(
            name="pymongo",
            command="pytest tests/contrib/pymongo",
            venvs=[
                Venv(
                    pys=[
                        2.7,
                        3.5,
                        3.6,
                        3.7,
                    ],
                    pkgs={
                        "pymongo": [
                            ">=3.0,<3.1",
                            ">=3.1,<3.2",
                            ">=3.2,<3.3",
                            ">=3.3,<3.4",
                            ">=3.4,<3.5",
                            ">=3.5,<3.6",
                            ">=3.6,<3.7",
                            ">=3.7,<3.8",
                            ">=3.8,<3.9",
                            ">=3.9,<3.10",
                            ">=3.10,<3.11",
                            latest,
                        ],
                        "mongoengine": latest,
                    },
                ),
                Venv(
                    pys=[
                        3.8,
                        3.9,
                    ],
                    pkgs={
                        "pymongo": [
                            ">=3.0,<3.1",
                            ">=3.1,<3.2",
                            ">=3.2,<3.3",
                            ">=3.3,<3.4",
                            ">=3.5,<3.6",
                            ">=3.6,<3.7",
                            ">=3.7,<3.8",
                            ">=3.8,<3.9",
                            ">=3.9,<3.10",
                            ">=3.10,<3.11",
                            latest,
                        ],
                        "mongoengine": latest,
                    },
                ),
            ],
        ),
        # Django  Python version support
        # 1.11    2.7, 3.4, 3.5, 3.6, 3.7 (added in 1.11.17)
        # 2.0     3.4, 3.5, 3.6, 3.7
        # 2.1     3.5, 3.6, 3.7
        # 2.2     3.5, 3.6, 3.7, 3.8 (added in 2.2.8)
        # 3.0     3.6, 3.7, 3.8
        # 3.1     3.6, 3.7, 3.8
        # Source: https://docs.djangoproject.com/en/dev/faq/install/#what-python-version-can-i-use-with-django
        Venv(
            name="django",
            command="pytest tests/contrib/django",
            venvs=[
                Venv(
                    pys=[2.7, 3.5, 3.6],
                    pkgs={
                        "django": [">=1.8,<1.9", ">=1.11,<1.12"],
                        "django-pylibmc": ">=0.6,<0.7",
                        "django-redis": ">=4.5,<4.6",
                        "pylibmc": latest,
                        "pytest-django": "==3.10.0",
                        "python-memcached": latest,
                        "redis": ">=2.10,<2.11",
                    },
                ),
                Venv(
                    pys=[3.5],
                    pkgs={
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3"],
                        "django-pylibmc": ">=0.6,<0.7",
                        "django-redis": ">=4.5,<4.6",
                        "pylibmc": latest,
                        "pytest-django": "==3.10.0",
                        "python-memcached": latest,
                        "redis": ">=2.10,<2.11",
                    },
                ),
                Venv(
                    pys=[3.6, 3.7, 3.8],
                    pkgs={
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", latest],
                        "django-pylibmc": ">=0.6,<0.7",
                        "django-redis": ">=4.5,<4.6",
                        "pylibmc": latest,
                        "pytest-django": "==3.10.0",
                        "python-memcached": latest,
                        "redis": ">=2.10,<2.11",
                    },
                ),
                Venv(
                    pys=[2.7, 3.5, 3.6],
                    env={"TEST_DATADOG_DJANGO_MIGRATION": "1"},
                    pkgs={
                        "pytest-django": "==3.10.0",
                        "django": [">=1.8,<1.9", ">=1.11,<1.12"],
                    },
                ),
                Venv(
                    pys=[3.5],
                    env={"TEST_DATADOG_DJANGO_MIGRATION": "1"},
                    pkgs={
                        "pytest-django": "==3.10.0",
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3"],
                    },
                ),
                Venv(
                    pys=[3.6, 3.7, 3.8],
                    env={"TEST_DATADOG_DJANGO_MIGRATION": "1"},
                    pkgs={
                        "pytest-django": "==3.10.0",
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="djangorestframework",
            command="pytest tests/contrib/djangorestframework",
            venvs=[
                Venv(
                    pys=[2.7, 3.5, 3.6],
                    pkgs={
                        "django": "==1.11",
                        "djangorestframework": [">=3.4,<3.5", ">=3.7,<3.8"],
                        "pytest-django": "==3.10.0",
                    },
                ),
                Venv(
                    pys=[3.5, 3.6, 3.7],
                    pkgs={
                        "django": ">=2.2,<2.3",
                        "djangorestframework": [">=3.8,<3.9", ">=3.9,<3.10", latest],
                        "pytest-django": "==3.10.0",
                    },
                ),
                Venv(
                    pys=[3.6, 3.7, 3.8],
                    pkgs={
                        "django": ">=3.0,<3.1",
                        "djangorestframework": ">=3.10,<3.11",
                        "pytest-django": "==3.10.0",
                    },
                ),
                Venv(
                    pys=[3.6, 3.7, 3.8],
                    pkgs={
                        "django": latest,
                        "djangorestframework": ">=3.11,<3.12",
                        "pytest-django": "==3.10.0",
                    },
                ),
            ],
        ),
        Venv(
            name="pynamodb",
            command="pytest tests/contrib/pynamodb",
            venvs=[
                Venv(
                    pys=[2.7, 3.5, 3.6, 3.7, 3.8, 3.9],
                    pkgs={
                        "pynamodb": [">=4.0,<4.1", ">=4.1,<4.2", ">=4.2,<4.3", ">=4.3,<4.4", latest],
                        "moto": ">=1.0,<2.0",
                    },
                ),
            ],
        ),
        Venv(
            name="starlette",
            command="pytest tests/contrib/starlette",
            venvs=[
                Venv(
                    pys=[3.6, 3.7, 3.8, 3.9],
                    pkgs={
                        "starlette": [">=0.13,<0.14", ">=0.14,<0.15", latest],
                        "httpx": latest,
                        "pytest-asyncio": latest,
                        "requests": latest,
                        "aiofiles": latest,
                        "sqlalchemy": latest,
                        "aiosqlite": latest,
                        "databases": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="requests",
            command="pytest tests/contrib/requests",
            venvs=[
                Venv(
                    pys=[
                        2.7,
                        3.5,
                        3.6,
                        3.7,
                        3.8,
                        3.9,
                    ],
                    pkgs={
                        "requests-mock": ">=1.4",
                        "requests": [
                            ">=2.8,<2.9",
                            ">=2.10,<2.11",
                            ">=2.12,<2.13",
                            ">=2.14,<2.15",
                            ">=2.16,<2.17",
                            ">=2.18,<2.19",
                            ">=2.20,<2.21",
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="boto",
            command="pytest tests/contrib/boto",
            venvs=[
                Venv(
                    pys=[2.7, 3.5, 3.6],
                    pkgs={
                        "boto": latest,
                        "moto": ["<1.0"],
                    },
                ),
            ],
        ),
        Venv(
            name="botocore",
            command="pytest tests/contrib/botocore",
            venvs=[
                Venv(
                    pys=[2.7, 3.5, 3.6, 3.7, 3.8, 3.9],
                    pkgs={
                        "botocore": latest,
                        "moto": [">=1.0,<2.0"],
                    },
                ),
            ],
        ),
    ],
)
