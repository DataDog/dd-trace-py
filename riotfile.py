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
        name="internal",
        command="pytest tests/internal",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
                # still need to set pkgs even though this suite doesn't require any additional packages
                pkgs=[],
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
    Suite(
        name="flask",
        command="pytest tests/contrib/flask",
        # to support autopatch tests we want to also run another command
        # command="python tests/ddtrace_run.py pytest tests/contrib/flask_autopatch",
        cases=[
            Case(
                pys=[2.7],
                pkgs=[
                    ("flask", [">=0.9,<0.10"]),
                    ("Werkzeug", ["<1"]),
                    ("blinker", [""]),
                ],
            ),
            Case(
                # 3.7 and 3.8 are failing
                pys=[3.5, 3.6],
                pkgs=[
                    ("flask", [">=0.10,<0.11", ">=0.11,<0.12", ">=0.12,<0.13"]),
                    ("Werkzeug", ["<1"]),
                    ("blinker", [""]),
                ],
            ),
            Case(
                # 3.7 and 3.8 are failing
                pys=[3.5, 3.6],
                pkgs=[
                    ("flask", [">=1.0,<1.1", ""]),
                    ("blinker", [""]),
                ],
            ),
        ],
    ),
    Suite(
        name="flask_cache",
        command="pytest tests/contrib/flask_cache",
        # tox file included an _autopatch variant but this didn't in fact resolve to any env so must have been skipped
        cases=[
            Case(
                pys=[2.7],
                pkgs=[
                    ("flask", [">=0.10,<0.11", ">=0.11,<0.12"]),
                    ("flask_cache", [">=0.12,<0.13"]),
                    ("python-memcached", [""]),
                    ("redis", [">=2.10,<2.11"]),
                    ("blinker", [""]),
                ],
            ),
            Case(
                pys=[3.5, 3.6, 3.7, 3.8,],
                pkgs=[
                    ("flask", [">=0.10,<0.11", ">=0.11,<0.12", ">=0.12,<0.13"]),
                    ("flask_cache", [">=0.13,<0.14"]),
                    ("python-memcached", [""]),
                    ("redis", [">=2.10,<2.11"]),
                    ("blinker", [""]),
                ],
            ),
        ],
    ),
    Suite(
        name="opentracer",
        command="pytest tests/opentracer/test_tracer.py tests/opentracer/test_span.py tests/opentracer/test_span_context.py tests/opentracer/test_dd_compatibility.py tests/opentracer/test_utils.py",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
                pkgs=[],
            ),
        ],
    ),
    Suite(
        name="opentracer_asyncio",
        command="pytest tests/opentracer/test_tracer_asyncio.py",
        cases=[
            Case(
                pys=[3.5, 3.6, 3.7, 3.8,],
                pkgs=[],
            ),
        ],
    ),
    Suite(
        name="opentracer_tornado",
        command="pytest tests/opentracer/test_tracer_tornado.py",
        cases=[
            Case(
                pys=[3.5, 3.6, 3.7, 3.8,],
                pkgs=[
                    # tox included opentracer_tornado-tornado{40,41,42,43} but no such packages get installed
                    # easier to use ~=
                    # https://www.python.org/dev/peps/pep-0440/#compatible-release
                    # tox didn't include tornado 5.1 nor 6.0
                    ("tornado", [">=4.4,<4.5", ">=4.5,<4.6", ">=5.0,<5.1", ">=5.1,<5.2", ">=6.0,<6.1"])
                ],
            ),
        ],
    ),
    Suite(
        name="opentracer_gevent",
        command="pytest tests/opentracer/test_tracer_gevent.py",
        cases=[
            Case(
                pys=[2.7],
                pkgs=[
                    ("gevent", [">=1.0,<1.1"])
                ],
            ),
            Case(
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("gevent", [">=1.1,<1.2", ">=1.2,<1.3"])
                ],
            ),
            Case(
                pys=[3.7, 3.8],
                pkgs=[
                    # added gevent 1.5 but also gevent 20.4, 20.5, 20.6 have been released this year
                    ("gevent", [">=1.3,<1.4", ">=1.4,<1.5", ">=1.5,<1.6"])
                ],
            ),
        ],
    ),
]
