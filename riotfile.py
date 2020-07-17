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
        command="pytest --ignore='tests/contrib' --ignore='tests/commands' --ignore='tests/opentracer' --ignore='tests/unit' --ignore='tests/internal' --ignore='tests/test_integration' --ignore='tests/profiling' tests",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[("msgpack", [None, "==0.5.0", ">=0.5,<0.6", ">=0.6.0,<1.0", ""])],
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
        name="unit",
        command="pytest tests/unit",
        cases=[
            Case(
                pys=[2.7, 3.5, 3.6, 3.7, 3.8,], pkgs=[],
            ),
        ],
    ),
    Suite(
        name="integration",
        command="pytest tests/test_integration.py",
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
                    ("django", [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", ""],),
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
        name="opentracing",
        command="pytest tests/opentracing/test_tracer.py tests/opentracing/test_span.py tests/opentracing/test_span_context.py tests/opentracing/test_dd_compatibility.py tests/opentracing/test_utils.py",
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
                    ("tornado", [">=4.4,<4.5", ">=4.5,<4.6", ">=5.0,<5.1", ">=5.1,<5.2", ">=6.0,<6.1"])
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
]
