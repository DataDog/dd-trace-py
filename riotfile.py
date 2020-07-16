global_deps = [
    "mock",
    "opentracing",
    "pytest<4",
    "pytest-benchmark",
    "pytest-django",
]

global_env = [("PYTEST_ADDOPTS", "--color=yes")]

suites = [
    Suite(
        name="tracer",
        command="pytest tests/test_tracer.py",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
                pkgs=[("msgpack", [None, "==0.5.0", ">=0.5,<0.6", ">=0.6.0,<1.0", ""])],
            ),
        ],
    ),
    Suite(
        name="redis",
        command="pytest tests/contrib/redis/",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
                pkgs=[
                    (
                        "redis",
                        [
                            ">=2.10,<2.11",
                            ">=3.0,<3.1",
                            ">=3.2,<3.3",
                            ">=3.4,<3.5",
                            ">=3.5,<3.6",
                            "",
                        ],
                    )
                ],
            ),
        ],
    ),
    Suite(
        name="profiling",
        command="python -m tests.profiling.run pytest --capture=no --verbose tests/profiling/",
        env=[
            ("DD_PROFILE_TEST_GEVENT", lambda case: "1" if "gevent" in case.pkgs else None),
        ],
        cases=[
            Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[("gevent", [None, ""])],),
            # Min reqs tests
            Case(
                pys=[2.7],
                pkgs=[
                    ("gevent", ["==1.1.0"]),
                    ("protobuf", ["==3.0.0"]),
                    ("tenacity", ["==5.0.1"]),
                ],
            ),
            Case(
                pys=[3.5, 3.6, 3.7, 3.8],
                pkgs=[
                    ("gevent", ["==1.4.0"]),
                    ("protobuf", ["==3.0.0"]),
                    ("tenacity", ["==5.0.1"]),
                ],
            ),
        ],
    ),
    Suite(
        name="django",
        command="pytest tests/contrib/django",
        cases=[
            Case(
                env=[("TEST_DATADOG_DJANGO_MIGRATION", [None, "1"])],
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    ("django", [">=1.8,<1.9", ">=1.11,<1.12"]),
                ],
            ),
            Case(
                env=[("TEST_DATADOG_DJANGO_MIGRATION", [None, "1"])],
                pys=[3.5],
                pkgs=[
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    ("django", [">=2.0,<2.1", ">=2.1,<2.2"]),
                ],
            ),
            Case(
                env=[("TEST_DATADOG_DJANGO_MIGRATION", [None, "1"])],
                pys=[3.6, 3.7, 3.8],
                pkgs=[
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    (
                        "django",
                        [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", ""],
                    ),
                ],
            ),
        ],
    ),
]
