global_deps = [
    "mock",
    "opentracing",
    "pytest>=3",
    "pytest-benchmark",
    "pytest-django",
]

global_env = [("PYTEST_ADDOPTS", "--color=yes")]

suites = [
    Suite(
        name="tracer",
        command="pytest tests/tracer",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[("msgpack", [None, "==0.5.0", ">=0.5,<0.6", ">=0.6.0,<1.0", ""])],
            ),
        ],
    ),
    Suite(
        name="ddtracerun",
        command="pytest tests/commands/test_runner.py",
        cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[("redis", [""])],),],
    ),
    Suite(
        name="logging",
        command="pytest tests/contrib/logging/",
        cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[],),],
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
    Suite(name="unit", command="pytest tests/unit", cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[],),],),
    Suite(
        name="integration",
        command="pytest tests/integration/",
        cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[("msgpack", [None, "==0.5.0", "~=0.5", "~=0.6", ""])],),],
    ),
    Suite(
        name="redis",
        command="pytest tests/contrib/redis/",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
                pkgs=[("redis", [">=2.10,<2.11", ">=3.0,<3.1", ">=3.2,<3.3", ">=3.4,<3.5", ">=3.5,<3.6", "",],)],
            ),
        ],
    ),
    Suite(
        name="profiling",
        command="python -m tests.profiling.run pytest --capture=no --verbose tests/profiling/",
        env=[("DD_PROFILE_TEST_GEVENT", lambda case: "1" if "gevent" in case.pkgs else None),],
        cases=[
            Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[("gevent", [None, ""])],),
            # Min reqs tests
            Case(pys=[2.7], pkgs=[("gevent", ["==1.1.0"]), ("protobuf", ["==3.0.0"]), ("tenacity", ["==5.0.1"]),],),
            Case(
                pys=[3.5, 3.6, 3.7, 3.8],
                pkgs=[("gevent", ["==1.4.0"]), ("protobuf", ["==3.0.0"]), ("tenacity", ["==5.0.1"]),],
            ),
        ],
    ),
    Suite(
        name="dbapi",
        command="pytest tests/contrib/dbapi",
        cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[],),],
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
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    ("django", [">=1.8,<1.9", ">=1.11,<1.12"]),
                ],
            ),
            Case(
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
                pys=[3.6, 3.7, 3.8],
                pkgs=[
                    ("django-pylibmc", [">=0.6,<0.7"]),
                    ("django-redis", [">=4.5,<4.6"]),
                    ("pylibmc", [""]),
                    ("python-memcached", [""]),
                    ("django", [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", ""],),
                ],
            ),
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8],
                env=[("TEST_DATADOG_DJANGO_MIGRATION", "1")],
                pkgs=[
                    (
                        "django",
                        [">=1.8,<1.9", ">=1.11,<1.12", ">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", ""],
                    ),
                ],
            ),
        ],
    ),
    Suite(
        name="django_drf",
        command="pytest tests/contrib/djangorestframework",
        cases=[
            Case(pys=[2.7, 3.5, 3.6], pkgs=[("django", ["==1.11"]), ("djangorestframework", ["~=3.4", "~=3.5"]),],),
            Case(pys=[3.5, 3.6, 3.7], pkgs=[("django", ["~=2.2"]), ("djangorestframework", ["~=3.8", "~=3.10"]),],),
            Case(pys=[3.6, 3.7, 3.8], pkgs=[("django", ["~=3.0", ""]), ("djangorestframework", ["~=3.10", ""]),],),
        ],
    ),
    Suite(
        name="flask",
        command="pytest tests/contrib/flask",
        # to support autopatch tests we want to also run another command
        # command="python tests/ddtrace_run.py pytest tests/contrib/flask_autopatch",
        cases=[
            Case(pys=[2.7], pkgs=[("flask", [">=0.9,<0.10"]), ("Werkzeug", ["<1"]), ("blinker", [""]),],),
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
                pkgs=[("flask", [">=1.0,<1.1", ""]), ("blinker", [""]),],
            ),
        ],
    ),
    Suite(
        # not great but we'll repeat the contents of the flask suite for this autopatch variant
        name="flask_autopatch",
        # why are we not running ddtrace_run if that's what we are supposed to test
        command="python tests/ddtrace_run.py pytest tests/contrib/flask_autopatch",
        env=[
            ("DATADOG_SERVICE_NAME", "test.flask.service"),
            # why are we disabling jinja2?
            ("DATADOG_PATCH_MODULES", "jinja2:false"),
        ],
        cases=[
            Case(pys=[2.7], pkgs=[("flask", [">=0.9,<0.10"]), ("Werkzeug", ["<1"]), ("blinker", [""]),],),
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
                pkgs=[("flask", [">=1.0,<1.1", ""]), ("blinker", [""]),],
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
        name="httplib", command="pytest tests/contrib/httplib", cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[],),]
    ),
    Suite(
        name="opentracing",
        command="pytest tests/opentracing/core",
        cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[],),],
    ),
    Suite(
        name="opentracing_asyncio",
        command="pytest tests/opentracing/test_tracer_asyncio.py",
        cases=[Case(pys=[3.5, 3.6, 3.7, 3.8,], pkgs=[],),],
    ),
    Suite(
        name="opentracing_tornado",
        command="pytest tests/opentracing/test_tracer_tornado.py",
        cases=[
            Case(
                pys=[3.5, 3.6, 3.7, 3.8,],
                pkgs=[
                    # tox included opentracer_tornado-tornado{40,41,42,43} but no such packages get installed
                    # easier to use ~=
                    # https://www.python.org/dev/peps/pep-0440/#compatible-release
                    # tox didn't include tornado 5.1 nor 6.0
                    # TODO: tornado 6.0 does not pass the tests
                    ("tornado", [">=4.4,<4.5", ">=4.5,<4.6", ">=5.0,<5.1", ">=5.1,<5.2",])
                ],
            ),
        ],
    ),
    Suite(
        name="opentracing_gevent",
        command="pytest tests/opentracing/test_tracer_gevent.py",
        cases=[
            Case(pys=[2.7], pkgs=[("gevent", [">=1.0,<1.1"])],),
            Case(pys=[2.7, 3.5, 3.6], pkgs=[("gevent", [">=1.1,<1.2", ">=1.2,<1.3"])],),
            Case(
                pys=[3.7, 3.8],
                pkgs=[
                    # added gevent 1.5 but also gevent 20.4, 20.5, 20.6 have been released this year
                    ("gevent", [">=1.3,<1.4", ">=1.4,<1.5", ">=1.5,<1.6"])
                ],
            ),
        ],
    ),
    Suite(
        name="celery",
        command="pytest tests/contrib/celery",
        cases=[
            # Non-4.x celery should be able to use the older redis lib, since it locks to an older kombu
            Case(pys=[2.7, 3.5, 3.6], pkgs=[("celery", [">=3.1,<3.2"]), ("redis", [">=2.10,<2.11"]),],),
            # 4.x celery bumps kombu to 4.4+, which requires redis 3.2 or later, this tests against
            # older redis with an older kombu, and newer kombu/newer redis.
            # https://github.com/celery/kombu/blob/3e60e6503a77b9b1a987cf7954659929abac9bac/Changelog#L35
            Case(
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("celery", [">=4.0,<4.1", ">=4.1,<4.2"]),
                    ("redis", [">=2.10,<2.11"]),
                    ("kombu", [">=4.3,<4.4"]),
                    ("pytest", [">=3,<4"]),
                ],
            ),
            # FIXME: repeating previous case but with different combo of redis and kombu versions
            Case(
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("celery", [">=4.0,<4.1", ">=4.1,<4.2"]),
                    ("redis", [">=3.2,<3.3"]),
                    ("kombu", [">=4.4,<4.5"]),
                    ("pytest", [">=3,<4"]),
                ],
            ),
            # Celery 4.2 is now limited to Kombu 4.3
            # https://github.com/celery/celery/commit/1571d414461f01ae55be63a03e2adaa94dbcb15d
            Case(
                pys=[2.7, 3.5, 3.6],
                pkgs=[
                    ("celery", [">=4.2,<4.3"]),
                    ("redis", [">=2.10,<2.11"]),
                    ("kombu", [">=4.3,<4.4"]),
                    ("pytest", [">=3,<4"]),
                ],
            ),
            # Celery 4.3 wants Kombu >= 4.4 and Redis >= 3.2
            # Python 3.7 needs Celery 4.3
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8],
                pkgs=[("celery", [">=4.3,<4.4"]), ("redis", [">=3.2,<3.3"]), ("kombu", [">=4.4,<4.5"])],
            ),
        ],
    ),
    Suite(
        name="requests",
        command="pytest tests/contrib/requests",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8],
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
    Suite(
        name="requests_gevent",
        command="pytest tests/contrib/requests_gevent/test_requests_gevent.py",
        cases=[
            Case(
                env=[("TEST_GEVENT", "1")],
                pys=[3.6],
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
                    ("gevent", [">=1.2,<1.3", ">=1.3,<1.4"]),
                ],
            ),
            Case(
                env=[("TEST_GEVENT", "1")],
                pys=[3.7, 3.8],
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
                    ("gevent", [">=1.3,<1.4"]),
                ],
            ),
        ],
    ),
    Suite(
        name="requests_autopatch",
        command="pytest tests/contrib/requests",
        cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[("tornado", [">=4.4,<4.5", ">=4.5,<4.6"])],),],
    ),
    Suite(
        name="sqlite", command="pytest tests/contrib/sqlite3", cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[],),]
    ),
    Suite(
        name="tornado",
        command="pytest tests/contrib/tornado",
        cases=[
            Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[("tornado", [">=4.4,<4.5", ">=4.5,<4.6"])],),
            Case(pys=[3.7, 3.8], pkgs=[("tornado", [">=5.0,<5.1", ">=5.1,<5.2", ">=6.0,<6.1", ""])],),
            Case(
                pys=[2.7],
                pkgs=[
                    ("tornado", [">=4.4,<4.5", ">=4.5,<4.6"]),
                    ("futures", [">=3.0,<3.1", ">=3.1,<3.2", ">=3.2,<3.3", ""]),
                ],
            ),
        ],
    ),
    Suite(
        name="aiobotocore",
        command="pytest tests/contrib/aiobotocore",
        cases=[
            # aiobotocore 0.2 requires pinning
            Case(pys=[3.5, 3.6], pkgs=[("aiobotocore", [">=0.2,<0.3",],), ("multidict", ["==4.5.2"])],),
            # aiobotocore dropped Python 3.5 support in 0.12
            Case(
                pys=[3.5],
                pkgs=[
                    (
                        "aiobotocore",
                        [
                            ">=0.2,<0.3",
                            ">=0.3,<0.4",
                            ">=0.4,<0.5",
                            ">=0.5,<0.6",
                            ">=0.6,<0.7",
                            ">=0.7,<0.8",
                            ">=0.8,<0.9",
                            ">=0.9,<0.10",
                            ">=0.10,<0.11",
                            ">=0.11,<0.12",
                        ],
                    )
                ],
            ),
            Case(
                pys=[3.6],
                pkgs=[
                    (
                        "aiobotocore",
                        [
                            ">=0.2,<0.3",
                            ">=0.3,<0.4",
                            ">=0.4,<0.5",
                            ">=0.5,<0.6",
                            ">=0.6,<0.7",
                            ">=0.7,<0.8",
                            ">=0.8,<0.9",
                            ">=0.9,<0.10",
                            ">=0.10,<0.11",
                            ">=0.11,<0.12",
                            ">=0.12,<0.13",
                        ],
                    )
                ],
            ),
            # aiobotocore 0.2 and 0.4 do not work because they use async as a reserved keyword
            Case(
                pys=[3.7, 3.8],
                pkgs=[
                    (
                        "aiobotocore",
                        [
                            ">=0.3,<0.4",
                            ">=0.5,<0.6",
                            # !!! tox didn't include 0.6 test
                            ">=0.6,<0.7",
                            ">=0.7,<0.8",
                            ">=0.8,<0.9",
                            ">=0.9,<0.10",
                            ">=0.10,<0.11",
                            ">=0.11,<0.12",
                            ">=0.12,<0.13",
                        ],
                    )
                ],
            ),
        ],
    ),
    Suite(
        name="aiohttp",
        command="pytest tests/contrib/aiohttp",
        cases=[
            Case(
                pys=[3.5, 3.6],
                pkgs=[
                    ("aiohttp", [">=1.2,<1.3", ">=1.3,<1.4", ">=2.0,<2.1", ">=2.2,<2.3"]),
                    ("aiohttp_jinja2", [">=0.12,<0.13", ">=0.13,<0.14"]),
                    # force the downgrade as a workaround
                    # https://github.com/aio-libs/aiohttp/issues/2662
                    ("yarl", ["==0.18.0"]),
                ],
            ),
            Case(
                pys=[3.5, 3.6, 3.7, 3.8],
                pkgs=[("aiohttp", [">=2.3,<2.4"]), ("aiohttp_jinja2", [">=0.15,<0.16"]), ("yarl", [">=1.0,<1.1"]),],
            ),
            Case(
                pys=[3.5, 3.6, 3.7],
                pkgs=[
                    ("aiohttp", [">=3.0,<3.1", ">=3.1,<3.2", ">=3.2,<3.3", ">=3.3,<3.4", ">=3.5,<3.6", ">=3.6,<3.7"]),
                    ("aiohttp_jinja2", [">=0.15,<0.16"]),
                    ("yarl", [">=1.0,<1.1"]),
                ],
            ),
            Case(
                pys=[3.8],
                pkgs=[
                    ("aiohttp", [">=3.0,<3.1", ">=3.1,<3.2", ">=3.2,<3.3", ">=3.3,<3.4", ">=3.6,<3.7"]),
                    ("aiohttp_jinja2", [">=0.15,<0.16"]),
                    ("yarl", [">=1.0,<1.1"]),
                ],
            ),
        ],
        # Python 3.7 needs at least aiohttp 2.3
    ),
    Suite(
        name="vertica",
        command="pytest tests/contrib/vertica",
        cases=[Case(pys=[2.7, 3.5, 3.6, 3.7, 3.8], pkgs=[("vertica-python", [">=0.6,<0.7", ">=0.7,<0.8"])],),],
    ),
]
