# type: ignore
import logging
from typing import List  # noqa
from typing import Tuple  # noqa

from riot import Venv


logger = logging.getLogger(__name__)
latest = ""


SUPPORTED_PYTHON_VERSIONS = [
    (2, 7),
    (3, 5),
    (3, 6),
    (3, 7),
    (3, 8),
    (3, 9),
    (3, 10),
    (3, 11),
]  # type: List[Tuple[int, int]]


def version_to_str(version):
    # type: (Tuple[int, int]) -> str
    """Convert a Python version tuple to a string

    >>> version_to_str((2, 7))
    '2.7'
    >>> version_to_str((3, 5))
    '3.5'
    >>> version_to_str((3, 1))
    '3.1'
    >>> version_to_str((3, 10))
    '3.10'
    >>> version_to_str((3, 11))
    '3.11'
    >>> version_to_str((3, ))
    '3'
    """
    return ".".join(str(p) for p in version)


def str_to_version(version):
    # type: (str) -> Tuple[int, int]
    """Convert a Python version string to a tuple

    >>> str_to_version("2.7")
    (2, 7)
    >>> str_to_version("3.5")
    (3, 5)
    >>> str_to_version("3.1")
    (3, 1)
    >>> str_to_version("3.10")
    (3, 10)
    >>> str_to_version("3.11")
    (3, 11)
    >>> str_to_version("3")
    (3,)
    """
    return tuple(int(p) for p in version.split("."))


MIN_PYTHON_VERSION = version_to_str(min(SUPPORTED_PYTHON_VERSIONS))
MAX_PYTHON_VERSION = version_to_str(max(SUPPORTED_PYTHON_VERSIONS))


