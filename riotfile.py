# type: ignore
import logging
from typing import List  # noqa
from typing import Tuple  # noqa

from riot import Venv


logger = logging.getLogger(__name__)
latest = ""


SUPPORTED_PYTHON_VERSIONS: List[Tuple[int, int]] = [
    (3, 8),
    (3, 9),
    (3, 10),
    (3, 11),
    (3, 12),
    (3, 13),
]  # type: List[Tuple[int, int]]


def version_to_str(version: Tuple[int, int]) -> str:
    """Convert a Python version tuple to a string

    >>> version_to_str((3, 8))
    '3.8'
    >>> version_to_str((3, 9))
    '3.9'
    >>> version_to_str((3, 10))
    '3.10'
    >>> version_to_str((3, 11))
    '3.11'
    >>> version_to_str((3, 12))
    '3.12'
    >>> version_to_str((3, ))
    '3'
    """
    return ".".join(str(p) for p in version)


def str_to_version(version: str) -> Tuple[int, int]:
    """Convert a Python version string to a tuple

    >>> str_to_version("3.8")
    (3, 8)
    >>> str_to_version("3.9")
    (3, 9)
    >>> str_to_version("3.10")
    (3, 10)
    >>> str_to_version("3.11")
    (3, 11)
    >>> str_to_version("3.12")
    (3, 12)
    >>> str_to_version("3")
    (3,)
    """
    return tuple(int(p) for p in version.split("."))


MIN_PYTHON_VERSION = version_to_str(min(SUPPORTED_PYTHON_VERSIONS))
MAX_PYTHON_VERSION = version_to_str(max(SUPPORTED_PYTHON_VERSIONS))


def select_pys(min_version: str = MIN_PYTHON_VERSION, max_version: str = MAX_PYTHON_VERSION) -> List[str]:
    """Helper to select python versions from the list of versions we support

    >>> select_pys()
    ['3.8', '3.9', '3.10', '3.11', '3.12', '3.13']
    >>> select_pys(min_version='3')
    ['3.8', '3.9', '3.10', '3.11', '3.12', '3.13']
    >>> select_pys(max_version='3')
    []
    >>> select_pys(min_version='3.8', max_version='3.9')
    ['3.8', '3.9']
    """
    min_version = str_to_version(min_version)
    max_version = str_to_version(max_version)

    return [version_to_str(version) for version in SUPPORTED_PYTHON_VERSIONS if min_version <= version <= max_version]


