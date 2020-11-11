from riot import Suite, Case

global_deps = [
    "mock",
    "opentracing",
    "pytest<4",
]

global_env = [("PYTEST_ADDOPTS", "--color=yes")]

suites = [
    Suite(
        name="black",
        command="black --check .",
        cases=[
            Case(
                pys=[3.8],
                pkgs=[
                    ("black", ["==20.8b1"]),
                ],
            ),
        ],
    ),
    Suite(
        name="flake8",
        command="flake8 ddtrace/ tests/",
        cases=[
            Case(
                pys=[3.8],
                pkgs=[
                    ("flake8", [">=3.8,<3.9"]),
                    ("flake8-blind-except", [""]),
                    ("flake8-builtins", [""]),
                    ("flake8-docstrings", [""]),
                    ("flake8-logging-format", [""]),
                    ("flake8-rst-docstrings", [""]),
                    ("pygments", [""]),
                ],
            ),
        ],
    ),
    Suite(
        name="tracer",
        command="pytest tests/tracer/",
        cases=[
            Case(
                pys=[
                    2.7,
                    3.5,
                    3.6,
                    3.7,
                    3.8,
                    3.9,
                ],
                pkgs=[("msgpack", [""])],
            ),
        ],
    ),
    Suite(
        name="pymongo",
        command="pytest tests/contrib/pymongo",
        cases=[
            Case(
                pys=[
                    2.7,
                    3.5,
                    3.6,
                    3.7,
                ],
                pkgs=[
                    (
                        "pymongo",
                        [
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
                            "",
                        ],
                    ),
                    ("mongoengine", [""]),
                ],
            ),
            Case(
                pys=[
                    3.8,
                    3.9,
                ],
                pkgs=[
                    (
                        "pymongo",
                        [
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
                            "",
                        ],
                    ),
                    ("mongoengine", [""]),
                ],
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
    Suite(
        name="django",
        command="pytest tests/contrib/django",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("django", [">=1.8,<1.9", ">=1.11,<1.12"]),
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("pytest-django", ["==3.10.0"]),
                    ("python-memcached", [""]),
                    ("redis", [">=2.10,<2.11"]),
                ],
            ),
            Case(
                pys=[3.5],
                pkgs=[
                    ("django", [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3"]),
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("pytest-django", ["==3.10.0"]),
                    ("python-memcached", [""]),
                    ("redis", [">=2.10,<2.11"]),
                ],
            ),
            Case(
                pys=[3.6, 3.7, 3.8],
                pkgs=[
                    (
                        "django",
                        [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", ""],
                    ),
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("pytest-django", ["==3.10.0"]),
                    ("python-memcached", [""]),
                    ("redis", [">=2.10,<2.11"]),
                ],
            ),
            Case(
                pys=[2.7, 3.5, 3.6],
                env=[("TEST_DATADOG_DJANGO_MIGRATION", "1")],
                pkgs=[
                    ("pytest-django", ["==3.10.0"]),
                    (
                        "django",
                        [">=1.8,<1.9", ">=1.11,<1.12"],
                    ),
                ],
            ),
            Case(
                pys=[3.5],
                env=[("TEST_DATADOG_DJANGO_MIGRATION", "1")],
                pkgs=[
                    ("pytest-django", ["==3.10.0"]),
                    (
                        "django",
                        [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3"],
                    ),
                ],
            ),
            Case(
                pys=[3.6, 3.7, 3.8],
                env=[("TEST_DATADOG_DJANGO_MIGRATION", "1")],
                pkgs=[
                    ("pytest-django", ["==3.10.0"]),
                    (
                        "django",
                        [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", ""],
                    ),
                ],
            ),
        ],
    ),
    Suite(
        name="djangorestframework",
        command="pytest tests/contrib/djangorestframework",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("django", ["==1.11"]),
                    ("djangorestframework", [">=3.4,<3.5", ">=3.7,<3.8"]),
                    ("pytest-django", ["==3.10.0"]),
                ],
            ),
            Case(
                pys=[3.5, 3.6, 3.7],
                pkgs=[
                    ("django", [">=2.2,<2.3"]),
                    ("djangorestframework", [">=3.8,<3.9", ">=3.9,<3.10", ""]),
                    ("pytest-django", ["==3.10.0"]),
                ],
            ),
            Case(
                pys=[3.6, 3.7, 3.8],
                pkgs=[
                    ("django", [">=3.0,<3.1"]),
                    ("djangorestframework", [">=3.10,<3.11"]),
                    ("pytest-django", ["==3.10.0"]),
                ],
            ),
            Case(
                pys=[3.6, 3.7, 3.8],
                pkgs=[
                    ("django", [""]),
                    ("djangorestframework", [">=3.11,<3.12"]),
                    ("pytest-django", ["==3.10.0"]),
                ],
            ),
        ],
    ),
    Suite(
        name="pynamodb",
        command="pytest tests/contrib/pynamodb",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8, 3.9],
                pkgs=[
                    ("pynamodb", [">=4.0,<4.1", ">=4.1,<4.2", ">=4.2,<4.3", ">=4.3,<4.4", ""]),
                    ("moto", [">=1.0,<2.0"]),
                ],
            ),
        ],
    ),
    Suite(
        name="requests",
        command="pytest tests/contrib/requests",
        cases=[
            Case(
                pys=[
                    2.7,
                    3.5,
                    3.6,
                    3.7,
                    3.8,
                    3.9,
                ],
                pkgs=[
                    ("requests-mock", [">=1.4"]),
                    (
                        "requests",
                        [
                            ">=2.8,<2.9",
                            ">=2.10,<2.11",
                            ">=2.12,<2.13",
                            ">=2.14,<2.15",
                            ">=2.16,<2.17",
                            ">=2.18,<2.19",
                            ">=2.20,<2.21",
                            "",
                        ],
                    ),
                ],
            ),
        ],
    ),
]