def select_pys(min_version=MIN_PYTHON_VERSION, max_version=MAX_PYTHON_VERSION):
    # type: (str, str) -> List[str]
    """Helper to select python versions from the list of versions we support

    >>> select_pys()
    ['2.7', '3.5', '3.6', '3.7', '3.8', '3.9', '3.10', '3.11']
    >>> select_pys(min_version='3')
    ['3.5', '3.6', '3.7', '3.8', '3.9', '3.10', '3.11']
    >>> select_pys(max_version='3')
    ['2.7']
    >>> select_pys(min_version='3.5', max_version='3.8')
    ['3.5', '3.6', '3.7', '3.8']
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
        "DD_TESTING_RAISE": "1",
        "DD_REMOTE_CONFIGURATION_ENABLED": "false",
    },
    venvs=[
        Venv(
            pys=["3"],
            pkgs={
                "black": "==21.4b2",
                "isort": [latest],
                # See https://github.com/psf/black/issues/2964 for incompatibility with click==8.1.0
                "click": "<8.1.0",
            },
            venvs=[
                Venv(
                    name="fmt",
                    command="isort . && black .",
                ),
                Venv(
                    name="black",
                    command="black {cmdargs}",
                ),
                Venv(
                    name="isort",
                    command="isort {cmdargs}",
                ),
            ],
        ),
        Venv(
            pys=["3"],
            pkgs={
                "flake8": ">=3.8,<3.9",
                "flake8-blind-except": latest,
                "flake8-builtins": latest,
                "flake8-docstrings": latest,
                "flake8-logging-format": latest,
                "flake8-rst-docstrings": latest,
                "flake8-isort": latest,
                "pygments": latest,
            },
            venvs=[
                Venv(
                    name="flake8",
                    command="flake8 {cmdargs}",
                ),
            ],
        ),
        Venv(
            pys=["3"],
            name="mypy",
            command="mypy {cmdargs}",
            create=True,
            pkgs={
                "mypy": "==0.991",
                "envier": "==0.4.0",
                "types-attrs": "==19.1.0",
                "types-docutils": "==0.19.1.1",
                "types-protobuf": "==3.20.4.5",
                "types-PyYAML": "==6.0.12.2",
                "types-setuptools": "==65.6.0.0",
                "types-six": "==1.16.21.4",
            },
        ),
        Venv(
            pys=["3"],
            pkgs={"codespell": "==2.1.0"},
            venvs=[
                Venv(
                    name="codespell",
                    command='codespell --skip="ddwaf.h" ddtrace/ tests/',
                ),
                Venv(
                    name="hook-codespell",
                    command="codespell {cmdargs}",
                ),
            ],
        ),
        Venv(
            pys=["3"],
            pkgs={"slotscheck": latest},
            venvs=[
                Venv(
                    name="slotscheck",
                    command="python -m slotscheck -v ddtrace/",
                ),
            ],
        ),
        Venv(
            pys=["3"],
            pkgs={"ddapm-test-agent": ">=1.2.0"},
            venvs=[
                Venv(
                    name="snapshot-fmt",
                    command="ddapm-test-agent-fmt {cmdargs} tests/snapshots/",
                ),
            ],
        ),
        Venv(
            pys=["3"],
            name="riot-helpers",
            # DEV: pytest really doesn't want to execute only `riotfile.py`, call doctest directly
            command="python -m doctest {cmdargs} riotfile.py",
            pkgs={"riot": latest},
        ),
        Venv(
            pys=["3"],
            name="scripts",
            command="python -m doctest {cmdargs} scripts/get-target-milestone.py",
        ),
        Venv(
            name="docs",
            pys=["3.10"],
            pkgs={
                "reno[sphinx]": "~=3.5.0",
                "sphinx": "~=4.0",
                "sphinxcontrib-spelling": "==7.7.0",
                "PyEnchant": "==3.2.2",
                "sphinx-copybutton": "==0.5.1",
                "furo": latest,
            },
            command="scripts/build-docs",
        ),
        Venv(
            name="appsec",
            pys=select_pys(),
            command="pytest {cmdargs} tests/appsec",
            pkgs={
                "pycryptodome": latest,
                "cryptography": latest,
                "astunparse": latest,
            },
        ),
        Venv(
            pys=select_pys(),
            pkgs={
                # pytest-benchmark depends on cpuinfo which dropped support for Python<=3.6 in 9.0
                # See https://github.com/workhorsy/py-cpuinfo/issues/177
                "pytest-benchmark": latest,
                "py-cpuinfo": "~=8.0.0",
                "msgpack": latest,
            },
            venvs=[
                Venv(
                    name="benchmarks",
                    command="pytest --no-cov --benchmark-warmup=on {cmdargs} tests/benchmarks",
                ),
                Venv(
                    name="benchmarks-nogc",
                    command="pytest --no-cov --benchmark-warmup=on --benchmark-disable-gc {cmdargs} tests/benchmarks",
                ),
            ],
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
            command="pytest {cmdargs} tests/tracer/",
            pkgs={
                "msgpack": latest,
                "attrs": ["==20.1.0", latest],
                "structlog": latest,
                # httpretty v1.0 drops python 2.7 support
                "httpretty": "==0.9.7",
            },
            venvs=[
                Venv(pys=select_pys()),
                # This test variant ensures tracer tests are compatible with both 64bit and 128bit trace ids.
                Venv(
                    pys=MAX_PYTHON_VERSION,
                    env={
                        "DD_TRACE_128_BIT_TRACEID_GENERATION_ENABLED": ["false", "true"],
                    },
                ),
                Venv(
                    env={"PYTHONOPTIMIZE": "1"},
                    # Test with the latest version of Python only
                    pys=".".join((str(_) for _ in SUPPORTED_PYTHON_VERSIONS[-1])),
                ),
            ],
        ),
        Venv(
            name="telemetry",
            command="pytest {cmdargs} tests/telemetry/",
            pys=select_pys(),
            pkgs={
                # httpretty v1.0 drops python 2.7 support
                "httpretty": "==0.9.7",
            },
        ),
        Venv(
            name="integration",
            command="pytest --no-cov {cmdargs} tests/integration/",
            pkgs={"msgpack": [latest]},
            venvs=[
                Venv(
                    name="integration-latest",
                    env={
                        "AGENT_VERSION": "latest",
                    },
                    venvs=[
                        Venv(pys=select_pys(max_version="3.5")),
                        Venv(
                            pkgs={
                                "six": "==1.12.0",
                            },
                            venvs=[
                                # DEV: attrs marked Python 3.6 as deprecated in 22.2.0,
                                #      this logs a warning and causes these tests to fail
                                # https://www.attrs.org/en/22.2.0/changelog.html#id1
                                Venv(pys="3.6", pkgs={"attrs": "<22.2.0"}),
                                Venv(pys="3.7"),
                            ],
                        ),
                        Venv(pys=select_pys(min_version="3.8")),
                    ],
                ),
                Venv(
                    name="integration-snapshot",
                    env={
                        "DD_TRACE_AGENT_URL": "http://localhost:9126",
                        "AGENT_VERSION": "testagent",
                    },
                    venvs=[
                        Venv(pys=select_pys(max_version="3.5")),
                        # DEV: attrs marked Python 3.6 as deprecated in 22.2.0,
                        #      this logs a warning and causes these tests to fail
                        # https://www.attrs.org/en/22.2.0/changelog.html#id1
                        Venv(pys=["3.6"], pkgs={"attrs": "<22.2.0"}),
                        Venv(pys=select_pys(min_version="3.7")),
                    ],
                ),
            ],
        ),
        Venv(
            name="internal",
            command="pytest {cmdargs} tests/internal/",
            pkgs={
                "httpretty": "==0.9.7",
                "gevent": latest,
            },
            venvs=[
                Venv(pys="2.7", pkgs={"packaging": ["==17.1", latest]}),
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.6"),
                    pkgs={"pytest-asyncio": latest, "packaging": ["==17.1", latest]},
                ),
                # FIXME[bytecode-3.11]: internal depends on bytecode, which is not python 3.11 compatible.
                Venv(
                    pys=select_pys(min_version="3.7"),
                    pkgs={"pytest-asyncio": latest, "packaging": ["==17.1", "==22.0", latest]},
                ),
            ],
        ),
        Venv(
            name="gevent",
            command="pytest {cmdargs} tests/contrib/gevent",
            pkgs={
                "elasticsearch": latest,
                "pynamodb": latest,
            },
            venvs=[
                Venv(
                    pys="2.7",
                    pkgs={
                        "gevent": "~=1.3.0",
                        "greenlet": "~=1.0",
                        "requests": "==2.20.0",
                        "opensearch-py": "==1.0.0",
                        "botocore": "==1.17.30",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.6"),
                    pkgs={
                        "gevent": "~=1.3.0",
                        "greenlet": "~=1.0",
                        "elasticsearch": "==6.3.1",
                        "pynamodb": "==3.3.1",
                        "requests": "==2.22.0",
                        "six": "==1.12.0",
                        "aiohttp": latest,
                        "aiobotocore": "<=2.3.1",
                        "opensearch-py": "~=1.0",
                    },
                ),
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
                            pys=select_pys(min_version="3.7", max_version="3.8"),
                            pkgs={
                                "gevent": "~=1.5.0",
                                # greenlet>0.4.17 wheels are incompatible with gevent and python>3.7
                                # This issue was fixed in gevent v20.9:
                                # https://github.com/gevent/gevent/issues/1678#issuecomment-697995192
                                "greenlet": "<0.4.17",
                            },
                        ),
                        Venv(
                            pys="3.9",
                            pkgs={
                                "gevent": ["~=21.1.0", latest],
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
                            # gevent added support for Python 3.11 in 22.8.0
                            pys="3.11",
                            pkgs={
                                "gevent": ["~=22.10.0", latest],
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="runtime",
            command="pytest {cmdargs} tests/runtime/",
            venvs=[Venv(pys=select_pys(), pkgs={"msgpack": latest})],
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
                    },
                ),
            ],
        ),
        Venv(
            name="debugger",
            command="pytest {cmdargs} tests/debugging/",
            pkgs={
                "msgpack": latest,
                "httpretty": "==0.9.7",
                "packaging": ">=17.1",
            },
            venvs=[
                Venv(pys="2.7"),
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={"pytest-asyncio": latest},
                ),
            ],
        ),
        Venv(
            name="vendor",
            command="pytest {cmdargs} tests/vendor/",
            pys=select_pys(),
            pkgs={
                "msgpack": ["~=1.0.0", latest],
            },
        ),
        Venv(
            name="vertica",
            command="pytest {cmdargs} tests/contrib/vertica/",
            pys=select_pys(max_version="3.9"),
            pkgs={
                "vertica-python": [">=0.6.0,<0.7.0", ">=0.7.0,<0.8.0"],
            },
            # venvs=[
            # FIXME: tests fail on vertica 1.x
            # Venv(
            #     # vertica-python dropped support for Python 2.7 in 1.3
            #     pys="2.7",
            #     pkgs={"vertica-python": ["~=1.2.0"]},
            # ),
            # Venv(
            #     # vertica-python dropped support for Python 3.5/3.6 in 1.1
            #     pys=select_pys(min_version="3.5", max_version="3.6"),
            #     pkgs={"vertica-python": ["~=1.0"]},
            # ),
            # Venv(
            #     # vertica-python added support for Python 3.9/3.10 in 1.0
            #     pys=select_pys(min_version="3.7", max_version="3.10"),
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
            pkgs={
                "cassandra-driver": latest,
                "psycopg2-binary": latest,
                "mysql-connector-python": "!=8.0.18",
                "vertica-python": ">=0.6.0,<0.7.0",
                "kombu": ">=4.2.0,<4.3.0",
            },
        ),
        Venv(
            name="httplib",
            command="pytest {cmdargs} tests/contrib/httplib",
            pys=select_pys(),
        ),
        Venv(
            name="test_logging",
            command="pytest {cmdargs} tests/contrib/logging",
            pys=select_pys(),
        ),
        Venv(
            name="falcon",
            command="pytest {cmdargs} tests/contrib/falcon",
            venvs=[
                # FIXME: tests fail on Python 2.7 with falcon 2.0
                # Venv(
                #     # falcon dropped support for Python 2.7 in 3.0
                #     pys="2.7",
                #     pkgs={"falcon": "~=2.0"},
                # ),
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "falcon": [
                            "~=3.0.0",
                            "~=3.0",  # latest 3.x
                            latest,
                        ]
                    },
                ),
            ],
        ),
        Venv(
            name="bottle",
            pkgs={"WebTest": latest},
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
            pkgs={"more_itertools": "<8.11.0"},
            venvs=[
                # Celery 4.3 wants Kombu >= 4.4 and Redis >= 3.2
                # Split into <3.8 and >=3.8 to pin importlib_metadata dependency for kombu
                Venv(
                    # celery dropped support for Python 2.7/3.5 in 5.0
                    pys=select_pys(max_version="3.7"),
                    pkgs={
                        "pytest": "~=3.10",
                        "celery": [
                            "~=4.4",  # most recent 4.x
                        ],
                        "redis": "~=3.5",
                        "kombu": "~=4.4",
                        "importlib_metadata": "<5.0",  # kombu using deprecated shims removed in importlib_metadata 5.0
                        "pytest-cov": "==2.3.0",
                        "pytest-mock": "==2.0.0",
                    },
                ),
                Venv(
                    # celery added support for Python 3.9 in 4.x
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "pytest": "~=3.10",
                        "celery": [
                            "~=4.4",  # most recent 4.x
                        ],
                        "redis": "~=3.5",
                        "kombu": "~=4.4",
                    },
                ),
                Venv(
                    # celery dropped support for Python 3.6 in 5.2
                    pys="3.6",
                    env={
                        # https://docs.celeryproject.org/en/v5.0.5/userguide/testing.html#enabling
                        "PYTEST_PLUGINS": "celery.contrib.pytest",
                    },
                    pkgs={
                        "celery": [
                            "~=5.0.0",
                            "~=5.1.0",
                        ],
                        "redis": "~=3.5",
                        "importlib_metadata": "<5.0",  # kombu using deprecated shims removed in importlib_metadata 5.0
                    },
                ),
                # Celery 5.x wants Python 3.6+
                # Split into <3.8 and >=3.8 to pin importlib_metadata dependency for kombu
                Venv(
                    pys="3.7",
                    env={
                        # https://docs.celeryproject.org/en/v5.0.5/userguide/testing.html#enabling
                        "PYTEST_PLUGINS": "celery.contrib.pytest",
                    },
                    pkgs={
                        "celery": [
                            "~=5.1.0",
                            latest,
                        ],
                        "redis": "~=3.5",
                        "importlib_metadata": "<5.0",  # kombu using deprecated shims removed in importlib_metadata 5.0
                    },
                ),
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
                    # celery added support for Python 3.10 in 5.2, no official support for 3.11 yet
                    # Billiard dependency is incompatible with Python 3.11
                    # https://github.com/celery/billiard/issues/377
                    pys="3.10",
                    env={
                        # https://docs.celeryproject.org/en/v5.0.5/userguide/testing.html#enabling
                        "PYTEST_PLUGINS": "celery.contrib.pytest",
                    },
                    pkgs={
                        "celery": [
                            latest,
                        ],
                        "redis": "~=3.5",
                    },
                ),
            ],
        ),
        Venv(
            name="pylons",
            command="python -m pytest {cmdargs} tests/contrib/pylons",
            venvs=[
                Venv(
                    pys="2.7",
                    pkgs={
                        "pylons": ">=1.0,<1.1",
                        "decorator": "<5",
                        "pastedeploy": "<3",
                        "pyrsistent": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="cherrypy",
            command="python -m pytest {cmdargs} tests/contrib/cherrypy",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.10"),
                    pkgs={
                        "cherrypy": [
                            ">=17,<18",
                        ],
                        "more_itertools": "<8.11.0",
                    },
                ),
                Venv(
                    # cherrypy dropped support for Python 2.7 in 18.0
                    # cherrypy dropped support for Python 3.5 in 18.7
                    # cherrypy added support for Python 3.11 in 18.7
                    pys=select_pys(min_version="3.6"),
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
            },
            venvs=[
                Venv(
                    # pymongo dropped support for Python 2.7/3.5/3.6 in 4.0
                    pys=select_pys(max_version="3.6"),
                    pkgs={"pymongo": ["~=3.4", "~=3.11", "~=3.13"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.9"), pkgs={"pymongo": ["~=3.11", "~=4.0", latest]}
                ),
                Venv(
                    # pymongo added support for Python 3.10 in 3.12.1
                    # pymongo added support for Python 3.11 in 3.12.3
                    pys=select_pys(min_version="3.10"),
                    pkgs={"pymongo": ["~=3.12.3", "~=4.0", latest]},
                ),
            ],
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
                "pytest-django": "==3.10.0",
                "pylibmc": latest,
                "python-memcached": latest,
            },
            venvs=[
                Venv(
                    # django dropped support for Python 2.7 in 2.0
                    pys="2.7",
                    pkgs={"django": "~=1.11"},
                ),
                Venv(
                    # django dropped support for Python 3.5 in 3.0
                    pys="3.5",
                    pkgs={"django": "~=2.2"},
                ),
                Venv(
                    # django dropped support for Python 3.6/3.7 in 4.0
                    pys=select_pys(min_version="3.6", max_version="3.7"),
                    pkgs={
                        "django": "~=3.2",
                        "channels": ["~=3.0", latest],
                    },
                ),
                Venv(
                    # django dropped support for Python 3.8/3.9 in 5.0
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "django": ["~=4.0", latest],
                        "channels": latest,
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "django": [latest],
                        "channels": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="django_hosts",
            command="pytest {cmdargs} tests/contrib/django_hosts",
            pkgs={
                "pytest-django": [
                    "==3.10.0",
                ],
            },
            venvs=[
                Venv(
                    pys="3.5",
                    pkgs={
                        "django_hosts": "~=4.0",
                        "django": "~=2.2",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
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
            name="djangorestframework",
            command="pytest {cmdargs} tests/contrib/djangorestframework",
            pkgs={"pytest-django": "==3.10.0"},
            venvs=[
                Venv(
                    # djangorestframework dropped support for Python 2.7 in 3.10.0
                    pys="2.7",
                    pkgs={
                        "django": "==1.11",
                        "djangorestframework": "~=3.9.3",
                    },
                ),
                Venv(
                    # djangorestframework dropped support for Python 3.5 in 3.13.0
                    pys="3.5",
                    pkgs={
                        "django": ">=2.2,<2.3",
                        "djangorestframework": "~=3.12",
                    },
                ),
                Venv(
                    # djangorestframework dropped support for Django 2.x in 3.14
                    pys=select_pys(min_version="3.6", max_version="3.9"),
                    pkgs={
                        "django": ">=2.2,<2.3",
                        "djangorestframework": ["==3.12.4", "==3.13.1"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "django": "~=3.2",
                        "djangorestframework": ">=3.11,<3.12",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "django": "~=4.0",
                        "djangorestframework": ["~=3.13", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="django_celery",
            command="pytest {cmdargs} tests/contrib/django_celery",
            pkgs={
                # The test app was built with Django 2. We don't need to test
                # other versions as the main purpose of these tests is to ensure
                # an error-free interaction between Django and Celery. We find
                # that we currently have no reasons for expanding this matrix.
                "django": "==2.2.1",
                "sqlalchemy": "~=1.2.18",
                "celery": "~=5.0.5",
                "gevent": latest,
                "requests": latest,
            },
            pys=select_pys(min_version="3.8"),
        ),
        Venv(
            name="elasticsearch",
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_elasticsearch.py",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "elasticsearch": [
                            "~=7.6.0",
                            "~=7.8.0",
                            "~=7.10.0",
                            # latest,
                            # FIXME: Elasticsearch introduced a breaking change in 7.14
                            # which makes it incompatible with previous major versions.
                        ]
                    },
                ),
                Venv(pys=select_pys(), pkgs={"elasticsearch1": ["~=1.10.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch2": ["~=2.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch5": ["~=5.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch6": ["~=6.8.0", latest]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch7": ["~=7.11.0"]}),
            ],
        ),
        Venv(
            name="elasticsearch-multi",
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_elasticsearch_multi.py",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "elasticsearch": ["~=1.6.0"],
                        "elasticsearch6": [latest],
                        "elasticsearch7": ["<7.14.0"],
                    },
                ),
            ],
        ),
        Venv(
            name="elasticsearch8-patch",
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_es8_patch.py",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "elasticsearch8": [latest],
                    },
                ),
            ],
        ),
        Venv(
            name="elasticsearch-opensearch",
            # avoid running tests in ElasticsearchPatchTest, only run tests with OpenSearchPatchTest configurations
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_opensearch.py -k 'not ElasticsearchPatchTest'",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.5"),
                    pkgs={"opensearch-py[requests]": ["~=1.1.0", "~=2.0.0"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={"opensearch-py[requests]": ["~=1.1.0", "~=2.0.0", latest]},
                ),
            ],
        ),
        Venv(
            name="flask",
            command="pytest {cmdargs} tests/contrib/flask",
            pkgs={"blinker": latest, "requests": latest},
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
                # Flask >= 2.0.0
                Venv(
                    # flask dropped support for Python 3.6 in 2.1
                    pys="3.6",
                    pkgs={"flask": "~=2.0.0"},
                ),
                Venv(
                    # flask dropped support for Python 3.6 in 2.1
                    pys="3.6",
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DD_SERVICE": "test.flask.service",
                        "DD_PATCH_MODULES": "jinja2:false",
                    },
                    pkgs={"flask": "~=2.0.0"},
                ),
                Venv(
                    # flask dropped support for Python 2.7/3.5 in 2.0
                    # flask added support for Python 3.10/3.11 in 2.0
                    pys=select_pys(min_version="3.7"),
                    pkgs={
                        "flask": [
                            "~=2.0.0",
                            "~=2.2",  # latest 2.2
                        ],
                        "importlib_metadata": "<=6.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7"),
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DD_SERVICE": "test.flask.service",
                        "DD_PATCH_MODULES": "jinja2:false",
                    },
                    pkgs={
                        "flask": [
                            "~=2.0.0",
                            "~=2.2",  # latest 2.2
                        ],
                        "importlib_metadata": "<=6.0",
                    },
                ),
                Venv(
                    # flask dropped support for Python 3.7 in 2.3.0
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "flask": [
                            "~=2.0.0",
                            "~=2.0",  # latest 2.x
                            latest,
                        ],
                        "importlib_metadata": "<=6.0",
                        "packaging": ">=17.1",
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
                            "~=2.0.0",
                            "~=2.0",  # latest 2.x
                            latest,
                        ],
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
                "packaging": ">=17.1",
            },
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "flask": "~=0.12.0",
                        "Werkzeug": ["<1.0"],
                        "Flask-Cache": "~=0.13.1",
                        "werkzeug": "<1.0",
                        "pytest": "~=3.0",
                        "pytest-mock": "==2.0.0",
                        "pytest-cov": "==2.1.0",
                        "Jinja2": "~=2.11.0",
                        "more_itertools": "<8.11.0",
                        # https://github.com/pallets/itsdangerous/issues/290
                        # DEV: Breaking change made in 2.0 release
                        "itsdangerous": "<2.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                    },
                ),
                Venv(
                    # flask-caching dropped support for Python 3.5 in 1.8
                    pys="3.5",
                    pkgs={
                        "flask": "~=1.0.0",
                        "flask-caching": "~=1.7.0",
                        # https://github.com/pallets/itsdangerous/issues/290
                        # DEV: Breaking change made in 2.0 release
                        "itsdangerous": "<2.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                    },
                ),
                Venv(
                    # flask-caching dropped support for Python 3.6 in 1.11
                    pys="3.6",
                    pkgs={
                        "flask": "~=1.0.0",
                        "flask-caching": "~=1.10.0",
                        # https://github.com/pallets/itsdangerous/issues/290
                        # DEV: Breaking change made in 2.0 release
                        "itsdangerous": "<2.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                        "Jinja2": "~=2.11.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7"),
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
                ),
                Venv(
                    pys=select_pys(min_version="3.7"),
                    pkgs={
                        "flask": [latest],
                        "flask-caching": ["~=1.10.0", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="mako",
            command="pytest {cmdargs} tests/contrib/mako",
            pys=select_pys(),
            pkgs={"mako": ["~=1.1.0", latest]},
        ),
        Venv(
            name="mysql",
            command="pytest {cmdargs} tests/contrib/mysql",
            venvs=[
                Venv(
                    # mysql-connector-python dropped support for Python 2.7/3.5 in 8.0.24
                    pys=select_pys(max_version="3.5"),
                    pkgs={"mysql-connector-python": ["==8.0.5", "==8.0.23"]},
                ),
                Venv(
                    # mysql-connector-python dropped support for Python 3.6 in 8.0.29
                    pys="3.6",
                    pkgs={"mysql-connector-python": ["==8.0.5", "==8.0.29"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.9"),
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
            ],
        ),
        Venv(
            name="psycopg",
            command="pytest {cmdargs} tests/contrib/psycopg",
            venvs=[
                Venv(
                    # psycopg2-binary dropped support for Python 2.7 in 2.9
                    pys="2.7",
                    # DEV: Use `psycopg2-binary` so we don't need PostgreSQL dev headers
                    pkgs={"psycopg2-binary": "~=2.8.0"},
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.8"),
                    pkgs={"psycopg2-binary": "~=2.8.0"},
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    # psycopg2-binary added support for Python 3.9/3.10 in 2.9.1
                    # psycopg2-binary added support for Python 3.11 in 2.9.2
                    pkgs={"psycopg2-binary": ["~=2.9.2", latest]},
                ),
            ],
        ),
        Venv(
            name="pymemcache",
            pys=select_pys(),
            pkgs={
                "pymemcache": [
                    "~=3.4.2",
                    "~=3.5",
                    latest,
                ]
            },
            venvs=[
                Venv(command="pytest {cmdargs} --ignore=tests/contrib/pymemcache/autopatch tests/contrib/pymemcache"),
                Venv(command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/pymemcache/autopatch/"),
            ],
        ),
        Venv(
            name="pynamodb",
            command="pytest {cmdargs} tests/contrib/pynamodb",
            venvs=[
                Venv(
                    # pynamodb dropped support for Python 2.7/3.5 in 4.4
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        "pynamodb": ["~=4.3.0"],
                        "moto": ">=0.0,<1.0",
                        "rsa": "<4.7.1",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "pynamodb": ["~=5.0", "~=5.3", latest],
                        "moto": ">=1.0,<2.0",
                        "cfn-lint": "~=0.53.1",
                        "Jinja2": "~=2.11.0",
                    },
                ),
            ],
        ),
        Venv(
            name="starlette",
            command="pytest {cmdargs} tests/contrib/starlette",
            pkgs={
                "httpx": latest,
                "pytest-asyncio": latest,
                "requests": latest,
                "aiofiles": latest,
                "sqlalchemy": latest,
                "aiosqlite": latest,
                "databases": latest,
            },
            venvs=[
                Venv(
                    # starlette dropped support for Python 3.6 in 0.20
                    pys="3.6",
                    pkgs={"starlette": ["~=0.14", "~=0.19"]},
                ),
                Venv(
                    # starlette added support for Python 3.9 in 0.14
                    pys=select_pys(min_version="3.7", max_version="3.9"),
                    pkgs={"starlette": ["~=0.14", "~=0.20", latest]},
                ),
                Venv(
                    # starlette added support for Python 3.10 in 0.15
                    pys="3.10",
                    pkgs={"starlette": ["~=0.15", "~=0.20", latest]},
                ),
                Venv(
                    # starlette added support for Python 3.11 in 0.21
                    pys="3.11",
                    pkgs={"starlette": ["~=0.21", latest]},
                ),
            ],
        ),
        Venv(
            name="sqlalchemy",
            command="pytest {cmdargs} tests/contrib/sqlalchemy",
            venvs=[
                Venv(
                    venvs=[
                        Venv(
                            # sqlalchemy dropped support for Python 2.7/3.5/3.6 in 2.0
                            pys=select_pys(max_version="3.6"),
                            pkgs={
                                "sqlalchemy": ["<2.0"],
                                "psycopg2-binary": "~=2.8.0",
                                "mysql-connector-python": "<8.0.24",
                            },
                        ),
                        Venv(
                            pys=select_pys(min_version="3.7", max_version="3.9"),
                            pkgs={
                                "sqlalchemy": ["~=1.3", "~=1.4"],
                                "psycopg2-binary": latest,
                                "mysql-connector-python": latest,
                            },
                        ),
                        Venv(
                            # sqlalchemy added support for Python 3.10 in 1.4.26
                            pys="3.10",
                            pkgs={
                                "sqlalchemy": "~=1.4",
                                "psycopg2-binary": latest,
                                "mysql-connector-python": latest,
                            },
                        ),
                        # FIXME: tests fail with sqlalchemy 2.0
                        # Venv(
                        #     # sqlalchemy added support for Python 3.11 in 2.0
                        #     pys="3.11",
                        #     pkgs={
                        #         "sqlalchemy": ["~=2.0.0", latest],
                        #         "psycopg2-binary": latest,
                        #         "mysql-connector-python": latest,
                        #     },
                        # ),
                    ],
                ),
            ],
        ),
        Venv(
            name="requests",
            command="pytest {cmdargs} tests/contrib/requests",
            venvs=[
                Venv(
                    # requests dropped support for Python 2.7 in 2.28
                    pys="2.7",
                    pkgs={
                        "requests-mock": ">=1.4",
                        "requests": ["~=2.26", "~=2.27"],
                    },
                ),
                Venv(
                    # requests dropped support for Python 3.5 in 2.26
                    pys="3.5",
                    pkgs={
                        "requests-mock": ">=1.4",
                        "requests": ["~=2.20", "~=2.25"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "requests-mock": ">=1.4",
                        "requests": [
                            "~=2.20",
                            "~=2.26",
                            latest,
                        ],
                    },
                ),
                Venv(
                    # requests added support for Python 3.10 in 2.27
                    pys="3.10",
                    pkgs={
                        "requests-mock": ">=1.4",
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
                        "requests-mock": ">=1.4",
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
                    },
                ),
            ],
        ),
        Venv(
            name="boto",
            command="pytest {cmdargs} tests/contrib/boto",
            venvs=[Venv(pys=select_pys(max_version="3.6"), pkgs={"boto": latest, "moto": "<1.0.0"})],
        ),
        Venv(
            name="botocore",
            command="pytest {cmdargs} tests/contrib/botocore",
            venvs=[
                Venv(pys=select_pys(min_version="3.8"), pkgs={"moto[all]": latest, "botocore": latest}),
                Venv(
                    pys=["2.7"],
                    pkgs={
                        "moto": "~=1.0",
                        "botocore": "~=1.20.0",
                        "python-jose[cryptography]": "==3.1.0",
                        "rsa": "<4.7.1",
                    },
                ),
                Venv(
                    pkgs={
                        "cffi": "==1.14.0",
                        "cfn-lint": "==0.33.2",
                        "jinja2": "~=2.11.0",
                        "python-jose[cryptography]": "==3.1.0",
                    },
                    venvs=[
                        Venv(
                            pys=["3.5"],
                            pkgs={
                                "moto[all]": "~=1.0",
                            },
                        ),
                        Venv(
                            pys=["3.6"],
                            pkgs={
                                "moto[all]": "~=2.0",
                                "graphql-core": "~=3.1.0",
                            },
                            venvs=[
                                Venv(
                                    pys=["3.7"],
                                    pkgs={
                                        "markupsafe": "<2.0",
                                    },
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="mongoengine",
            command="pytest {cmdargs} tests/contrib/mongoengine",
            pkgs={
                "pymongo": latest,
            },
            venvs=[
                Venv(
                    # mongoengine dropped support for Python 2.7 in 0.20
                    pys="2.7",
                    pkgs={"mongoengine": "~=0.19"},
                ),
                Venv(
                    # mongoengine dropped support for Python 3.5 in 0.22
                    pys="3.5",
                    pkgs={"mongoengine": "<0.22"},
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.8"),
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
                "pytest-asyncio": latest,
                "httpx": latest,
                "asgiref": ["~=3.0.0", "~=3.0", latest],
            },
            pys=select_pys(min_version="3.6"),
            command="pytest {cmdargs} tests/contrib/asgi",
        ),
        Venv(
            name="mariadb",
            command="pytest {cmdargs} tests/contrib/mariadb",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.10"),
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
            venvs=[
                Venv(
                    # pymysql dropped support for Python 2.7/3.5 in 1.0
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        "pymysql": "~=0.9",
                    },
                ),
                Venv(
                    # pymysql added support for Python 3.8/3.9 in 0.10
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"pymysql": "~=0.10"},
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
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
            command="pytest {cmdargs} tests/contrib/pyramid/test_pyramid.py",
            pkgs={
                "requests": [latest],
                "webtest": [latest],
                "tests/contrib/pyramid/pserve_app": [latest],
            },
            venvs=[
                Venv(
                    # pyramid dropped support for Python 2.7/3.5 in 2.0
                    # pserve_app has PasteDeploy dependency, but PasteDeploy>=3.0 is incompatible with Python 2.7
                    # pyramid>=2.0 no longer supports Python 2.7 and 3.5
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        "pastedeploy": "<3.0",
                        "pyramid": [
                            "~=1.10",
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.9"),
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
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "pyramid": [latest],
                    },
                ),
            ],
        ),
        Venv(
            name="aiobotocore",
            command="pytest {cmdargs} tests/contrib/aiobotocore",
            pkgs={"pytest-asyncio": latest, "async_generator": ["~=1.10"]},
            venvs=[
                # async_generator 1.10 used because @asynccontextmanager was only available in Python 3.6+
                # aiobotocore 1.x and higher require Python 3.6 or higher
                # aiobotocore dropped Python 3.5 support in 0.12
                Venv(
                    pys="3.5",
                    pkgs={
                        "aiobotocore": ["~=0.11"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "aiobotocore": ["~=1.4.2", "~=2.0.0", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="fastapi",
            command="pytest {cmdargs} tests/contrib/fastapi",
            pkgs={
                "httpx": latest,
                "pytest-asyncio": latest,
                "requests": latest,
                "aiofiles": latest,
            },
            venvs=[
                Venv(
                    # fastapi dropped support for Python 3.6 in 0.84
                    pys="3.6",
                    pkgs={"fastapi": ["~=0.64.0", "~=0.83.0"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.10"),
                    pkgs={"fastapi": ["~=0.64.0", "~=0.90.0", latest]},
                ),
                Venv(
                    # fastapi added support for Python 3.11 in 0.86.0
                    pys=select_pys(min_version="3.11"),
                    pkgs={"fastapi": ["~=0.86.0", latest]},
                ),
            ],
        ),
        Venv(
            name="aiomysql",
            pys=select_pys(min_version="3.7"),
            command="pytest {cmdargs} tests/contrib/aiomysql",
            pkgs={
                "pytest-asyncio": latest,
                "aiomysql": ["~=0.1.0", latest],
            },
        ),
        Venv(
            name="pytest",
            command="pytest {cmdargs} tests/contrib/pytest/",
            venvs=[
                Venv(
                    pys=["2.7"],
                    # pytest==4.6 is last to support python 2.7
                    pkgs={
                        "pytest": ">=4.0,<=4.6",
                        "msgpack": latest,
                        "pytest-cov": "==2.12.1",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.9"),
                    pkgs={
                        "pytest": [
                            ">=6.0,<7.0",
                            latest,
                        ],
                        "msgpack": latest,
                        "more_itertools": "<8.11.0",
                        "pytest-mock": "==2.0.0",
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
                                "pytest": [latest],
                                "pytest-cov": "==2.12.0",
                            },
                        ),
                    ],
                ),
                Venv(
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "pytest": [
                            ">=6.0,<7.0",
                            latest,
                        ],
                        "msgpack": latest,
                        "asynctest": "==0.13.0",
                        "more_itertools": "<8.11.0",
                    },
                ),
            ],
        ),
        Venv(
            name="asynctest",
            command="pytest {cmdargs} tests/contrib/asynctest/",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.9"),
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
            name="pytest-bdd",
            command="pytest {cmdargs} tests/contrib/pytest_bdd/",
            pkgs={"msgpack": latest},
            venvs=[
                Venv(
                    pys=["2.7"],
                    # pytest-bdd==3.4 is last to support python 2.7
                    pkgs={"pytest-bdd": ">=3.0,<3.5"},
                ),
                Venv(
                    pkgs={
                        "more_itertools": "<8.11.0",
                    },
                    venvs=[
                        Venv(
                            pys=["3.6"],
                            pkgs={"pytest-bdd": [">=4.0,<5.0"]},
                        ),
                        Venv(
                            pys=select_pys(min_version="3.7", max_version="3.9"),
                            pkgs={
                                "pytest-bdd": [
                                    ">=4.0,<5.0",
                                    # FIXME: add support for v6.1
                                    ">=6.0,<6.1",
                                ]
                            },
                        ),
                        Venv(
                            pys=select_pys(min_version="3.10"),
                            pkgs={
                                "pytest-bdd": [
                                    ">=4.0,<5.0",
                                    # FIXME: add support for v6.1
                                    ">=6.0,<6.1",
                                ]
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="grpc",
            command="python -m pytest {cmdargs} tests/contrib/grpc",
            pkgs={
                "googleapis-common-protos": latest,
            },
            venvs=[
                # Versions between 1.14 and 1.20 have known threading issues
                # See https://github.com/grpc/grpc/issues/18994
                Venv(
                    # grpcio dropped support for Python 2.7 in 1.27
                    pys="2.7",
                    pkgs={"grpcio": ["~=1.26.0"]},
                ),
                Venv(
                    # grpcio dropped support for Python 3.5 in 1.40, but aio module (not compatible with Python 3.5)
                    # was added in 1.32
                    pys="3.5",
                    pkgs={"grpcio": ["~=1.31.0"]},
                ),
                Venv(
                    # grpcio dropped support for Python 3.6 in 1.49
                    pys="3.6",
                    pkgs={"grpcio": ["~=1.34.0", "~=1.48.0"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.9"),
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
            ],
        ),
        Venv(
            name="grpc_aio",
            command="python -m pytest {cmdargs} tests/contrib/grpc_aio",
            pkgs={
                "googleapis-common-protos": latest,
                "pytest-asyncio": latest,
            },
            venvs=[
                Venv(
                    # grpcio dropped support for Python 3.6 in 1.49
                    pys="3.6",
                    pkgs={"grpcio": ["~=1.34.0", "~=1.48.0"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.9"),
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
            ],
        ),
        Venv(
            name="graphene",
            command="pytest {cmdargs} tests/contrib/graphene",
            pys=select_pys(min_version="3.6"),
            pkgs={
                "graphene": ["~=3.0.0", latest],
                "pytest-asyncio": latest,
                "graphql-relay": "~=3.1.5",
            },
        ),
        Venv(
            name="graphql",
            command="pytest {cmdargs} tests/contrib/graphql",
            venvs=[
                Venv(
                    pys=["3.6"],
                    pkgs={
                        "pytest-asyncio": latest,
                        "graphql-core": ["~=3.1.0"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7"),
                    pkgs={
                        "pytest-asyncio": latest,
                        "graphql-core": ["~=3.1.0", "~=3.2.0", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="rq",
            command="pytest tests/contrib/rq",
            venvs=[
                Venv(
                    # rq dropped support for Python 2.7 in 1.4.0
                    pys="2.7",
                    pkgs={
                        "rq": [
                            "~=1.3.0",
                        ],
                    },
                ),
                Venv(
                    # rq dropped support for Python 3.5 in 1.12
                    pys="3.5",
                    pkgs={"rq": ["~=1.8.0", "~=1.11.1"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.8"),
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
            pys=select_pys(min_version="3.6"),
            command="pytest {cmdargs} tests/contrib/httpx",
            pkgs={
                "pytest-asyncio": latest,
                "httpx": [
                    "~=0.17.0",
                    "~=0.22.0",
                    latest,
                ],
            },
        ),
        Venv(
            name="urllib3",
            command="pytest {cmdargs} tests/contrib/urllib3",
            venvs=[
                Venv(
                    # urllib3 to drop support for Python 2.7/3.5/3.6 in 2.0
                    pys=select_pys(max_version="3.6"),
                    pkgs={"urllib3": ["~=1.26.4", "<2.0"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.8"),
                    pkgs={"urllib3": ["~=1.26.4", latest]},
                ),
                Venv(
                    # urllib3 added support for Python 3.9 in 1.25.8
                    pys="3.9",
                    pkgs={"urllib3": ["~=1.25.8", "~=1.26.12", latest]},
                ),
                Venv(
                    # urllib3 added support for Python 3.10 in 1.26.6
                    pys="3.10",
                    pkgs={"urllib3": ["~=1.26.6", latest]},
                ),
                Venv(
                    # urllib3 added support for Python 3.11 in 1.26.8
                    pys="3.11",
                    pkgs={"urllib3": ["~=1.26.8", latest]},
                ),
            ],
        ),
        Venv(
            # cassandra-driver does not officially support 3.9, 3.10
            # releases 3.7 and 3.8 are broken on Python >= 3.7
            # (see https://github.com/r4fek/django-cassandra-engine/issues/104)
            name="cassandra",
            pys=select_pys(max_version="3.8"),
            pkgs={"cassandra-driver": ["~=3.24.0", latest]},
            command="pytest {cmdargs} tests/contrib/cassandra",
        ),
        Venv(
            name="algoliasearch",
            command="pytest {cmdargs} tests/contrib/algoliasearch",
            venvs=[
                Venv(
                    # algoliasearch dropped support for Python 2.7 in 3.0
                    pys="2.7",
                    pkgs={
                        "algoliasearch": ["~=2.5", "~=2.6"],
                        "pyrsistent": "~=0.14.0",
                    },
                ),
                Venv(pys=select_pys(min_version="3.5", max_version="3.8"), pkgs={"algoliasearch": ["~=2.5", "~=2.6"]}),
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
            pys=select_pys(min_version="3.5", max_version="3.9"),
            pkgs={
                "sqlalchemy": latest,
                "aiopg": "~=0.16.0",
            },
            # venvs=[
            # FIXME: tests fail on aiopg 1.x
            # Venv(
            #     # aiopg dropped support for Python 3.5 in 1.1
            #     pys="3.5",
            #     pkgs={
            #         "aiopg": ["~=0.16.0", "~=1.0"],
            #     },
            # ),
            # Venv(
            #     # aiopg dropped support for Python 3.6 in 1.4
            #     pys="3.6",
            #     pkgs={
            #         "aiopg": ["~=1.2", "~=1.3"],
            #     },
            # ),
            # Venv(
            #     pys=select_pys(min_version="3.7", max_version="3.9"),
            #     pkgs={
            #         "aiopg": ["~=1.2", "~=1.4.0", latest],
            #     },
            # ),
            # Venv(
            #     # aiopg added support for Python 3.10 in 1.3
            #     pys="3.10",
            #     pkgs={
            #         "aiopg": ["~=1.3.0", latest],
            #     },
            # ),
            # Venv(
            #     # aiopg added support for Python 3.11 in 1.4
            #     pys="3.11",
            #     pkgs={
            #         "aiopg": ["~=1.4.0", latest],
            #     },
            # ),
            # ],
        ),
        Venv(
            name="aiohttp",
            command="pytest {cmdargs} tests/contrib/aiohttp",
            pkgs={
                "pytest-aiohttp": [latest],
            },
            venvs=[
                Venv(
                    pys="3.5",
                    pkgs={
                        # aiohttp 3.8 dropped support for Python 3.5
                        "aiohttp": ["~=2.3", "<3.8"],
                        "async-timeout": ["<4.0.0"],
                    },
                ),
                Venv(
                    # pytest-asyncio is incompatible with aiohttp 3.0+ in Python 3.6
                    pys="3.6",
                    pkgs={
                        "aiohttp": [
                            "~=3.7",
                            latest,
                        ],
                        "yarl": "~=1.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7"),
                    pkgs={
                        "pytest-asyncio": [latest],
                        "aiohttp": [
                            "~=3.7",
                            latest,
                        ],
                        "yarl": "~=1.0",
                    },
                ),
            ],
        ),
        Venv(
            name="aiohttp_jinja2",
            command="pytest {cmdargs} tests/contrib/aiohttp_jinja2",
            pkgs={
                "pytest-aiohttp": [latest],
            },
            venvs=[
                Venv(
                    pys="3.6",
                    pkgs={
                        "aiohttp": [
                            "~=3.7",
                            latest,
                        ],
                        "aiohttp_jinja2": [
                            "~=1.5.0",
                            latest,
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7"),
                    pkgs={
                        "pytest-asyncio": [latest],
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
                ),
            ],
        ),
        Venv(
            name="jinja2",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "jinja2": "~=2.11.0",
                        # https://github.com/pallets/markupsafe/issues/282
                        # DEV: Breaking change made in 2.1.0 release
                        "markupsafe": "<2.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "jinja2": ["~=3.0.0", latest],
                    },
                ),
            ],
            command="pytest {cmdargs} tests/contrib/jinja2",
        ),
        Venv(
            name="rediscluster",
            pys=select_pys(),
            command="pytest {cmdargs} tests/contrib/rediscluster",
            pkgs={
                # deprecated package
                "redis-py-cluster": [">=2.0,<2.1", latest],
            },
        ),
        Venv(
            name="redis",
            venvs=[
                Venv(
                    # redis dropped support for Python 2.7/3.5 in 4.0
                    pys=select_pys(max_version="3.5"),
                    command="pytest {cmdargs} --ignore-glob='*asyncio*' tests/contrib/redis",
                    pkgs={
                        "redis": ["~=3.5.3"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.10"),
                    command="pytest {cmdargs} tests/contrib/redis",
                    pkgs={
                        "pytest-asyncio": latest,
                        "redis": [
                            "~=4.1",
                            "~=4.3",
                            latest,
                        ],
                    },
                ),
                Venv(
                    # redis added support for Python 3.11 in 4.3
                    pys="3.11",
                    command="pytest {cmdargs} tests/contrib/redis",
                    pkgs={
                        "pytest-asyncio": latest,
                        "redis": ["~=4.3", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="aredis",
            pys=select_pys(min_version="3.6", max_version="3.9"),
            command="pytest {cmdargs} tests/contrib/aredis",
            pkgs={
                "pytest-asyncio": latest,
                "aredis": latest,
            },
        ),
        Venv(
            name="yaaredis",
            command="pytest {cmdargs} tests/contrib/yaaredis",
            pkgs={"pytest-asyncio": latest},
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.9"),
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
                "pytest-asyncio": latest,
                "requests": latest,
            },
            venvs=[
                Venv(
                    # sanic added support for Python 3.9 in 20.12
                    pys=select_pys(min_version="3.7", max_version="3.9"),
                    pkgs={
                        "sanic": "~=20.12",
                        "pytest-sanic": "~=1.6.2",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.9"),
                    pkgs={
                        "sanic": ["~=21.3", "~=21.12"],
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
                    pys=select_pys(min_version="3.7", max_version="3.10"),
                    pkgs={
                        "sanic": ["~=22.3", "~=22.12"],
                        "sanic-testing": "~=22.3.0",
                    },
                ),
                Venv(
                    # sanic added support for Python 3.11 in 22.12.0
                    pys="3.11",
                    pkgs={
                        "sanic": "~=22.12.0",
                        "sanic-testing": "~=22.3.0",
                    },
                ),
            ],
        ),
        Venv(
            name="snowflake",
            command="pytest {cmdargs} tests/contrib/snowflake",
            pkgs={"responses": "~=0.16.0", "cryptography": "<39"},
            venvs=[
                Venv(
                    # snowflake-connector-python dropped support for Python 2.7 in 2.2.0
                    pys="2.7",
                    pkgs={
                        "snowflake-connector-python": "~=2.1.0",
                        "pyOpenSSL": "~=19.1",
                    },
                ),
                Venv(
                    # snowflake-connector-python dropped support for Python 3.5 in 2.3.0
                    pys="3.5",
                    pkgs={
                        "snowflake-connector-python": "~=2.2.0",
                        "pyOpenSSL": "~=19.1",
                    },
                ),
                Venv(
                    # snowflake-connector-python dropped support for Python 3.6 in 2.7.5
                    pys="3.6",
                    pkgs={
                        "snowflake-connector-python": ["~=2.4.0", "~=2.7.4"],
                        "pyOpenSSL": "~=19.1",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.8"),
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
                    pys="3.11",
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
            name="aioredis",
            # aioredis was merged into redis as of v2.0.1, no longer maintained and does not support Python 3.11 onward
            pys=select_pys(min_version="3.6", max_version="3.10"),
            command="pytest {cmdargs} tests/contrib/aioredis",
            pkgs={
                "pytest-asyncio": latest,
                "aioredis": [
                    "~=1.3.0",
                    latest,
                ],
            },
        ),
        Venv(
            name="asyncpg",
            command="pytest {cmdargs} tests/contrib/asyncpg",
            pkgs={
                "pytest-asyncio": latest,
            },
            venvs=[
                # our test_asyncpg.py uses `yield` in an async function and is not compatible with Python 3.5
                Venv(
                    # asyncpg dropped support for Python 3.6 in 0.27
                    pys="3.6",
                    pkgs={"asyncpg": ["~=0.23", "~=0.26"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.8"),
                    pkgs={"asyncpg": ["~=0.23", latest]},
                ),
                Venv(
                    # asyncpg added support for Python 3.9 in 0.22
                    pys="3.9",
                    pkgs={"asyncpg": ["~=0.22.0", latest]},
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
            ],
        ),
        Venv(
            name="asyncio",
            command="pytest {cmdargs} tests/contrib/asyncio",
            pys=select_pys(min_version="3.5"),
            pkgs={
                "pytest-asyncio": latest,
            },
        ),
        Venv(
            name="futures",
            command="pytest {cmdargs} tests/contrib/futures",
            pkgs={"gevent": latest},
            venvs=[
                # futures is backported for 2.7
                Venv(pys=["2.7"], pkgs={"futures": ["~=3.0", "~=3.1", "~=3.2", "~=3.4"]}),
                Venv(
                    pys=select_pys(min_version="3.5"),
                ),
            ],
        ),
        Venv(
            name="sqlite3",
            command="pytest {cmdargs} tests/contrib/sqlite3",
            venvs=[
                Venv(
                    pys=["2.7", "3.5", "3.6", "3.8", "3.9", "3.10", "3.11"],
                ),
                Venv(pys=["3.7"], pkgs={"importlib-metadata": latest}),
            ],
        ),
        Venv(
            name="dbapi",
            command="pytest {cmdargs} tests/contrib/dbapi",
            pys=select_pys(),
        ),
        Venv(
            name="dogpile_cache",
            command="pytest {cmdargs} tests/contrib/dogpile_cache",
            venvs=[
                Venv(
                    # dogpile.cache dropped support for Python 2.7/3.5 in 1.0
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        "dogpile.cache": ["~=0.8", "~=0.9"],
                        "decorator": "<5",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.10"),
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
            pys=select_pys(),
            command="pytest {cmdargs} tests/contrib/consul",
            pkgs={
                "python-consul": [
                    ">=1.1,<1.2",
                    latest,
                ],
            },
        ),
        Venv(
            name="opentelemetry",
            command="pytest {cmdargs} tests/opentelemetry",
            pys=select_pys(min_version="3.7"),
            pkgs={
                "pytest-asyncio": latest,
                "opentelemetry-api": ["~=1.0.0", "~=1.3.0", "~=1.4.0", "~=1.8.0", "~=1.11.0", "~=1.15.0", latest],
                "opentelemetry-instrumentation-flask": latest,
                # opentelemetry-instrumentation-flask does not support the latest version of markupsafe
                "markupsafe": "==2.0.1",
                "flask": latest,
                "gevent": latest,
                "requests": "==2.28.1",  # specific version expected by tests
            },
        ),
        Venv(
            name="opentracer",
            pkgs={"opentracing": latest},
            venvs=[
                Venv(
                    pys=select_pys(),
                    command="pytest {cmdargs} tests/opentracer/core",
                ),
                Venv(
                    pys=select_pys(min_version="3.5"),
                    command="pytest {cmdargs} tests/opentracer/test_tracer_asyncio.py",
                    pkgs={"pytest-asyncio": latest},
                ),
                Venv(
                    pys=select_pys(min_version="3.5"),
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
                            pys=select_pys(max_version="3.6"),
                            pkgs={
                                "gevent": "~=1.2.0",
                                "greenlet": "~=1.0",
                            },
                        ),
                        Venv(
                            pys=select_pys(min_version="3.7", max_version="3.8"),
                            pkgs={
                                "gevent": ["~=1.4.0"],
                                # greenlet>0.4.17 wheels are incompatible with gevent and python>3.7
                                # This issue was fixed in gevent v20.9:
                                # https://github.com/gevent/gevent/issues/1678#issuecomment-697995192
                                "greenlet": "<0.4.17",
                            },
                        ),
                        Venv(
                            pys="3.9",
                            pkgs={
                                "gevent": "~=21.1.0",
                                "greenlet": "~=1.0",
                            },
                        ),
                        Venv(
                            pys="3.10",
                            pkgs={
                                "gevent": "~=21.8.0",
                            },
                        ),
                        Venv(
                            pys="3.11",
                            pkgs={
                                "gevent": "~=22.8.0",
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="pyodbc",
            command="pytest {cmdargs} tests/contrib/pyodbc",
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
                    pys="3.11",
                    pkgs={"pyodbc": [latest]},
                ),
            ],
        ),
        Venv(
            name="pylibmc",
            command="pytest {cmdargs} tests/contrib/pylibmc",
            venvs=[
                Venv(
                    # pylibmc dropped support for Python 2.7/3.5 in 1.6.2
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        "pylibmc": "~=1.6.1",
                    },
                ),
                Venv(
                    # pylibmc added support for Python 3.8/3.9/3.10 in 1.6.2
                    pys=select_pys(min_version="3.6", max_version="3.10"),
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
            venvs=[
                Venv(
                    # kombu dropped support for Python 2.5/3.5/3.6 in 5.0
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "kombu": [
                            ">=4.0,<4.1",
                            ">=4.6,<4.7",
                        ],
                        # kombu using deprecated shims removed in importlib-metadata 5.0 pre-Python 3.8
                        "importlib_metadata": "<5.0",
                    },
                ),
                # Kombu>=4.2 only supports Python 3.7+
                Venv(
                    pys="3.7",
                    pkgs={
                        "kombu": [">=4.6,<4.7", ">=5.0,<5.1", latest],
                        # kombu using deprecated shims removed in importlib-metadata 5.0 pre-Python 3.8
                        "importlib_metadata": "<5.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "kombu": [">=4.6,<4.7", ">=5.0,<5.1", latest],
                    },
                ),
                Venv(
                    # kombu added support for Python 3.10 in 5.2.1
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "kombu": [">=5.2,<5.3", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="tornado",
            command="python -m pytest {cmdargs} tests/contrib/tornado",
            venvs=[
                Venv(
                    # tornado dropped support for Python 2.7 in 6.0
                    pys="2.7",
                    pkgs={
                        "tornado": [
                            "~=4.5",
                            # "~=5.1.1"  # FIXME: tests fail on Python 2.7 with tornado 5.1.1
                        ],
                        "futures": ["~=3.3", latest],
                    },
                ),
                # FIXME: tests fail on Python 3.5/3.6 with tornado 5.1, 6.1
                # Venv(
                #     # tornado dropped support for Python 3.5/3.6 in 6.2
                #     pys=select_pys(min_version="3.5", max_version="3.6"),
                #     pkgs={"tornado": ["~=5.1.1", "~=6.1"]},
                # ),
                Venv(
                    # tornado added support for Python 3.7 in 5.1
                    pys="3.7",
                    pkgs={"tornado": ["~=5.1", "~=6.1", latest]},
                ),
                Venv(
                    # tornado added support for Python 3.8/3.9 in 6.1
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={"tornado": ["~=6.1", latest]},
                ),
                Venv(
                    # tornado added support for Python 3.10 in 6.2
                    pys=select_pys(min_version="3.10"),
                    pkgs={"tornado": [latest]},
                ),
            ],
        ),
        Venv(
            name="mysqldb",
            command="pytest {cmdargs} tests/contrib/mysqldb",
            venvs=[
                Venv(
                    # mysqlclient dropped support for Python 2.7/3.5 in 2.0
                    pys=select_pys(max_version="3.5"),
                    pkgs={"mysqlclient": "~=1.4.6"},
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.9"),
                    pkgs={"mysqlclient": ["~=2.0", "~=2.1", latest]},
                ),
                Venv(
                    # mysqlclient added support for Python 3.9/3.10 in 2.1
                    pys=select_pys(min_version="3.9"),
                    pkgs={"mysqlclient": ["~=2.1", latest]},
                ),
            ],
        ),
        Venv(
            name="molten",
            command="pytest {cmdargs} tests/contrib/molten",
            pys=select_pys(min_version="3.6"),
            pkgs={
                "molten": [">=1.0,<1.1", latest],
            },
        ),
        Venv(
            name="gunicorn",
            command="pytest {cmdargs} tests/contrib/gunicorn",
            pkgs={"requests": latest, "gevent": latest},
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={"gunicorn": ["==20.0.4", latest]},
                ),
                Venv(
                    pys="2.7",
                    pkgs={"gunicorn": ["==19.10.0"]},
                ),
            ],
        ),
        Venv(
            name="kafka",
            venvs=[
                Venv(
                    command="pytest {cmdargs} tests/contrib/kafka",
                    venvs=[
                        # confluent-kafka dropped official wheels for Python 2.7 in 1.8.2
                        Venv(pys="2.7", pkgs={"confluent-kafka": "~=1.7.0"}),
                        # confluent-kafka>=1.7 has issues building on linux with Python 3.5
                        Venv(pys="3.5", pkgs={"confluent-kafka": "~=1.5.0"}),
                        Venv(
                            pys=select_pys(min_version="3.6", max_version="3.10"),
                            pkgs={"confluent-kafka": ["~=1.9.2", latest]},
                        ),
                        # confluent-kafka added support for Python 3.11 in 2.0.2
                        Venv(pys="3.11", pkgs={"confluent-kafka": latest}),
                    ],
                ),
            ],
        ),
        Venv(
            name="aws_lambda",
            command="pytest {cmdargs} tests/contrib/aws_lambda",
            pys=select_pys(min_version="3.7", max_version="3.9"),
            pkgs={
                "boto3": latest,
                "datadog-lambda": [">=4.66.0", latest],
                "pytest-asyncio": latest,
            },
        ),
        Venv(
            name="sourcecode",
            command="pytest {cmdargs} tests/sourcecode",
            pys=select_pys(),
            pkgs={
                "setuptools": ["<=67.6.0"],
            },
        ),
        Venv(
            name="ci_visibility",
            command="pytest {cmdargs} tests/ci_visibility",
            pys=select_pys(),
        ),
        Venv(
            name="profile",
            pkgs={
                "gunicorn": latest,
                #
                # pytest-benchmark depends on cpuinfo which dropped support for Python<=3.6 in 9.0
                # See https://github.com/workhorsy/py-cpuinfo/issues/177
                "pytest-benchmark": latest,
                "py-cpuinfo": "~=8.0.0",
            },
            venvs=[
                # Python 2.7
                Venv(
                    # uWSGI tests are not supported on Python 2.7
                    command='python -m tests.profiling.run pytest --capture=no --benchmark-disable --ignore-glob="*asyncio*" --ignore=tests/profiling/test_uwsgi.py {cmdargs} tests/profiling',  # noqa: E501
                    pys="2.7",
                    venvs=[
                        Venv(
                            pkgs={
                                "tenacity": latest,
                                "protobuf": latest,
                            }
                        ),
                        # Minimum requirements
                        Venv(
                            pkgs={
                                "tenacity": "==5.0.1",
                                "protobuf": "==3.0.0",
                            }
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
                                        # gevent==1.1 requires greenlet<2
                                        "gevent": "==1.1.0",
                                        "greenlet": "<2",
                                    }
                                ),
                                Venv(
                                    pkgs={"gevent": latest},
                                ),
                            ],
                        ),
                    ],
                ),
                # Python 3.5+
                Venv(
                    command="python -m tests.profiling.run pytest --no-cov --capture=no --benchmark-disable {cmdargs} tests/profiling",  # noqa: E501
                    pkgs={
                        "uwsgi": latest,
                        "pytest-asyncio": latest,
                    },
                    venvs=[
                        # Python 3.5-3.6
                        Venv(
                            pys=select_pys(min_version="3.5", max_version="3.6"),
                            venvs=[
                                Venv(
                                    pkgs={
                                        "tenacity": latest,
                                        "protobuf": latest,
                                    },
                                ),
                                # Minimum requirements
                                Venv(
                                    pkgs={
                                        "tenacity": "==5.0.1",
                                        "protobuf": "==3.8.0",
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
                                                "gevent": "==1.4.0",
                                                "greenlet": "==0.4.14",
                                            }
                                        ),
                                        Venv(
                                            pkgs={"gevent": latest},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        # Python 3.7
                        Venv(
                            pys="3.7",
                            venvs=[
                                Venv(
                                    pkgs={
                                        "tenacity": latest,
                                        "protobuf": latest,
                                    },
                                ),
                                # Minimum requirements
                                Venv(
                                    pkgs={
                                        "tenacity": "==6.0.0",
                                        "protobuf": "==3.8.0",
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
                                                "gevent": "==1.4.0",
                                                "greenlet": "==0.4.14",
                                            }
                                        ),
                                        Venv(
                                            pkgs={"gevent": latest},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        # Python 3.8 + 3.9
                        Venv(
                            pys=["3.8", "3.9"],
                            venvs=[
                                Venv(
                                    pkgs={
                                        "tenacity": latest,
                                        "protobuf": latest,
                                    },
                                ),
                                # Minimum requirements
                                Venv(
                                    pkgs={
                                        "tenacity": "==7.0.0",
                                        "protobuf": "==3.19.0",
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
                                                "gevent": "==20.6.1",
                                                "greenlet": "==0.4.16",
                                            }
                                        ),
                                        Venv(
                                            pkgs={"gevent": latest},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        # Python 3.10
                        Venv(
                            pys="3.10",
                            venvs=[
                                Venv(
                                    pkgs={
                                        "tenacity": latest,
                                        "protobuf": latest,
                                    },
                                ),
                                # Minimum requirements
                                Venv(
                                    pkgs={
                                        "tenacity": "==8.0.0",
                                        "protobuf": "==3.19.0",
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
                                                "gevent": "==21.8.0",
                                                "greenlet": "==1.1.0",
                                            }
                                        ),
                                        Venv(
                                            pkgs={"gevent": latest},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                        # Python 3.11+
                        Venv(
                            pys=select_pys(min_version="3.11"),
                            venvs=[
                                Venv(
                                    pkgs={
                                        "tenacity": latest,
                                        "protobuf": latest,
                                    },
                                ),
                                # Minimum requirements
                                Venv(
                                    pkgs={
                                        "tenacity": "==8.2.0",
                                        "protobuf": "==4.22.0",
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
                                            pkgs={"gevent": ["==22.10.2", latest]},
                                        ),
                                    ],
                                ),
                            ],
                        ),
                    ],
                ),
            ],
        ),
    ],
)