venv = Venv(
    pkgs={
        "mock": latest,
        "pytest": latest,
        "pytest-mock": latest,
        "coverage": latest,
        "pytest-cov": latest,
        "opentracing": latest,
        "hypothesis": "<6.45.1",
    },
    env={
        "_DD_CIVISIBILITY_USE_CI_CONTEXT_PROVIDER": "1",
        "DD_TESTING_RAISE": "1",
        "DD_REMOTE_CONFIGURATION_ENABLED": "false",
        "DD_INJECTION_ENABLED": "1",
        "DD_INJECT_FORCE": "1",
        "DD_PATCH_MODULES": "unittest:false",
        "CMAKE_BUILD_PARALLEL_LEVEL": "12",
        "CARGO_BUILD_JOBS": "12",
    },
    venvs=[
        Venv(
            pys=["3"],
            name="meta-testing",
            command="pytest {cmdargs} tests/meta",
        ),
        Venv(
            name="gitlab-gen-config",
            command="python scripts/gen_gitlab_config.py {cmdargs}",
            pys=["3"],
            pkgs={
                "ruamel.yaml": latest,
                "lxml": latest,
            },
        ),
        Venv(
            name="appsec",
            pys=select_pys(),
            command="pytest {cmdargs} tests/appsec/appsec/",
            pkgs={
                "requests": latest,
                "docker": latest,
            },
            env={
                "DD_CIVISIBILITY_ITR_ENABLED": "0",
            },
        ),
        Venv(
            name="appsec_iast_packages",
            # FIXME: GrpcIO is hanging with 3.13 on CI + hatch for some reason
            pys=["3.8", "3.9", "3.10", "3.11", "3.12"],
            command="pytest {cmdargs} tests/appsec/iast_packages/",
            pkgs={
                "requests": latest,
                "astunparse": latest,
                "flask": latest,
                "virtualenv-clone": latest,
            },
            env={
                "_DD_IAST_PATCH_MODULES": "benchmarks.,tests.appsec",
                "DD_IAST_DEDUPLICATE_ENABLED": "false",
                "DD_IAST_REQUEST_SAMPLING": "100",
            },
        ),
        Venv(
            name="profile-diff",
            command="python scripts/diff.py {cmdargs}",
            pys="3",
            pkgs={
                "austin-python": "~=1.0",
                "rich": latest,
            },
        ),
        Venv(
            name="tracer",
            command="pytest -v {cmdargs} tests/tracer/",
            pkgs={
                "msgpack": latest,
                "coverage": latest,
                "attrs": latest,
                "structlog": latest,
                "httpretty": latest,
                "wheel": latest,
                "fastapi": latest,
                "httpx": latest,
                "pytest-randomly": latest,
                "setuptools": latest,
                "boto3": latest,
            },
            env={
                "DD_CIVISIBILITY_LOG_LEVEL": "none",
                "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "0",
            },
            venvs=[
                Venv(pys=select_pys()),
                # This test variant ensures tracer tests are compatible with both 64bit trace ids.
                # 128bit trace ids are tested by the default case above.
                Venv(
                    name="tracer-128-bit-traceid-disabled",
                    pys=MAX_PYTHON_VERSION,
                    env={
                        "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": "false",
                    },
                ),
                Venv(
                    name="tracer-python-optimize",
                    env={"PYTHONOPTIMIZE": "1"},
                    # Test with the latest version of Python only
                    pys=MAX_PYTHON_VERSION,
                ),
                Venv(
                    name="tracer-legacy-attrs",
                    pkgs={"cattrs": "<23.2.0", "attrs": "==22.1.0"},
                    # Test with the min version of Python only, attrs 20.1.0 is not compatible with Python 3.12
                    pys=MIN_PYTHON_VERSION,
                ),
            ],
        ),
        Venv(
            name="telemetry",
            command="pytest {cmdargs} tests/telemetry/",
            pys=select_pys(),
            pkgs={
                "requests": latest,
                "gunicorn": latest,
                "flask": "<=2.2.3",
                "httpretty": "<1.1",
                "werkzeug": "<2.0",
                "pytest-randomly": latest,
                "markupsafe": "<2.0",
            },
        ),
        Venv(
            name="integration",
            # Enabling coverage for integration tests breaks certain tests in CI
            # Also, running two separate pytest sessions, the ``civisibility`` one with --no-ddtrace
            command="pytest -vv --no-ddtrace --no-cov --ignore-glob='*civisibility*' {cmdargs} tests/integration/",
            pkgs={"msgpack": [latest], "coverage": latest, "pytest-randomly": latest},
            pys=select_pys(),
            venvs=[
                Venv(
                    name="integration-latest",
                    env={
                        "AGENT_VERSION": "latest",
                    },
                ),
                Venv(
                    name="integration-snapshot",
                    env={
                        "DD_TRACE_AGENT_URL": "http://localhost:9126",
                        "AGENT_VERSION": "testagent",
                    },
                ),
            ],
        ),
        Venv(
            name="integration-civisibility",
            # Enabling coverage for integration tests breaks certain tests in CI
            # Also, running two separate pytest sessions, the ``civisibility`` one with --no-ddtrace
            command="pytest --no-cov --no-ddtrace {cmdargs} tests/integration/test_integration_civisibility.py",
            pkgs={"msgpack": [latest], "coverage": latest, "pytest-randomly": latest},
            pys=select_pys(),
            venvs=[
                Venv(
                    name="integration-latest-civisibility",
                    env={
                        "AGENT_VERSION": "latest",
                    },
                ),
                Venv(
                    name="integration-snapshot-civisibility",
                    env={
                        "DD_TRACE_AGENT_URL": "http://localhost:9126",
                        "AGENT_VERSION": "testagent",
                    },
                ),
            ],
        ),
        Venv(
            name="datastreams",
            command="pytest --no-cov {cmdargs} tests/datastreams/",
            pkgs={
                "msgpack": [latest],
                "pytest-randomly": latest,
            },
            pys=select_pys(max_version="3.12"),
            venvs=[
                Venv(
                    name="datastreams-latest",
                    env={
                        "AGENT_VERSION": "latest",
                    },
                ),
            ],
        ),
        Venv(
            name="internal",
            env={
                "DD_TRACE_AGENT_URL": "http://ddagent:8126",
                "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "0",
            },
            command="pytest -v {cmdargs} tests/internal/",
            pkgs={
                "httpretty": latest,
                "gevent": latest,
                "pytest-randomly": latest,
                "python-json-logger": "==2.0.7",
                "pyfakefs": latest,
                "pytest-benchmark": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.11"),
                    pkgs={
                        "pytest-asyncio": "~=0.23.7",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.12"),
                    pkgs={
                        "pytest-asyncio": "~=0.23.7",
                        "setuptools": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="gevent",
            command="pytest {cmdargs} tests/contrib/gevent",
            pkgs={
                "elasticsearch": latest,
                "pynamodb": "<6.0",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pkgs={
                        "aiobotocore": "<=2.3.1",
                        "aiohttp": latest,
                        "botocore": latest,
                        "requests": latest,
                        "opensearch-py": latest,
                    },
                    venvs=[
                        Venv(
                            pys="3.8",
                            pkgs={
                                "gevent": "~=20.12.0",
                                # greenlet v1.0.0 adds support for contextvars
                                "greenlet": "~=1.0.0",
                            },
                        ),
                        Venv(
                            pys="3.9",
                            pkgs={
                                # https://github.com/gevent/gevent/issues/2076
                                "gevent": ["~=21.1.0", "<21.8.0"],
                                "greenlet": "~=1.0",
                            },
                        ),
                        Venv(
                            # gevent added support for Python 3.10 in 21.8.0
                            pys="3.10",
                            pkgs={
                                "gevent": ["~=21.12.0", latest],
                            },
                        ),
                        Venv(
                            pys="3.11",
                            pkgs={
                                "gevent": ["~=22.10.0", latest],
                            },
                        ),
                        Venv(
                            pys=select_pys(min_version="3.12"),
                            pkgs={
                                "gevent": [latest],
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="runtime",
            command="pytest {cmdargs} tests/runtime/",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "msgpack": latest,
                        "pytest-randomly": latest,
                    },
                )
            ],
        ),
        Venv(
            name="smoke_test",
            command="python tests/smoke_test.py {cmdargs}",
            venvs=[
                Venv(
                    pys=select_pys(),
                )
            ],
        ),
        Venv(
            name="ddtracerun",
            command="pytest {cmdargs} --no-cov tests/commands/test_runner.py",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "redis": latest,
                        "gevent": latest,
                        "pytest-randomly": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="debugger",
            command="pytest {cmdargs} tests/debugging/",
            pkgs={
                "msgpack": latest,
                "httpretty": latest,
                "typing-extensions": latest,
                "pytest-asyncio": latest,
            },
            pys=select_pys(),
        ),
        Venv(
            name="errortracker",
            command="pytest {cmdargs} tests/errortracking/",
            pkgs={
                "flask": latest,
            },
            pys=select_pys(min_version="3.10"),
        ),
        Venv(
            name="vendor",
            command="pytest {cmdargs} tests/vendor/",
            pys=select_pys(),
            pkgs={
                "msgpack": ["~=1.0.0", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="vertica",
            command="pytest {cmdargs} tests/contrib/vertica/",
            pys=select_pys(max_version="3.9"),
            pkgs={
                "vertica-python": [">=0.6.0,<0.7.0", ">=0.7.0,<0.8.0"],
                "pytest-randomly": latest,
            },
            # venvs=[
            # FIXME: tests fail on vertica 1.x
            # Venv(
            #     # vertica-python added support for Python 3.9/3.10 in 1.0
            #     pys=select_pys(min_version="3.8", max_version="3.10"),
            #     pkgs={"vertica-python": ["~=1.0", latest]},
            # ),
            # Venv(
            #     # vertica-python added support for Python 3.11 in 1.2
            #     pys="3.11",
            #     pkgs={"vertica-python": ["~=1.2", latest]},
            # ),
            # ],
        ),
        Venv(
            name="wait",
            command="python tests/wait-for-services.py {cmdargs}",
            # Default Python 3 (3.10) collections package breaks with kombu/vertica, so specify Python 3.9 instead.
            pys="3.9",
            create=True,
            skip_dev_install=True,
            pkgs={
                "cassandra-driver": latest,
                "psycopg2-binary": latest,
                "mysql-connector-python": "!=8.0.18",
                "vertica-python": ">=0.6.0,<0.7.0",
                "kombu": ">=4.2.0,<4.3.0",
                "pytest-randomly": latest,
                "requests": latest,
            },
        ),
        Venv(
            name="httplib",
            command="pytest {cmdargs} tests/contrib/httplib",
            pkgs={
                "pytest-randomly": latest,
            },
            pys=select_pys(),
        ),
        Venv(
            name="logging",
            command="pytest {cmdargs} tests/contrib/logging",
            pkgs={
                "pytest-randomly": latest,
            },
            pys=select_pys(),
        ),
        Venv(
            name="falcon",
            command="pytest {cmdargs} tests/contrib/falcon",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.12"),
                    pkgs={
                        "falcon": [
                            "~=3.0.0",
                            "~=3.0",  # latest 3.x
                            latest,
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.13"),
                    pkgs={
                        "falcon": [
                            "~=4.0",  # latest 4.x
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="bottle",
            pkgs={
                "WebTest": latest,
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    command="pytest {cmdargs} --ignore='tests/contrib/bottle/test_autopatch.py' tests/contrib/bottle/",
                    venvs=[
                        Venv(
                            pys=select_pys(max_version="3.9"),
                            pkgs={"bottle": [">=0.12,<0.13", latest]},
                        ),
                    ],
                ),
                Venv(
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/bottle/test_autopatch.py",
                    env={"DD_SERVICE": "bottle-app"},
                    venvs=[
                        Venv(
                            pys=select_pys(max_version="3.9"),
                            pkgs={"bottle": [">=0.12,<0.13", latest]},
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="celery",
            command="pytest {cmdargs} tests/contrib/celery",
            pkgs={
                "more_itertools": "<8.11.0",
                "pytest-randomly": latest,
            },
            venvs=[
                # Celery 4.3 wants Kombu >= 4.4 and Redis >= 3.2
                # Split into <3.8 and >=3.8 to pin importlib_metadata dependency for kombu
                #     # celery added support for Python 3.9 in 4.x
                #     pys=select_pys(min_version="3.8", max_version="3.9"),
                #     pkgs={
                #         "pytest": "~=4.0",
                #         "celery": [
                #             "latest",  # most recent 4.x
                #         ],
                #         "redis": "~=3.5",
                #         "kombu": "~=4.4",
                #     },
                # ),
                # Celery 5.x wants Python 3.6+
                # Split into <3.8 and >=3.8 to pin importlib_metadata dependency for kombu
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    env={
                        # https://docs.celeryproject.org/en/v5.0.5/userguide/testing.html#enabling
                        "PYTEST_PLUGINS": "celery.contrib.pytest",
                    },
                    pkgs={
                        "celery": [
                            "~=5.2",
                            latest,
                        ],
                        "redis": "~=3.5",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.10"),
                    env={
                        # https://docs.celeryproject.org/en/v5.0.5/userguide/testing.html#enabling
                        "PYTEST_PLUGINS": "celery.contrib.pytest",
                    },
                    pkgs={
                        "celery[redis]": [
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="cherrypy",
            command="python -m pytest {cmdargs} tests/contrib/cherrypy",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.10"),
                    pkgs={
                        "cherrypy": [
                            ">=17,<18",
                        ],
                        "more_itertools": "<8.11.0",
                        "typing-extensions": latest,
                    },
                ),
                Venv(
                    # cherrypy added support for Python 3.11 in 18.7
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "cherrypy": [">=18.0,<19", latest],
                        "more_itertools": "<8.11.0",
                    },
                ),
            ],
        ),
        Venv(
            name="pymongo",
            command="pytest {cmdargs} tests/contrib/pymongo",
            pkgs={
                "mongoengine": latest,
                "pytest-randomly": latest,
            },
            venvs=[
                # ddtrace patches different methods for the following pymongo version:
                # pymmongo<3.9, 3.9<=pymongo<3.12, 3.12<=pymongo<4.5, pymongo>=4.5
                # To get full test coverage we must test all these version ranges
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"pymongo": ["~=3.8.0", "~=3.9.0", "~=3.11", "~=4.0", latest]},
                ),
                Venv(
                    # pymongo added support for Python 3.10 in 3.12.1
                    # pymongo added support for Python 3.11 in 3.12.3
                    pys=select_pys(min_version="3.10"),
                    pkgs={"pymongo": ["~=3.12.3", "~=4.0", latest]},
                ),
            ],
        ),
        Venv(
            name="ddtrace_api",
            command="pytest {cmdargs} tests/contrib/ddtrace_api",
            pkgs={"ddtrace-api": "==0.0.1", "requests": latest},
            pys=select_pys(min_version="3.8"),
        ),
        # Django  Python version support
        # 2.2     3.5, 3.6, 3.7, 3.8  3.9
        # 3.2     3.6, 3.7, 3.8, 3.9, 3.10
        # 4.0     3.8, 3.9, 3.10
        # 4.1     3.8, 3.9, 3.10, 3.11
        # 4.2     3.8, 3.9, 3.10, 3.11
        # 5.0     3.10, 3.11, 3.12
        # Source: https://docs.djangoproject.com/en/dev/faq/install/#what-python-version-can-i-use-with-django
        Venv(
            name="django",
            command="pytest {cmdargs} tests/contrib/django",
            pkgs={
                "django-redis": ">=4.5,<4.6",
                "django-pylibmc": ">=0.6,<0.7",
                "daphne": [latest],
                "requests": [latest],
                "redis": ">=2.10,<2.11",
                "psycopg2-binary": [">=2.8.6"],  # We need <2.9.0 for Python 2.7, and >2.9.0 for 3.9+
                "pytest-django[testing]": "==3.10.0",
                "pylibmc": latest,
                "python-memcached": latest,
                "pytest-randomly": latest,
                "django-q": latest,
                "spyne": latest,
                "zeep": latest,
                "bcrypt": "==4.2.1",
            },
            env={
                "DD_CIVISIBILITY_ITR_ENABLED": "0",
                "DD_IAST_REQUEST_SAMPLING": "100",  # Override default 30% to analyze all IAST requests
            },
            venvs=[
                Venv(
                    # django dropped support for Python 3.8/3.9 in 5.0
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "django": ["~=4.0"],
                        "channels": latest,
                    },
                ),
                Venv(
                    # django started supporting psycopg3 in 4.2 for versions >3.1.8
                    pys=select_pys(min_version="3.8", max_version="3.13"),
                    pkgs={
                        "django": ["~=4.2"],
                        "psycopg": latest,
                        "channels": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="django:django_hosts",
            command="pytest {cmdargs} tests/contrib/django_hosts",
            pkgs={
                "pytest-django[testing]": [
                    "==3.10.0",
                ],
                "pytest-randomly": latest,
                "setuptools": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "django_hosts": "~=4.0",
                        "django": "~=3.2",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "django_hosts": ["~=5.0", latest],
                        "django": "~=4.0",
                    },
                ),
            ],
        ),
        Venv(
            name="django:djangorestframework",
            command="pytest {cmdargs} tests/contrib/djangorestframework",
            pkgs={
                "pytest-django[testing]": "==3.10.0",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    # djangorestframework dropped support for Django 2.x in 3.14
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "django": ">=2.2,<2.3",
                        "djangorestframework": ["==3.12.4", "==3.13.1"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "django": "~=3.2",
                        "djangorestframework": ">=3.11,<3.12",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "django": ["~=4.0"],
                        "djangorestframework": ["~=3.13", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="django:celery",
            command="pytest {cmdargs} tests/contrib/django_celery",
            pkgs={
                # The test app was built with Django 2. We don't need to test
                # other versions as the main purpose of these tests is to ensure
                # an error-free interaction between Django and Celery. We find
                # that we currently have no reasons for expanding this matrix.
                "celery": latest,
                "gevent": latest,
                "requests": latest,
                "typing-extensions": latest,
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.11"),
                    pkgs={
                        "sqlalchemy": "~=1.2.18",
                        "django": "==2.2.1",
                    },
                ),
                Venv(
                    pys="3.12",
                    pkgs={
                        "sqlalchemy": latest,
                        "django": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="dramatiq",
            command="pytest {cmdargs} tests/contrib/dramatiq",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={"dramatiq": latest, "pytest": latest, "redis": latest},
                ),
            ],
        ),
        Venv(
            name="elasticsearch",
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_elasticsearch.py",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "elasticsearch": [
                            "~=7.13.0",  # latest to support unofficial Elasticsearch servers, released Jul 2021
                            "~=7.17",
                            "==8.0.1",  # 8.0.0 has a bug that interferes with tests
                            latest,
                        ]
                    },
                ),
                Venv(pys=select_pys(), pkgs={"elasticsearch1": ["~=1.10.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch2": ["~=2.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch5": ["~=5.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch6": ["~=6.8.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch7": ["~=7.13.0", latest]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch8": ["~=8.0.1", latest]}),
            ],
        ),
        Venv(
            name="elasticsearch:multi",
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_elasticsearch_multi.py",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "elasticsearch": latest,
                        "elasticsearch7": latest,
                        "pytest-randomly": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="elasticsearch:async",
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_async.py",
            env={"AIOHTTP_NO_EXTENSIONS": "1"},  # needed until aiohttp is updated to support python 3.12
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "elasticsearch[async]": latest,
                        "elasticsearch7[async]": latest,
                        "opensearch-py[async]": latest,
                        "pytest-randomly": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="elasticsearch:opensearch",
            # avoid running tests in ElasticsearchPatchTest, only run tests with OpenSearchPatchTest configurations
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_opensearch.py -k 'not ElasticsearchPatchTest'",
            pys=select_pys(),
            pkgs={
                "opensearch-py[requests]": ["~=1.1.0", "~=2.0.0", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="flask",
            command="pytest {cmdargs} tests/contrib/flask",
            pkgs={
                "blinker": latest,
                "requests": latest,
                "werkzeug": "~=2.0",
                "urllib3": "~=1.0",
                "pytest-randomly": latest,
                "importlib_metadata": latest,
                "flask-openapi3": latest,
            },
            venvs=[
                # Flask 1.x.x
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "flask": "~=1.0",
                        # https://github.com/pallets/itsdangerous/issues/290
                        # DEV: Breaking change made in 2.1.0 release
                        "itsdangerous": "<2.1.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                        # DEV: Flask 1.0.x is missing a maximum version for werkzeug dependency
                        "werkzeug": "<2.0",
                    },
                ),
                Venv(
                    pys=select_pys(max_version="3.9"),
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DD_SERVICE": "test.flask.service",
                        "DD_PATCH_MODULES": "jinja2:false",
                    },
                    pkgs={
                        "flask": "~=1.0",
                        # https://github.com/pallets/itsdangerous/issues/290
                        # DEV: Breaking change made in 2.0 release
                        "itsdangerous": "<2.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                        # DEV: Flask 1.0.x is missing a maximum version for werkzeug dependency
                        "werkzeug": "<2.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "flask": [
                            "~=2.0",
                            "~=3.0.0",
                            latest,
                        ],
                        # Flask 3.x.x requires Werkzeug >= 3.0.0
                        "werkzeug": ">=3.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DD_SERVICE": "test.flask.service",
                        "DD_PATCH_MODULES": "jinja2:false",
                    },
                    pkgs={
                        "flask": [
                            "~=3.0.0",
                            latest,
                        ],
                        # Flask 3.x.x requires Werkzeug >= 3.0.0
                        "werkzeug": ">=3.0",
                    },
                ),
            ],
        ),
        Venv(
            name="flask_cache",
            command="pytest {cmdargs} tests/contrib/flask_cache",
            pkgs={
                "python-memcached": latest,
                "redis": "~=2.0",
                "blinker": latest,
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pkgs={
                        "flask": "~=0.12.0",
                        "Werkzeug": ["<1.0"],
                        "Flask-Cache": "~=0.13.1",
                        "werkzeug": "<1.0",
                        "pytest": "~=6.0",
                        "pytest-mock": "==2.0.0",
                        "pytest-cov": "~=3.0",
                        "Jinja2": "~=2.10.0",
                        "more_itertools": "<8.11.0",
                        # https://github.com/pallets/itsdangerous/issues/290
                        # DEV: Breaking change made in 2.0 release
                        "itsdangerous": "<2.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                    },
                    venvs=[
                        Venv(pys=select_pys(min_version="3.8", max_version="3.9"), pkgs={"exceptiongroup": latest}),
                    ],
                ),
                Venv(
                    pkgs={
                        "flask": "~=1.1.0",
                        "flask-caching": ["~=1.10.0", latest],
                        # https://github.com/pallets/itsdangerous/issues/290
                        # DEV: Breaking change made in 2.0 release
                        "itsdangerous": "<2.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                    },
                    venvs=[
                        Venv(
                            pys=select_pys(min_version="3.8", max_version="3.11"),
                        ),
                        Venv(pys=select_pys(min_version="3.12"), pkgs={"redis": latest}),
                    ],
                ),
                Venv(
                    pkgs={
                        "flask": [latest],
                        "flask-caching": ["~=1.10.0", latest],
                    },
                    venvs=[
                        Venv(
                            pys=select_pys(min_version="3.8", max_version="3.11"),
                        ),
                        Venv(pys=select_pys(min_version="3.12"), pkgs={"redis": latest}),
                    ],
                ),
            ],
        ),
        Venv(
            name="mako",
            command="pytest {cmdargs} tests/contrib/mako",
            pys=select_pys(),
            pkgs={
                "mako": ["~=1.1.0", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="mysql",
            command="pytest {cmdargs} tests/contrib/mysql",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"mysql-connector-python": ["==8.0.5", latest]},
                ),
                Venv(
                    # mysql-connector-python added support for Python 3.10 in 8.0.28
                    pys="3.10",
                    pkgs={"mysql-connector-python": ["~=8.0.28", latest]},
                ),
                Venv(
                    # mysql-connector-python added support for Python 3.11 in 8.0.31
                    pys="3.11",
                    pkgs={"mysql-connector-python": ["~=8.0.31", latest]},
                ),
                Venv(
                    pys=select_pys(min_version="3.12"),
                    pkgs={"mysql-connector-python": latest},
                ),
            ],
        ),
        Venv(
            name="psycopg:psycopg2",
            command="pytest {cmdargs} tests/contrib/psycopg2",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys="3.8",
                    pkgs={"psycopg2-binary": "~=2.8.0"},
                ),
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.12"),
                    # psycopg2-binary added support for Python 3.9/3.10 in 2.9.1
                    # psycopg2-binary added support for Python 3.11 in 2.9.2
                    pkgs={"psycopg2-binary": ["~=2.9.2", latest]},
                ),
            ],
        ),
        Venv(
            name="psycopg",
            command="pytest {cmdargs} tests/contrib/psycopg",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pkgs={"psycopg": [latest]},
                    venvs=[
                        Venv(
                            pys=select_pys(min_version="3.8", max_version="3.11"),
                            pkgs={
                                "pytest-asyncio": "==0.21.1",
                            },
                        ),
                        Venv(
                            pys=select_pys(min_version="3.12", max_version="3.12"),
                            pkgs={
                                "pytest-asyncio": "==0.23.7",
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="appsec_iast_memcheck",
            command="pytest --memray --stacks=35 {cmdargs} tests/appsec/iast_memcheck/",
            pys=select_pys(),
            pkgs={
                "requests": latest,
                "urllib3": latest,
                "pycryptodome": latest,
                "cryptography": latest,
                "pytest-memray": latest,
                "psycopg2-binary": "~=2.9.9",
            },
            env={
                "_DD_IAST_PATCH_MODULES": "benchmarks.,tests.appsec.",
                "DD_IAST_REQUEST_SAMPLING": "100",
                "DD_IAST_DEDUPLICATION_ENABLED": "false",
            },
        ),
        Venv(
            name="pymemcache",
            pys=select_pys(),
            pkgs={
                "pytest-randomly": latest,
                "pymemcache": [
                    "~=3.4.2",
                    "~=3.5",
                    latest,
                ],
            },
            venvs=[
                Venv(command="pytest {cmdargs} --ignore=tests/contrib/pymemcache/autopatch tests/contrib/pymemcache"),
                Venv(command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/pymemcache/autopatch/"),
            ],
        ),
        Venv(
            name="pynamodb",
            command="pytest {cmdargs} tests/contrib/pynamodb",
            # TODO: Py312 requires changes to test code
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.11"),
                    pkgs={
                        "pynamodb": ["~=5.0", "~=5.3", "<6.0"],
                        "moto": ">=1.0,<2.0",
                        "cfn-lint": "~=0.53.1",
                        "Jinja2": "~=2.10.0",
                        "pytest-randomly": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="starlette",
            command="pytest {cmdargs} tests/contrib/starlette",
            pkgs={
                "httpx": latest,
                "pytest-asyncio": "==0.21.1",
                "greenlet": "==3.0.3",
                "requests": latest,
                "aiofiles": latest,
                "sqlalchemy": "<2.0",
                "aiosqlite": latest,
                "databases": latest,
                "pytest-randomly": latest,
                "anyio": "<4.0",
            },
            venvs=[
                # starlette added new TestClient after v0.20
                # starlette added new root_path/path definitions after v0.33
                Venv(
                    # starlette added support for Python 3.9 in 0.14
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"starlette": ["~=0.14.0", "~=0.20.0", "~=0.33.0", latest]},
                ),
                Venv(
                    # starlette added support for Python 3.10 in 0.15
                    pys="3.10",
                    pkgs={"starlette": ["~=0.15.0", "~=0.20.0", "~=0.33.0", latest]},
                ),
                Venv(
                    # starlette added support for Python 3.11 in 0.21
                    pys="3.11",
                    pkgs={"starlette": ["~=0.21.0", "~=0.33.0", latest]},
                ),
                Venv(
                    pys="3.12",
                    pkgs={"starlette": latest},
                ),
            ],
        ),
        Venv(
            name="structlog",
            pys=select_pys(),
            command="pytest {cmdargs} tests/contrib/structlog",
            pkgs={
                "structlog": ["~=20.2.0", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="sqlalchemy",
            command="pytest {cmdargs} tests/contrib/sqlalchemy",
            pkgs={
                "pytest-randomly": latest,
                "psycopg2-binary": latest,
                "mysql-connector-python": latest,
                "sqlalchemy": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.12"),
                    pkgs={
                        "greenlet": "==3.0.3",
                        "sqlalchemy": ["~=1.3.0", latest],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.12"),
                    pkgs={
                        "greenlet": "==3.1.0",
                    },
                ),
            ],
        ),
        Venv(
            name="requests",
            command="pytest {cmdargs} tests/contrib/requests",
            pkgs={
                "pytest-randomly": latest,
                "urllib3": "~=1.0",
                "requests-mock": ">=1.4",
            },
            venvs=[
                Venv(
                    # requests added support for Python 3.8 in 2.23
                    pys="3.8",
                    pkgs={
                        "requests": [
                            "~=2.22.0",
                            latest,
                        ],
                    },
                ),
                Venv(
                    # requests added support for Python 3.9 in 2.25
                    pys="3.9",
                    pkgs={
                        "requests": [
                            "~=2.25.0",
                            latest,
                        ],
                    },
                ),
                Venv(
                    # requests added support for Python 3.10 in 2.27
                    pys="3.10",
                    pkgs={
                        "requests": [
                            "~=2.27",
                            latest,
                        ],
                    },
                ),
                Venv(
                    # requests added support for Python 3.11 in 2.28
                    pys="3.11",
                    pkgs={
                        "requests": [
                            "~=2.28.0",
                            latest,
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.12"),
                    pkgs={
                        "requests": [
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="wsgi",
            command="pytest {cmdargs} tests/contrib/wsgi",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "WebTest": latest,
                        "pytest-randomly": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="botocore",
            command="pytest {cmdargs} tests/contrib/botocore",
            pkgs={
                "moto[all]": "<5.0",
                "pytest-randomly": latest,
                "vcrpy": "==6.0.1",
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={"botocore": "==1.34.49", "boto3": "==1.34.49"},
                ),
                Venv(
                    pys=select_pys(min_version="3.9"),
                    pkgs={"vcrpy": "==7.0.0", "botocore": ">=1.34.131", "boto3": ">=1.34.131"},
                ),
            ],
        ),
        Venv(
            name="mongoengine",
            command="pytest {cmdargs} tests/contrib/mongoengine",
            pkgs={
                # pymongo v4.9.0 introduced breaking changes that are not yet supported by mongoengine
                "pymongo": "<4.9.0",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys="3.8",
                    pkgs={"mongoengine": ["~=0.23", latest]},
                ),
                Venv(
                    # mongoengine added support for Python 3.9/3.10 in 0.24
                    pys=select_pys(min_version="3.9"),
                    pkgs={"mongoengine": ["~=0.24", latest]},
                ),
            ],
        ),
        Venv(
            name="asgi",
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "httpx": latest,
                "asgiref": ["~=3.0.0", "~=3.0", latest],
                "pytest-randomly": latest,
            },
            pys=select_pys(min_version="3.8"),
            command="pytest {cmdargs} tests/contrib/asgi",
        ),
        Venv(
            name="mariadb",
            command="pytest {cmdargs} tests/contrib/mariadb",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.10"),
                    pkgs={
                        "mariadb": [
                            "~=1.0.0",
                            "~=1.0",
                            latest,
                        ],
                    },
                ),
                Venv(pys=select_pys(min_version="3.11"), pkgs={"mariadb": ["~=1.1.2", latest]}),
            ],
        ),
        Venv(
            name="pymysql",
            command="pytest {cmdargs} tests/contrib/pymysql",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    # pymysql added support for Python 3.8/3.9 in 0.10
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"pymysql": "~=0.10"},
                ),
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.12"),
                    pkgs={
                        "pymysql": [
                            "~=1.0",
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="pyramid",
            command="pytest {cmdargs} tests/contrib/pyramid",
            pkgs={
                "requests": [latest],
                "webtest": [latest],
                "tests/contrib/pyramid/pserve_app": [latest],
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "pyramid": [
                            "~=1.10",
                            "~=2.0",
                            latest,
                        ],
                    },
                ),
                Venv(
                    # pyramid added support for Python 3.10/3.11 in 2.1
                    # FIXME[python-3.12]: blocked on venusian release https://github.com/Pylons/venusian/issues/85
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "pyramid": [latest],
                    },
                ),
            ],
        ),
        Venv(
            name="aiobotocore",
            command="pytest {cmdargs} --no-cov tests/contrib/aiobotocore",
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "async_generator": ["~=1.10"],
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.11"),
                    pkgs={
                        "aiobotocore": ["~=1.4.2", "~=2.0.0", latest],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.12"),
                    pkgs={"aiobotocore": latest},
                ),
            ],
        ),
        Venv(
            name="fastapi",
            command="pytest {cmdargs} tests/contrib/fastapi",
            pkgs={
                "httpx": "<=0.27.2",
                "pytest-asyncio": "==0.21.1",
                "python-multipart": latest,
                "pytest-randomly": latest,
                "requests": latest,
                "aiofiles": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.10"),
                    pkgs={"fastapi": ["~=0.64.0", "~=0.90.0", latest]},
                ),
                Venv(
                    # fastapi added support for Python 3.11 in 0.86.0
                    pys=select_pys(min_version="3.11"),
                    pkgs={"fastapi": ["~=0.86.0", latest], "anyio": ">=3.4.0,<4.0"},
                ),
            ],
        ),
        Venv(
            name="aiomysql",
            command="pytest {cmdargs} tests/contrib/aiomysql",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.12"),
                    pkgs={
                        "pytest-randomly": latest,
                        "pytest-asyncio": "==0.21.1",
                        "aiomysql": ["~=0.1.0", latest],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.13"),
                    pkgs={
                        "pytest-randomly": latest,
                        "pytest-asyncio": latest,
                        "aiomysql": ["~=0.1.0", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="pytest",
            command="pytest --no-ddtrace --no-cov {cmdargs} tests/contrib/pytest/",
            pkgs={
                "pytest-randomly": latest,
                "pytest-xdist": latest,
            },
            env={
                "DD_AGENT_PORT": "9126",
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "pytest": [
                            ">=6.0,<7.0",
                            latest,
                        ],
                        "msgpack": latest,
                        "more_itertools": "<8.11.0",
                        "pytest-mock": "==2.0.0",
                        "httpx": latest,
                    },
                    venvs=[
                        Venv(
                            pkgs={
                                "pytest": ["~=6.0"],
                                "pytest-cov": "==2.9.0",
                            },
                        ),
                        Venv(
                            pkgs={
                                "pytest": ["~=7.0", latest],
                                "pytest-cov": "==2.12.0",
                            },
                            venvs=[
                                Venv(
                                    env={
                                        "_DD_PYTEST_USE_LEGACY_PLUGIN": "true",
                                    },
                                ),
                                Venv(
                                    env={
                                        "_DD_PYTEST_USE_LEGACY_PLUGIN": "false",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
                Venv(
                    pys=select_pys(min_version="3.10", max_version="3.12"),
                    pkgs={
                        "pytest": [
                            "~=6.0",
                            "~=7.0",
                            latest,
                        ],
                        "msgpack": latest,
                        "asynctest": "==0.13.0",
                        "more_itertools": "<8.11.0",
                        "httpx": latest,
                    },
                    venvs=[
                        Venv(
                            env={
                                "DD_PYTEST_LEGACY_PLUGIN": "true",
                            },
                        ),
                        Venv(
                            env={
                                "_DD_PYTEST_USE_LEGACY_PLUGIN": "false",
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="unittest",
            command="pytest --no-ddtrace {cmdargs} tests/contrib/unittest/",
            pkgs={
                "msgpack": latest,
                "pytest-randomly": latest,
            },
            env={
                "DD_PATCH_MODULES": "unittest:true",
                "DD_AGENT_PORT": "9126",
                # gitlab sets the service name to the repo name while locally the default service name is used
                # setting DD_SERVICE ensures the output of the snapshot tests is consistent.
                "DD_UNITTEST_SERVICE": "dd-trace-py",
            },
            pys=select_pys(),
        ),
        Venv(
            name="asynctest",
            command="pytest --no-ddtrace {cmdargs} tests/contrib/asynctest/",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "pytest": [
                            ">=6.0,<7.0",
                        ],
                        "asynctest": "==0.13.0",
                    },
                ),
            ],
        ),
        Venv(
            name="pytest_bdd",
            command="pytest --no-ddtrace {cmdargs} tests/contrib/pytest_bdd/",
            pkgs={
                "msgpack": latest,
                "more_itertools": "<8.11.0",
                "pytest": "==7.4.4",
                "pytest-randomly": latest,
                "pytest-bdd": [
                    ">=4.0,<5.0",
                    # FIXME: add support for v6.1
                    ">=6.0,<6.1",
                ],
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "pytest-bdd": [
                            ">=4.0,<5.0",
                            # FIXME: add support for v6.1
                            ">=6.0,<6.1",
                        ]
                    },
                    venvs=[
                        Venv(
                            env={
                                "_DD_PYTEST_USE_LEGACY_PLUGIN": "true",
                            },
                        ),
                        Venv(
                            env={
                                "_DD_PYTEST_USE_LEGACY_PLUGIN": "false",
                            },
                        ),
                    ],
                ),
                Venv(
                    pys=select_pys(min_version="3.10", max_version="3.12"),
                    pkgs={
                        "pytest-bdd": [
                            # FIXME: add support for v6.1
                            ">=6.0,<6.1",
                        ]
                    },
                    venvs=[
                        Venv(
                            env={
                                "_DD_PYTEST_USE_LEGACY_PLUGIN": "true",
                            },
                        ),
                        Venv(
                            env={
                                "_DD_PYTEST_USE_LEGACY_PLUGIN": "false",
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="pytest_benchmark",
            pys=select_pys(min_version="3.8", max_version="3.12"),
            command="pytest {cmdargs} --no-ddtrace --no-cov tests/contrib/pytest_benchmark/",
            pkgs={
                "msgpack": latest,
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pkgs={
                        "pytest-benchmark": [
                            ">=3.1.0,<=4.0.0",
                        ]
                    },
                    env={
                        "_DD_PYTEST_USE_LEGACY_PLUGIN": "true",
                    },
                ),
                Venv(
                    pkgs={
                        "pytest-benchmark": [
                            ">=3.1.0,<=4.0.0",
                        ]
                    },
                    env={
                        "_DD_PYTEST_USE_LEGACY_PLUGIN": "false",
                    },
                ),
            ],
        ),
        Venv(
            name="grpc",
            command="python -m pytest -v {cmdargs} tests/contrib/grpc",
            pkgs={
                "googleapis-common-protos": latest,
                "pytest-randomly": latest,
            },
            venvs=[
                # Versions between 1.14 and 1.20 have known threading issues
                # See https://github.com/grpc/grpc/issues/18994
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"grpcio": ["~=1.34.0", latest]},
                ),
                Venv(
                    # grpcio added support for Python 3.10 in 1.41
                    # but the version contains some bugs resolved by https://github.com/grpc/grpc/pull/27635.
                    pys="3.10",
                    pkgs={"grpcio": ["~=1.42.0", latest]},
                ),
                Venv(
                    # grpcio added support for Python 3.11 in 1.49
                    pys="3.11",
                    pkgs={"grpcio": ["~=1.49.0", latest]},
                ),
                Venv(
                    # grpcio added support for Python 3.12 in 1.59
                    pys="3.12",
                    pkgs={
                        "grpcio": ["~=1.59.0", latest],
                        "pytest-asyncio": "==0.23.7",
                    },
                ),
                Venv(
                    # grpcio added support for Python 3.13 in 1.66.2
                    pys=select_pys(min_version="3.13"),
                    pkgs={
                        "grpcio": ["~=1.66.2", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="grpc:grpc_aio",
            command="python -m pytest {cmdargs} tests/contrib/grpc_aio",
            pkgs={
                "googleapis-common-protos": latest,
                "pytest-randomly": latest,
            },
            # grpc.aio support is broken and disabled by default
            env={"_DD_TRACE_GRPC_AIO_ENABLED": "true"},
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "grpcio": ["~=1.34.0", "~=1.59.0"],
                        "pytest-asyncio": "==0.23.7",
                    },
                ),
                Venv(
                    # grpcio added support for Python 3.10 in 1.41
                    # but the version contains some bugs resolved by https://github.com/grpc/grpc/pull/27635.
                    pys="3.10",
                    pkgs={
                        "grpcio": ["~=1.42.0", "~=1.59.0"],
                        "pytest-asyncio": "==0.23.7",
                    },
                ),
                Venv(
                    # grpcio added support for Python 3.11 in 1.49
                    pys="3.11",
                    pkgs={
                        "grpcio": ["~=1.49.0", "~=1.59.0"],
                        "pytest-asyncio": "==0.23.7",
                    },
                ),
            ],
        ),
        Venv(
            name="graphql:graphene",
            command="pytest {cmdargs} tests/contrib/graphene",
            pys=select_pys(min_version="3.8"),
            pkgs={
                "graphene": ["~=3.0.0", latest],
                "pytest-asyncio": "==0.21.1",
                "graphql-relay": latest,
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="graphql",
            command="pytest {cmdargs} tests/contrib/graphql",
            pys=select_pys(min_version="3.8"),
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "graphql-core": ["~=3.2.0", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="rq",
            command="pytest {cmdargs} tests/contrib/rq",
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys="3.8",
                    pkgs={
                        "rq": [
                            "~=1.8.0",
                            "~=1.10.0",
                            latest,
                        ],
                        # https://github.com/rq/rq/issues/1469 rq [1.0,1.8] is incompatible with click 8.0+
                        "click": "==7.1.2",
                    },
                ),
                Venv(
                    # rq added support for Python 3.9 in 1.8.1
                    pys="3.9",
                    pkgs={
                        "rq": [
                            "~=1.8.1",
                            "~=1.10.0",
                            latest,
                        ],
                        # https://github.com/rq/rq/issues/1469 rq [1.0,1.8] is incompatible with click 8.0+
                        "click": "==7.1.2",
                    },
                ),
                Venv(
                    # rq added support for Python 3.10/3.11 in 1.13
                    pys=select_pys(min_version="3.10"),
                    pkgs={"rq": latest},
                ),
            ],
        ),
        Venv(
            name="httpx",
            pys=select_pys(min_version="3.8"),
            command="pytest {cmdargs} tests/contrib/httpx",
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
                "httpx": [
                    "~=0.17.0",
                    "~=0.23.0",
                    latest,
                ],
            },
        ),
        Venv(
            name="urllib3",
            command="pytest {cmdargs} tests/contrib/urllib3",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    # Support added for Python 3.8 in 1.25.0
                    pys="3.8",
                    pkgs={"urllib3": ["==1.25.0", latest]},
                ),
                Venv(
                    # Support added for Python 3.9 in 1.25.8
                    pys="3.9",
                    pkgs={"urllib3": ["==1.25.8", latest]},
                ),
                Venv(
                    # Support added for Python 3.10 in 1.26.6
                    pys="3.10",
                    pkgs={"urllib3": ["==1.26.6", latest]},
                ),
                Venv(
                    # Support added for Python 3.11 in 1.26.8
                    pys="3.11",
                    pkgs={"urllib3": ["==1.26.8", latest]},
                ),
                Venv(
                    # Support added for Python 3.12 in 2.0.0
                    pys=select_pys(min_version="3.12"),
                    pkgs={"urllib3": ["==2.0.0", latest]},
                ),
            ],
        ),
        Venv(
            name="cassandra",
            pys="3.8",  # see https://github.com/r4fek/django-cassandra-engine/issues/104
            pkgs={"cassandra-driver": ["~=3.24.0", latest], "pytest-randomly": latest},
            command="pytest {cmdargs} tests/contrib/cassandra",
        ),
        Venv(
            name="algoliasearch",
            command="pytest {cmdargs} tests/contrib/algoliasearch",
            pkgs={"urllib3": "~=1.26.15", "pytest-randomly": latest},
            venvs=[
                Venv(
                    pys="3.8",
                    pkgs={"algoliasearch": ["~=2.5", "~=2.6"]},
                ),
                Venv(
                    # algoliasearch added support for Python 3.9, 3.10, 3.11 in 3.0
                    pys=select_pys(min_version="3.9"),
                    pkgs={"algoliasearch": "~=2.6"},
                ),
            ],
        ),
        Venv(
            name="aiopg",
            command="pytest {cmdargs} tests/contrib/aiopg",
            pys=select_pys(min_version="3.8", max_version="3.9"),
            pkgs={
                "sqlalchemy": latest,
                "aiopg": "~=0.16.0",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "aiopg": ["~=1.0", "~=1.4.0"],
                    },
                ),
            ],
        ),
        Venv(
            name="aiohttp",
            command="pytest {cmdargs} tests/contrib/aiohttp",
            pkgs={
                "pytest-aiohttp": [latest],
                "pytest-randomly": latest,
                "aiohttp": [
                    "~=3.7",
                    latest,
                ],
                "yarl": "~=1.0",
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "pytest-asyncio": ["==0.23.7"],
                    },
                ),
            ],
        ),
        Venv(
            name="aiohttp_jinja2",
            command="pytest {cmdargs} tests/contrib/aiohttp_jinja2",
            pkgs={
                "pytest-aiohttp": [latest],
                "pytest-randomly": latest,
                "aiohttp": [
                    "~=3.7",
                    latest,
                ],
                "aiohttp_jinja2": [
                    "~=1.5.0",
                    latest,
                ],
                "jinja2": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "pytest-asyncio": ["==0.23.7"],
                    },
                ),
            ],
        ),
        Venv(
            name="jinja2",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "jinja2": "~=2.10.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "jinja2": ["~=3.0.0", latest],
                    },
                ),
            ],
            command="pytest {cmdargs} tests/contrib/jinja2",
        ),
        Venv(
            name="rediscluster",
            command="pytest {cmdargs} tests/contrib/rediscluster",
            pkgs={"pytest-randomly": latest},
            venvs=[
                Venv(pys=select_pys(max_version="3.11"), pkgs={"redis-py-cluster": [">=2.0,<2.1", latest]}),
            ],
        ),
        Venv(
            name="redis",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    command="pytest {cmdargs} tests/contrib/redis",
                    pkgs={
                        "redis": [
                            "~=4.1",
                            "~=4.3",
                            "==5.0.1",
                        ],
                    },
                    venvs=[
                        Venv(
                            pys=select_pys(min_version="3.8", max_version="3.10"),
                            pkgs={
                                "pytest-asyncio": "==0.23.7",
                            },
                        ),
                    ],
                ),
                Venv(
                    # redis added support for Python 3.11 in 4.3
                    pys="3.11",
                    command="pytest {cmdargs} tests/contrib/redis",
                    pkgs={
                        "redis": ["~=4.3", "==5.0.1"],
                        "pytest-asyncio": "==0.23.7",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.12"),
                    command="pytest {cmdargs} tests/contrib/redis",
                    pkgs={
                        "redis": latest,
                        "pytest-asyncio": "==0.23.7",
                    },
                ),
            ],
        ),
        Venv(
            name="aredis",
            pys=select_pys(min_version="3.8", max_version="3.9"),
            command="pytest {cmdargs} tests/contrib/aredis",
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "aredis": latest,
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="avro",
            pys=select_pys(min_version="3.8"),
            command="pytest {cmdargs} tests/contrib/avro",
            pkgs={
                "avro": latest,
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="protobuf",
            command="pytest {cmdargs} tests/contrib/protobuf",
            pys=select_pys(min_version="3.8"),
            pkgs={
                "protobuf": latest,
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="yaaredis",
            command="pytest {cmdargs} tests/contrib/yaaredis",
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"yaaredis": ["~=2.0.0", latest]},
                ),
                Venv(
                    # yaaredis added support for Python 3.10 in 3.0
                    pys="3.10",
                    pkgs={"yaaredis": latest},
                ),
            ],
        ),
        Venv(
            name="sanic",
            command="pytest {cmdargs} tests/contrib/sanic",
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
                "requests": latest,
                "websockets": "<11.0",
            },
            venvs=[
                Venv(
                    # sanic added support for Python 3.9 in 20.12
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "sanic": "~=20.12",
                        "pytest-sanic": "~=1.6.2",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "sanic": [
                            "~=21.3",
                            "~=21.12",
                        ],
                        "sanic-testing": "~=0.8.3",
                    },
                ),
                Venv(
                    # sanic added support for Python 3.10 in 21.12.0
                    pys="3.10",
                    pkgs={
                        "sanic": "~=21.12.0",
                        "sanic-testing": "~=0.8.3",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.10"),
                    pkgs={
                        "sanic": ["~=22.3", "~=22.12"],
                        "sanic-testing": "~=22.3.0",
                    },
                ),
                Venv(
                    # sanic added support for Python 3.11 in 22.12.0
                    pys="3.11",
                    pkgs={
                        "sanic": ["~=22.12.0", latest],
                        "sanic-testing": "~=22.3.0",
                    },
                ),
                Venv(
                    pys="3.12",
                    pkgs={
                        "sanic": [latest],
                        "sanic-testing": "~=22.3.0",
                    },
                ),
            ],
        ),
        Venv(
            name="snowflake",
            command="pytest {cmdargs} tests/contrib/snowflake",
            pkgs={
                "responses": "~=0.16.0",
                "cryptography": "<39",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys="3.8",
                    pkgs={"snowflake-connector-python": ["~=2.3.0", "~=2.9.0", latest]},
                ),
                Venv(
                    # snowflake-connector-python added support for Python 3.9 in 2.4.0
                    pys="3.9",
                    pkgs={"snowflake-connector-python": ["~=2.4.0", "~=2.9.0", latest]},
                ),
                Venv(
                    # snowflake-connector-python added support for Python 3.10 in 2.7.2
                    pys="3.10",
                    pkgs={"snowflake-connector-python": ["~=2.7.2", "~=2.9.0", latest]},
                ),
                Venv(
                    # snowflake-connector-python added support for Python 3.11 in 3.0
                    pys=select_pys(min_version="3.11"),
                    pkgs={"snowflake-connector-python": [latest]},
                ),
            ],
        ),
        Venv(
            pys=["3"],
            name="reno",
            pkgs={
                "reno": latest,
            },
            command="reno {cmdargs}",
        ),
        Venv(
            name="asyncpg",
            command="pytest {cmdargs} tests/contrib/asyncpg",
            pkgs={
                "pytest-asyncio": "~=0.21.1",
                "pytest-randomly": latest,
            },
            venvs=[
                # our test_asyncpg.py uses `yield` in an async function and is not compatible with Python 3.5
                Venv(
                    pys="3.8",
                    pkgs={"asyncpg": ["~=0.23", latest]},
                ),
                Venv(
                    # asyncpg added support for Python 3.9 in 0.22
                    pys="3.9",
                    pkgs={"asyncpg": ["~=0.23.0", latest]},
                ),
                Venv(
                    # asyncpg added support for Python 3.10 in 0.24
                    pys="3.10",
                    pkgs={"asyncpg": ["~=0.24.0", latest]},
                ),
                Venv(
                    # asyncpg added support for Python 3.11 in 0.27
                    pys="3.11",
                    pkgs={"asyncpg": ["~=0.27", latest]},
                ),
                Venv(
                    pys=select_pys(min_version="3.12"),
                    pkgs={"asyncpg": [latest]},
                ),
            ],
        ),
        Venv(
            name="futures",
            command="pytest {cmdargs} tests/contrib/futures",
            pkgs={
                "gevent": latest,
                "pytest-randomly": latest,
            },
            pys=select_pys(),
        ),
        Venv(
            name="sqlite3",
            command="pytest {cmdargs} tests/contrib/sqlite3",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                # sqlite3 is tied to the Python version and is not installable via pip
                # To test a range of versions without updating Python, we use Linux only pysqlite3-binary package
                # Remove pysqlite3-binary on Python 3.9+ locally on non-linux machines
                Venv(pys=select_pys(min_version="3.9", max_version="3.12"), pkgs={"pysqlite3-binary": [latest]}),
                Venv(pys=select_pys(max_version="3.8"), pkgs={"importlib-metadata": latest}),
            ],
        ),
        Venv(
            name="dbapi",
            command="pytest {cmdargs} tests/contrib/dbapi",
            pkgs={
                "pytest-randomly": latest,
            },
            pys=select_pys(),
            env={
                "DD_CIVISIBILITY_ITR_ENABLED": "0",
                "DD_IAST_REQUEST_SAMPLING": "100",  # Override default 30% to analyze all IAST requests
            },
        ),
        Venv(
            name="dbapi_async",
            command="pytest {cmdargs} tests/contrib/dbapi_async",
            env={
                "DD_CIVISIBILITY_ITR_ENABLED": "0",
                "DD_IAST_REQUEST_SAMPLING": "100",  # Override default 30% to analyze all IAST requests
            },
            pkgs={
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(pys=select_pys(min_version="3.8", max_version="3.10")),
                Venv(pys=select_pys(min_version="3.11"), pkgs={"attrs": latest}),
            ],
        ),
        Venv(
            name="dogpile_cache",
            command="pytest {cmdargs} tests/contrib/dogpile_cache",
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.10"),
                    pkgs={
                        "dogpile.cache": [
                            "~=0.9",
                            "~=1.0",
                            latest,
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.11"),
                    pkgs={
                        "dogpile.cache": [
                            "~=0.9",
                            "~=1.0",
                            "~=1.1",
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="consul",
            pys=select_pys(max_version="3.12"),
            command="pytest {cmdargs} tests/contrib/consul",
            pkgs={
                "python-consul": [
                    ">=1.1,<1.2",
                    latest,
                ],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="opentelemetry",
            command="pytest {cmdargs} tests/opentelemetry",
            pys=select_pys(min_version="3.8"),
            # DD_TRACE_OTEL_ENABLED must be set to true before ddtrace is imported
            # and ddtrace (ddtrace.config specifically) must be imported before opentelemetry.
            # If this order is violated otel and datadog spans will not be interoperable.
            env={"DD_TRACE_OTEL_ENABLED": "true"},
            pkgs={
                "pytest-randomly": latest,
                "pytest-asyncio": "==0.21.1",
                # Ensure we test against version of opentelemetry-api that broke compatibility with ddtrace
                "opentelemetry-api": ["~=1.0.0", "~=1.15.0", "~=1.26.0", latest],
                "opentelemetry-instrumentation-flask": latest,
                "markupsafe": "==2.0.1",
                "flask": latest,
                "gevent": latest,
                "requests": "==2.28.1",  # specific version expected by tests
            },
        ),
        Venv(
            name="asyncio",
            command="pytest {cmdargs} tests/contrib/asyncio",
            pys=select_pys(),
            pkgs={
                "pytest-randomly": latest,
                "pytest-asyncio": "==0.21.1",
            },
        ),
        Venv(
            name="openai",
            command="pytest {cmdargs} tests/contrib/openai",
            pkgs={
                "vcrpy": "==4.2.1",
                "urllib3": "~=1.26",
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.11"),
                    pkgs={
                        "openai[embeddings,datalib]": ["==1.0.0", "==1.30.1"],
                        "pillow": "==9.5.0",
                        "httpx": "==0.27.2",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "openai": latest,
                        "tiktoken": latest,
                        "pillow": latest,
                    },
                    env={"TIKTOKEN_AVAILABLE": "True"},
                ),
            ],
        ),
        Venv(
            name="opentracer",
            pkgs={"opentracing": latest, "pytest-randomly": latest},
            venvs=[
                Venv(
                    pys=select_pys(),
                    command="pytest {cmdargs} tests/opentracer/core",
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    command="pytest {cmdargs} tests/opentracer/test_tracer_asyncio.py",
                    pkgs={"pytest-asyncio": "==0.21.1"},
                ),
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.11"),
                    command="pytest {cmdargs} tests/opentracer/test_tracer_tornado.py",
                    # TODO: update opentracing tests to be compatible with Tornado v6.
                    # https://github.com/opentracing/opentracing-python/issues/136
                    pkgs={
                        "tornado": ["~=4.5.0", "~=5.1.0"],
                    },
                ),
                Venv(
                    command="pytest {cmdargs} tests/opentracer/test_tracer_gevent.py",
                    venvs=[
                        Venv(
                            pys="3.8",
                            pkgs={
                                "gevent": latest,
                                "greenlet": latest,
                            },
                        ),
                        Venv(
                            pys="3.9",
                            pkgs={"gevent": latest, "greenlet": latest},
                        ),
                        Venv(
                            pys="3.10",
                            pkgs={"gevent": latest},
                        ),
                        Venv(
                            pys="3.11",
                            pkgs={"gevent": latest},
                        ),
                        Venv(
                            pys="3.12",
                            pkgs={"gevent": "~=23.9.0"},
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="pyodbc",
            command="pytest {cmdargs} tests/contrib/pyodbc",
            pkgs={"pytest-randomly": latest},
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.8"),
                    pkgs={"pyodbc": ["~=4.0.31", latest]},
                ),
                Venv(
                    # pyodbc added support for Python 3.9/3.10 in 4.0.34
                    pys=select_pys(min_version="3.9", max_version="3.10"),
                    pkgs={"pyodbc": ["~=4.0.34", latest]},
                ),
                Venv(
                    # pyodbc added support for Python 3.11 in 4.0.35
                    pys=select_pys(min_version="3.11"),
                    pkgs={"pyodbc": [latest]},
                ),
            ],
        ),
        Venv(
            name="pylibmc",
            command="pytest {cmdargs} tests/contrib/pylibmc",
            pkgs={"pytest-randomly": latest},
            venvs=[
                Venv(
                    # pylibmc added support for Python 3.8/3.9/3.10 in 1.6.2
                    pys=select_pys(min_version="3.8", max_version="3.10"),
                    pkgs={
                        "pylibmc": ["~=1.6.2", latest],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.11"),
                    pkgs={
                        "pylibmc": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="kombu",
            command="pytest {cmdargs} tests/contrib/kombu",
            pkgs={"pytest-randomly": latest},
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "kombu": [">=4.6,<4.7", ">=5.0,<5.1", latest],
                    },
                ),
                Venv(
                    # kombu added support for Python 3.10 in 5.2.1
                    pys=select_pys(min_version="3.10", max_version="3.11"),
                    pkgs={
                        "kombu": [">=5.2,<5.3", latest],
                    },
                ),
                Venv(pys=select_pys(min_version="3.12"), pkgs={"kombu": latest}),
            ],
        ),
        Venv(
            name="tornado",
            command="python -m pytest {cmdargs} tests/contrib/tornado",
            pkgs={"pytest-randomly": latest},
            venvs=[
                Venv(
                    # tornado added support for Python 3.8/3.9 in 6.1
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"tornado": ["~=6.1", "~=6.2"]},
                ),
                Venv(
                    # tornado added support for Python 3.10 in 6.2
                    pys=select_pys(min_version="3.10", max_version="3.12"),
                    pkgs={"tornado": ["==6.2", "==6.3.1"]},
                ),
                Venv(
                    # tornado fixed a bug affecting 3.13 in 6.4.1
                    pys=select_pys(min_version="3.13"),
                    pkgs={"tornado": "==6.4.1"},
                ),
            ],
        ),
        Venv(
            name="mysqldb",
            command="pytest {cmdargs} tests/contrib/mysqldb",
            pkgs={"pytest-randomly": latest},
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"mysqlclient": ["~=2.0", "~=2.1", latest]},
                ),
                Venv(
                    # mysqlclient added support for Python 3.9/3.10 in 2.1
                    pys=select_pys(min_version="3.9", max_version="3.12"),
                    pkgs={"mysqlclient": ["~=2.1", latest]},
                ),
                Venv(
                    pys=select_pys(min_version="3.13"),
                    pkgs={"mysqlclient": "==2.2.6"},
                ),
            ],
        ),
        Venv(
            name="openai_agents",
            command="pytest {cmdargs} tests/contrib/openai_agents",
            pys=select_pys(min_version="3.9", max_version="3.13"),
            pkgs={
                "vcrpy": latest,
                "pytest-asyncio": latest,
                "openai": latest,
                "openai-agents": latest,
            },
        ),
        Venv(
            name="langchain",
            command="pytest -v {cmdargs} tests/contrib/langchain",
            pkgs={
                "vcrpy": "==7.0.0",
                "pytest-asyncio": "==0.23.7",
                "tiktoken": latest,
                "huggingface-hub": latest,
                "ai21": latest,
                "exceptiongroup": latest,
                "psutil": latest,
                "pytest-randomly": "==3.10.1",
                "numexpr": "==2.8.5",
                "greenlet": "==3.0.3",
                "respx": latest,
            },
            venvs=[
                Venv(
                    pkgs={
                        "langchain": "==0.1.20",
                        "langchain-community": "==0.0.38",
                        "langchain-core": "==0.1.52",
                        "langchain-openai": "==0.1.6",
                        "langchain-anthropic": "==0.1.11",
                        "langchain-pinecone": "==0.1.0",
                        "langchain-aws": "==0.1.3",
                        "langchain-cohere": "==0.1.4",
                        "openai": "==1.30.3",
                        "pinecone-client": latest,
                        "botocore": "==1.34.51",
                        "boto3": "==1.34.51",
                        "cohere": "==5.4.0",
                        "anthropic": "==0.26.0",
                        "faiss-cpu": "==1.8.0",
                    },
                    pys=select_pys(min_version="3.9", max_version="3.11"),
                ),
                Venv(
                    pkgs={
                        "langchain": "==0.2.0",
                        "langchain-core": "==0.2.0",
                        "langchain-openai": latest,
                        "langchain-pinecone": latest,
                        "langchain-anthropic": latest,
                        "langchain-aws": latest,
                        "langchain-cohere": latest,
                        "openai": latest,
                        "pinecone-client": latest,
                        "botocore": "==1.34.51",
                        "boto3": "==1.34.51",
                        "cohere": latest,
                        "anthropic": "==0.26.0",
                    },
                    pys=select_pys(min_version="3.9", max_version="3.12"),
                ),
                Venv(
                    pkgs={
                        "langchain": latest,
                        "langchain-community": latest,
                        "langchain-core": latest,
                        "langchain-openai": latest,
                        "langchain-pinecone": latest,
                        "langchain-anthropic": latest,
                        "langchain-aws": latest,
                        "langchain-cohere": latest,
                        "openai": latest,
                        "pinecone-client": latest,
                        "botocore": latest,
                        "cohere": latest,
                    },
                    pys=select_pys(min_version="3.9", max_version="3.12"),
                ),
            ],
        ),
        Venv(
            name="langgraph",
            command="pytest {cmdargs} tests/contrib/langgraph",
            pys=select_pys(min_version="3.9"),
            pkgs={
                "pytest-asyncio": latest,
                "langgraph": "~=0.2.60",
            },
        ),
        Venv(
            name="litellm",
            command="pytest {cmdargs} tests/contrib/litellm",
            pys=select_pys(min_version="3.9"),
            pkgs={
                "litellm": "==1.65.4",
                "vcrpy": latest,
                "pytest-asyncio": latest,
                "botocore": latest,
                "boto3": latest,
                "openai": "==1.68.2",
            },
        ),
        Venv(
            name="anthropic",
            command="pytest {cmdargs} tests/contrib/anthropic",
            pys=select_pys(min_version="3.8", max_version="3.12"),
            pkgs={
                "pytest-asyncio": latest,
                "vcrpy": latest,
                "anthropic": [latest, "~=0.28"],
            },
        ),
        Venv(
            name="google_generativeai",
            command="pytest {cmdargs} tests/contrib/google_generativeai",
            pys=select_pys(min_version="3.9", max_version="3.12"),
            pkgs={
                "pytest-asyncio": latest,
                "google-generativeai": [latest],
                "pillow": latest,
                "google-ai-generativelanguage": [latest],
                "vertexai": [latest],
            },
        ),
        Venv(
            name="vertexai",
            command="pytest {cmdargs} tests/contrib/vertexai",
            pys=select_pys(min_version="3.9", max_version="3.12"),
            pkgs={
                "pytest-asyncio": latest,
                "vertexai": [latest],
                "google-ai-generativelanguage": [latest],
            },
        ),
        Venv(
            name="crewai",
            command="pytest {cmdargs} tests/contrib/crewai",
            pys=select_pys(min_version="3.10", max_version="3.12"),
            pkgs={
                "pytest-asyncio": latest,
                "openai": latest,
                "crewai": [latest],
                "vcrpy": "==7.0.0",
            },
        ),
        Venv(
            name="logbook",
            pys=select_pys(),
            command="pytest {cmdargs} tests/contrib/logbook",
            pkgs={
                "logbook": ["~=1.0.0", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="loguru",
            pys=select_pys(),
            command="pytest {cmdargs} tests/contrib/loguru",
            pkgs={
                "loguru": ["~=0.4.0", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="molten",
            command="pytest {cmdargs} tests/contrib/molten",
            pys=select_pys(),
            pkgs={
                "cattrs": ["<23.1.1"],
                "molten": [">=1.0,<1.1", latest],
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="gunicorn",
            command="pytest {cmdargs} tests/contrib/gunicorn",
            pkgs={
                "requests": latest,
                "gevent": latest,
                "gunicorn": ["==20.0.4", latest],
                "pytest-randomly": latest,
            },
            pys=select_pys(),
        ),
        Venv(
            name="kafka",
            env={
                "_DD_TRACE_STATS_WRITER_INTERVAL": "1000000000",
                "DD_DATA_STREAMS_ENABLED": "true",
            },
            pkgs={
                "pytest-randomly": latest,
            },
            venvs=[
                Venv(
                    command="pytest {cmdargs} -vv tests/contrib/kafka",
                    venvs=[
                        Venv(
                            pys=select_pys(min_version="3.8", max_version="3.10"),
                            pkgs={"confluent-kafka": ["~=1.9.2", latest]},
                        ),
                        # confluent-kafka added support for Python 3.11 in 2.0.2
                        Venv(pys=select_pys(min_version="3.11"), pkgs={"confluent-kafka": latest}),
                    ],
                ),
            ],
        ),
        Venv(
            name="aws_lambda",
            command="pytest --no-ddtrace {cmdargs} tests/contrib/aws_lambda",
            pys=select_pys(min_version="3.8", max_version="3.13"),
            pkgs={
                "boto3": latest,
                "datadog-lambda": [">=6.105.0", latest],
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="azure_functions",
            command="pytest {cmdargs} tests/contrib/azure_functions",
            pys=select_pys(min_version="3.8", max_version="3.11"),
            pkgs={
                "azure.functions": latest,
                "requests": latest,
            },
        ),
        Venv(
            name="sourcecode",
            command="pytest {cmdargs} tests/sourcecode",
            pys=select_pys(),
            pkgs={
                "setuptools": latest,
                "pytest-randomly": latest,
            },
        ),
        Venv(
            name="ci_visibility",
            command="pytest --no-ddtrace {cmdargs} tests/ci_visibility",
            pkgs={
                "msgpack": latest,
                "coverage": latest,
                "pytest-randomly": latest,
                "gevent": latest,
            },
            env={
                "DD_AGENT_PORT": "9126",
            },
            venvs=[
                # Python 3.8
                Venv(
                    pys=["3.8"],
                    pkgs={"greenlet": "==3.1.0"},
                    # Prevent segfaults from zope.interface c optimizations
                    env={"PURE_PYTHON": "1"},
                ),
                # Python 3.9-3.12
                Venv(
                    pys=select_pys(min_version="3.9", max_version="3.12"),
                ),
            ],
        ),
        Venv(
            name="subprocess",
            command="pytest {cmdargs} --no-cov tests/contrib/subprocess",
            pkgs={
                "pytest-randomly": latest,
            },
            pys=select_pys(),
        ),
        Venv(
            name="integration_registry",
            command="pytest {cmdargs} tests/contrib/integration_registry",
            pkgs={
                "riot": "==0.20.1",
                "pytest-randomly": latest,
                "pytest-asyncio": "==0.23.7",
                "PyYAML": latest,
                "jsonschema": latest,
            },
            # we only need to run this on one version of Python
            pys=["3.13"],
        ),
        Venv(
            name="llmobs",
            command="pytest {cmdargs} tests/llmobs",
            pkgs={
                "vcrpy": latest,
                "pytest-asyncio": "==0.21.1",
                "ragas": "==0.1.21",
                "langchain": latest,
            },
            pys=select_pys(min_version="3.8"),
        ),
        Venv(
            name="valkey",
            command="pytest {cmdargs} tests/contrib/valkey",
            pkgs={
                "valkey": latest,
                "pytest-randomly": latest,
                "pytest-asyncio": "==0.23.7",
            },
            pys=select_pys(min_version="3.8"),
        ),
        Venv(
            name="profile",
            # NB riot commands that use this Venv must include --pass-env to work properly
            command="python -m tests.profiling.run pytest -v --no-cov --capture=no --benchmark-disable {cmdargs} tests/profiling",  # noqa: E501
            env={
                "DD_PROFILING_ENABLE_ASSERTS": "1",
                "DD_PROFILING_STACK_V2_ENABLED": "0",
                "DD_PROFILING__FORCE_LEGACY_EXPORTER": "1",
                "CPUCOUNT": "12",
            },
            pkgs={
                "gunicorn": latest,
                #
                # pytest-benchmark depends on cpuinfo which dropped support for Python<=3.6 in 9.0
                # See https://github.com/workhorsy/py-cpuinfo/issues/177
                "pytest-benchmark": latest,
                "py-cpuinfo": "~=8.0.0",
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
            },
            venvs=[
                # Python 3.8 + 3.9
                Venv(
                    pys=["3.8", "3.9"],
                    pkgs={"uwsgi": latest},
                    venvs=[
                        Venv(
                            pkgs={
                                "protobuf": [">3", latest],
                            },
                        ),
                        # Gevent
                        Venv(
                            env={
                                "DD_PROFILE_TEST_GEVENT": "1",
                            },
                            pkgs={
                                "gunicorn[gevent]": latest,
                                "gevent": latest,
                            },
                        ),
                    ],
                ),
                # Python 3.10
                Venv(
                    pys="3.10",
                    pkgs={"uwsgi": latest},
                    venvs=[
                        Venv(
                            pkgs={
                                "protobuf": [">3", latest],
                            },
                        ),
                        # Gevent
                        Venv(
                            env={
                                "DD_PROFILE_TEST_GEVENT": "1",
                            },
                            pkgs={
                                "gunicorn[gevent]": latest,
                            },
                            venvs=[
                                Venv(
                                    pkgs={
                                        "gevent": latest,
                                        "greenlet": latest,
                                    }
                                ),
                                Venv(
                                    pkgs={"gevent": latest},
                                ),
                            ],
                        ),
                    ],
                ),
                # Python >= 3.11
                Venv(
                    pys=select_pys("3.11", "3.13"),
                    pkgs={"uwsgi": latest},
                    venvs=[
                        Venv(
                            pkgs={
                                "protobuf": ["==4.22.0", latest],
                            },
                        ),
                        # Gevent
                        Venv(
                            env={
                                "DD_PROFILE_TEST_GEVENT": "1",
                            },
                            pkgs={"gunicorn[gevent]": latest, "gevent": latest},
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="profile-v2",
            # NB riot commands that use this Venv must include --pass-env to work properly
            command="python -m tests.profiling.run pytest -v --no-cov --capture=no --benchmark-disable {cmdargs} tests/profiling_v2",  # noqa: E501
            env={
                "DD_PROFILING_ENABLE_ASSERTS": "1",
                "DD_PROFILING_EXPORT_LIBDD_ENABLED": "1",
                "CPUCOUNT": "12",
            },
            pkgs={
                "gunicorn": latest,
                "jsonschema": latest,
                "lz4": latest,
                "pytest-cpp": latest,
                #
                # pytest-benchmark depends on cpuinfo which dropped support for Python<=3.6 in 9.0
                # See https://github.com/workhorsy/py-cpuinfo/issues/177
                "pytest-benchmark": latest,
                "py-cpuinfo": "~=8.0.0",
                "pytest-asyncio": "==0.21.1",
                "pytest-randomly": latest,
            },
            venvs=[
                # Python 3.8 + 3.9
                Venv(
                    pys=["3.8", "3.9"],
                    pkgs={"uwsgi": latest},
                    venvs=[
                        Venv(
                            pkgs={
                                "protobuf": ["==3.19.0", latest],
                            },
                        ),
                        # Gevent
                        Venv(
                            env={
                                "DD_PROFILE_TEST_GEVENT": "1",
                            },
                            pkgs={
                                "gunicorn[gevent]": latest,
                                "gevent": latest,
                            },
                        ),
                    ],
                ),
                # Python 3.10
                Venv(
                    pys="3.10",
                    pkgs={"uwsgi": latest},
                    venvs=[
                        Venv(
                            pkgs={
                                "protobuf": ["==3.19.0", latest],
                            },
                        ),
                        # Gevent
                        Venv(
                            env={
                                "DD_PROFILE_TEST_GEVENT": "1",
                            },
                            pkgs={
                                "gunicorn[gevent]": latest,
                            },
                            venvs=[
                                Venv(
                                    pkgs={
                                        "gevent": latest,
                                        "greenlet": latest,
                                    }
                                ),
                                Venv(
                                    pkgs={"gevent": latest},
                                ),
                            ],
                        ),
                    ],
                ),
                # Python >= 3.11
                Venv(
                    pys=select_pys("3.11", "3.13"),
                    pkgs={"uwsgi": latest},
                    venvs=[
                        Venv(
                            pkgs={
                                "protobuf": ["==4.22.0", latest],
                            },
                        ),
                        # Gevent
                        Venv(
                            env={
                                "DD_PROFILE_TEST_GEVENT": "1",
                            },
                            pkgs={"gunicorn[gevent]": latest, "gevent": latest},
                        ),
                    ],
                ),
            ],
        ),
    ],
)
