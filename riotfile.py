# type: ignore
from typing import List
from typing import Tuple

from riot import Venv
from riot import latest


SUPPORTED_PYTHON_VERSIONS = [(2, 7), (3, 5), (3, 6), (3, 7), (3, 8), (3, 9), (3, 10)]  # type: List[Tuple[int, int]]


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
    ['2.7', '3.5', '3.6', '3.7', '3.8', '3.9', '3.10']
    >>> select_pys(min_version='3')
    ['3.5', '3.6', '3.7', '3.8', '3.9', '3.10']
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
        "hypothesis": latest,
    },
    env={
        "DD_TESTING_RAISE": "1",
    },
    venvs=[
        Venv(
            pys=["3"],
            pkgs={"black": "==21.4b2", "isort": [latest]},
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
                "mypy": latest,
                "types-attrs": latest,
                "types-protobuf": latest,
                "types-setuptools": latest,
                "types-six": latest,
            },
        ),
        Venv(
            pys=["3"],
            pkgs={"codespell": "==2.1.0"},
            venvs=[
                Venv(
                    name="codespell",
                    command="codespell ddtrace/ tests/",
                ),
                Venv(
                    name="hook-codespell",
                    command="codespell {cmdargs}",
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
            name="docs",
            pys=["3"],
            pkgs={
                "cython": latest,
                "reno[sphinx]": latest,
                "sphinx": "~=4.3.2",
                "sphinxcontrib-spelling": latest,
                "PyEnchant": latest,
            },
            command="scripts/build-docs",
        ),
        Venv(
            name="appsec",
            pys=select_pys(),
            command="pytest {cmdargs} tests/appsec",
        ),
        Venv(
            pys=select_pys(),
            pkgs={"pytest-benchmark": latest, "msgpack": latest},
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
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "msgpack": latest,
                        "attrs": ["==19.2.0", latest],
                        "packaging": ["==17.1", latest],
                        "structlog": latest,
                        # httpretty v1.0 drops python 2.7 support
                        "httpretty": "==0.9.7",
                    },
                )
            ],
        ),
        Venv(
            name="integration",
            pys=select_pys(),
            command="pytest --no-cov {cmdargs} tests/integration/",
            pkgs={"msgpack": [latest]},
            venvs=[
                Venv(
                    name="integration-v5",
                    env={
                        "AGENT_VERSION": "v5",
                    },
                ),
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
            name="runtime",
            command="pytest {cmdargs} tests/runtime/",
            venvs=[Venv(pys=select_pys(), pkgs={"msgpack": latest})],
        ),
        Venv(
            name="ddtracerun",
            command="pytest {cmdargs} --no-cov tests/commands/test_runner.py",
            pys=select_pys(),
            pkgs={
                "redis": latest,
                "gevent": latest,
            },
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
            name="test_logging",
            command="pytest {cmdargs} tests/contrib/logging",
            pys=select_pys(),
        ),
        Venv(
            name="falcon",
            command="pytest {cmdargs} tests/contrib/falcon",
            venvs=[
                # Falcon 1.x
                # Python 2.7+
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "falcon": [
                            "~=1.4.1",
                            "~=1.4",  # latest 1.x
                        ]
                    },
                ),
                # Falcon 2.x
                # Python 3.5+
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "falcon": [
                            "~=2.0.0",
                            "~=2.0",  # latest 2.x
                        ]
                    },
                ),
                # Falcon 3.x
                # Python 3.5+
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
            name="celery",
            command="pytest {cmdargs} tests/contrib/celery",
            pkgs={"more_itertools": "<8.11.0"},
            venvs=[
                # Non-4.x celery should be able to use the older redis lib, since it locks to an older kombu
                Venv(
                    # Use <=3.5 to avoid setuptools >=58 which removed `use_2to3` which is needed by celery<4
                    # https://github.com/pypa/setuptools/issues/2086
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        "pytest": "~=3.10",
                        "celery": "~=3.0",  # most recent 3.x.x release
                        "redis": "~=2.10.6",
                    },
                ),
                # 4.x celery bumps kombu to 4.4+, which requires redis 3.2 or later, this tests against
                # older redis with an older kombu, and newer kombu/newer redis.
                # https://github.com/celery/kombu/blob/3e60e6503a77b9b1a987cf7954659929abac9bac/Changelog#L35
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "pytest": "~=3.10",
                        "celery": [
                            "~=4.0.2",
                            "~=4.1.1",
                        ],
                        "redis": "~=2.10.6",
                        "kombu": "~=4.3.0",
                    },
                ),
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "pytest": "~=3.10",
                        "celery": [
                            "~=4.0.2",
                            "~=4.1.1",
                        ],
                        "redis": "~=3.5",
                        "kombu": "~=4.4.0",
                    },
                ),
                # Celery 4.2 is now limited to Kombu 4.3
                # https://github.com/celery/celery/commit/1571d414461f01ae55be63a03e2adaa94dbcb15d
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "pytest": "~=3.10",
                        "celery": "~=4.2.2",
                        "redis": "~=2.10.6",
                        "kombu": "~=4.3.0",
                    },
                ),
                # Celery 4.3 wants Kombu >= 4.4 and Redis >= 3.2
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "pytest": "~=3.10",
                        "celery": [
                            "~=4.3.1",
                            "~=4.4.7",
                            "~=4.4",  # most recent 4.x
                        ],
                        "redis": "~=3.5",
                        "kombu": "~=4.4",
                    },
                ),
                # Celery 5.x wants Python 3.6+
                Venv(
                    pys=select_pys(min_version="3.6"),
                    env={
                        # https://docs.celeryproject.org/en/v5.0.5/userguide/testing.html#enabling
                        "PYTEST_PLUGINS": "celery.contrib.pytest",
                    },
                    pkgs={
                        "celery": [
                            # Pin until https://github.com/celery/celery/issues/6829 is resolved.
                            # "~=5.0.5",
                            "==5.0.5",
                            "~=5.0",  # most recent 5.x
                            latest,
                        ],
                        "redis": "~=3.5",
                    },
                ),
            ],
        ),
        Venv(
            name="cherrypy",
            command="python -m pytest {cmdargs} tests/contrib/cherrypy",
            venvs=[
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "cherrypy": [
                            ">=11,<12",
                            ">=12,<13",
                            ">=13,<14",
                            ">=14,<15",
                            ">=15,<16",
                            ">=16,<17",
                            ">=17,<18",
                        ],
                        "more_itertools": "<8.11.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.5"),
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
                    # Use <=3.5 to avoid setuptools >=58 which dropped `use_2to3` which is needed by pymongo>=3.4
                    # https://github.com/pypa/setuptools/issues/2086
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        "pymongo": [
                            ">=3.0,<3.1",
                            ">=3.1,<3.2",
                            ">=3.2,<3.3",
                            ">=3.3,<3.4",
                        ],
                    },
                ),
                Venv(
                    # pymongo 3.4 is incompatible with Python>=3.8
                    # AttributeError: module 'platform' has no attribute 'linux_distribution'
                    pys=select_pys(max_version="3.7"),
                    pkgs={
                        "pymongo": ">=3.4,<3.5",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.9"),
                    pkgs={
                        "pymongo": [
                            ">=3.5,<3.6",
                            ">=3.6,<3.7",
                            ">=3.7,<3.8",
                            ">=3.8,<3.9",
                            ">=3.9,<3.10",
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "pymongo": [
                            ">=3.10,<3.11",
                            ">=3.12,<3.13",
                            ">=4.0,<4.1",
                            latest,
                        ],
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
        # 4.0     3.8, 3.9, 3.10
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
                "psycopg2": ["~=2.8.0"],
                "pytest-django": "==3.10.0",
                "pylibmc": latest,
                "python-memcached": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "django": [">=1.8,<1.9", ">=1.11,<1.12"],
                    },
                ),
                Venv(
                    pys=["3.5"],
                    pkgs={
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.9"),
                    pkgs={"django": [">=2.0,<2.1"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "django": [
                            ">=2.1,<2.2",
                            ">=2.2,<2.3",
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "django": [
                            "~=3.0",
                            "~=3.0.0",
                            "~=3.1.0",
                            "~=3.2.0",
                        ],
                        "channels": ["~=3.0", latest],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "django": [
                            "~=4.0.0",
                            latest,
                        ],
                        "channels": ["~=3.0", latest],
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
                    pys=["3.5"],
                    pkgs={
                        "django_hosts": ["~=4.0"],
                        "django": ["~=2.2"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "django_hosts": ["~=4.0"],
                        "django": [
                            "~=2.2",
                            "~=3.2",
                        ],
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
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "django": "==1.11",
                        "djangorestframework": [">=3.4,<3.5", ">=3.7,<3.8"],
                        "pytest-django": "==3.10.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.9"),
                    pkgs={
                        "django": ">=2.2,<2.3",
                        "djangorestframework": [">=3.8,<3.9", ">=3.9,<3.10", latest],
                        "pytest-django": "==3.10.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "django": ">=3.0,<3.1",
                        "djangorestframework": ">=3.10,<3.11",
                        "pytest-django": "==3.10.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "django": "~=3.2",
                        "djangorestframework": ">=3.11,<3.12",
                        "pytest-django": "==3.10.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "django": "~=4.0",
                        "djangorestframework": ["~=3.13", latest],
                        "pytest-django": "==3.10.0",
                    },
                ),
            ],
        ),
        Venv(
            name="elasticsearch",
            command="pytest {cmdargs} tests/contrib/elasticsearch/test_elasticsearch.py",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.8"),
                    pkgs={
                        "elasticsearch": [
                            "~=1.6.0",
                            "~=1.7.0",
                            "~=1.8.0",
                            "~=1.9.0",
                            "~=2.3.0",
                            "~=2.4.0",
                            "~=5.1.0",
                            "~=5.2.0",
                            "~=5.3.0",
                            "~=5.4.0",
                            "~=6.3.0",
                            "~=6.4.0",
                            "~=6.8.0",
                            "~=7.0.0",
                            "~=7.1.0",
                            "~=7.5.0",
                        ]
                    },
                ),
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "elasticsearch": [
                            "~=7.6.0",
                            "~=7.8.0",
                            "~=7.10.0",
                            # FIXME: Elasticsearch introduced a breaking change in 7.14
                            # which makes it incompatible with previous major versions.
                            # latest,
                        ]
                    },
                ),
                Venv(pys=select_pys(), pkgs={"elasticsearch1": ["~=1.10.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch2": ["~=2.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch5": ["~=5.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch6": ["~=6.4.0", "~=6.8.0", latest]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch7": ["~=7.6.0", "~=7.8.0", "~=7.10.0"]}),
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
                        "elasticsearch2": [latest],
                        "elasticsearch5": [latest],
                        "elasticsearch6": [latest],
                        "elasticsearch7": ["<7.14.0"],
                    },
                ),
            ],
        ),
        Venv(
            name="flask",
            # TODO: Re-enable coverage for Flask tests
            command="pytest --no-cov {cmdargs} tests/contrib/flask",
            pkgs={"blinker": latest},
            venvs=[
                # Flask == 0.12.0
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "flask": ["~=0.12.0"],
                        "pytest": "~=3.0",
                        "more_itertools": "<8.11.0",
                    },
                ),
                Venv(
                    pys=select_pys(max_version="3.9"),
                    # TODO: Re-enable coverage for Flask tests
                    command="python tests/ddtrace_run.py pytest --no-cov {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DATADOG_SERVICE_NAME": "test.flask.service",
                        "DATADOG_PATCH_MODULES": "jinja2:false",
                    },
                    pkgs={"flask": ["~=0.12.0"], "pytest": "~=3.0", "more_itertools": "<8.11.0"},
                ),
                # Flask 1.x.x
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "flask": [
                            "~=1.0.0",
                            "~=1.1.0",
                            "~=1.0",  # latest 1.x
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(),
                    # TODO: Re-enable coverage for Flask tests
                    command="python tests/ddtrace_run.py pytest --no-cov {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DATADOG_SERVICE_NAME": "test.flask.service",
                        "DATADOG_PATCH_MODULES": "jinja2:false",
                    },
                    pkgs={
                        "flask": [
                            "~=1.0.0",
                            "~=1.1.0",
                            "~=1.0",  # latest 1.x
                        ],
                    },
                ),
                # Flask >= 2.0.0
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "flask": [
                            "~=2.0.0",
                            "~=2.0",  # latest 2.x
                            latest,
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    # TODO: Re-enable coverage for Flask tests
                    command="python tests/ddtrace_run.py pytest --no-cov {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DATADOG_SERVICE_NAME": "test.flask.service",
                        "DATADOG_PATCH_MODULES": "jinja2:false",
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
            # TODO: Re-enable coverage for Flask tests
            command="pytest --no-cov {cmdargs} tests/contrib/flask_cache",
            pkgs={
                "python-memcached": latest,
                "redis": "~=2.0",
                "blinker": latest,
            },
            venvs=[
                Venv(
                    pys=select_pys(max_version="2.7"),
                    pkgs={
                        "flask": ["~=0.10.0", "~=0.11.0"],
                        "Werkzeug": ["<1.0"],
                        "Flask-Cache": ["~=0.12.0"],
                        "werkzeug": "<1.0",
                        "pytest": "~=3.0",
                        "more_itertools": "<8.11.0",
                    },
                ),
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "flask": ["~=0.10.0", "~=0.11.0", "~=0.12.0"],
                        "Werkzeug": ["<1.0"],
                        "Flask-Cache": ["~=0.13.0", latest],
                        "werkzeug": "<1.0",
                        "pytest": "~=3.0",
                        "more_itertools": "<8.11.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3"),
                    pkgs={
                        "flask": ["~=1.0.0", "~=1.1.0", latest],
                        "flask-caching": ["~=1.10.0", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="mako",
            command="pytest {cmdargs} tests/contrib/mako",
            pys=select_pys(),
            pkgs={"mako": ["<1.0.0", "~=1.0.0", "~=1.1.0", latest]},
        ),
        Venv(
            name="mysql",
            command="pytest {cmdargs} tests/contrib/mysql",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.5"),
                    pkgs={"mysql-connector-python": ["==8.0.5", "<8.0.24"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.9"),
                    pkgs={"mysql-connector-python": ["==8.0.5", ">=8.0", latest]},
                ),
                Venv(
                    pys=select_pys(min_version="3.10"),
                    pkgs={"mysql-connector-python": [">=8.0", latest]},
                ),
            ],
        ),
        Venv(
            name="psycopg",
            command="pytest {cmdargs} tests/contrib/psycopg",
            venvs=[
                Venv(
                    pys=["2.7"],
                    # DEV: Use `psycopg2-binary` so we don't need PostgreSQL dev headers
                    pkgs={"psycopg2-binary": ["~=2.7.0", "~=2.8.0"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    # 2.7.x should also work, but it is from 2019
                    # DEV: Use `psycopg2-binary` so we don't need PostgreSQL dev headers
                    pkgs={"psycopg2-binary": ["~=2.8.0", "~=2.9.0", latest]},
                ),
            ],
        ),
        Venv(
            name="pymemcache",
            pys=select_pys(),
            pkgs={
                "pymemcache": [
                    "~=1.4",  # Most recent 1.x release
                    "~=2.0",  # Most recent 2.x release
                    "~=3.0.1",
                    "~=3.1.1",
                    "~=3.2.0",
                    "~=3.3.0",
                    "~=3.4.2",
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
            pkgs={
                "pynamodb": [">=4.0,<4.1", ">=4.1,<4.2", ">=4.2,<4.3", ">=4.3,<4.4", latest],
            },
            venvs=[
                Venv(pys=select_pys(min_version="3.5"), pkgs={"moto": ">=1.0,<2.0"}),
                Venv(
                    pys=["2.7"],
                    pkgs={
                        "moto": ">=1.0,<2.0",
                        "rsa": "<4.7.1",
                    },
                ),
            ],
        ),
        Venv(
            name="starlette",
            command="pytest {cmdargs} tests/contrib/starlette",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "starlette": [">=0.13,<0.14", ">=0.14,<0.15", latest],
                        "httpx": latest,
                        "pytest-asyncio": latest,
                        "requests": latest,
                        "aiofiles": latest,
                        # Pinned until https://github.com/encode/databases/issues/298 is resolved.
                        "sqlalchemy": "~=1.3.0",
                        "aiosqlite": latest,
                        "databases": latest,
                    },
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
                            pys=select_pys(max_version="3.9"),
                            pkgs={
                                "sqlalchemy": ["~=1.0.0", "~=1.1.0", "~=1.2.0", "~=1.3.0", latest],
                                # 2.8.x is the last one support Python 2.7
                                "psycopg2-binary": ["~=2.8.0"],
                                "mysql-connector-python": ["<8.0.24"],
                            },
                        ),
                        Venv(
                            pys=select_pys(min_version="3.6", max_version="3.9"),
                            pkgs={
                                "sqlalchemy": ["~=1.0.0", "~=1.1.0", "~=1.2.0", "~=1.3.0", latest],
                                "psycopg2-binary": latest,
                                "mysql-connector-python": latest,
                            },
                        ),
                        Venv(
                            pys=select_pys(min_version="3.10"),
                            pkgs={
                                "mysql-connector-python": latest,
                                "sqlalchemy": ["~=1.2.0", "~=1.3.0", latest],
                                "psycopg2-binary": latest,
                                "mysql-connector-python": latest,
                            },
                        ),
                    ],
                ),
            ],
        ),
        Venv(
            name="requests",
            command="pytest {cmdargs} tests/contrib/requests",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.9"),
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
                Venv(
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "requests-mock": ">=1.4",
                        "requests": [
                            ">=2.20,<2.21",
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
            pkgs={"botocore": latest},
            venvs=[
                Venv(pys=select_pys(min_version="3.5"), pkgs={"moto[all]": latest}),
                Venv(pys=["2.7"], pkgs={"moto": ["~=1.0"], "rsa": ["<4.7.1"]}),
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
                    # Use <=3.5 to avoid setuptools >=58 which dropped `use_2to3` which is needed by mongoengine<0.20
                    # https://github.com/pypa/setuptools/issues/2086
                    pys=select_pys(max_version="3.5"),
                    pkgs={
                        # 0.20 dropped support for Python 2.7
                        "mongoengine": [">=0.15,<0.16", ">=0.16,<0.17", ">=0.17,<0.18", ">=0.18,<0.19"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={"mongoengine": [">=0.20,<0.21", ">=0.21,<0.22", ">=0.22,<0.23", latest]},
                ),
            ],
        ),
        Venv(
            name="asgi",
            pkgs={
                "pytest-asyncio": latest,
                "httpx": latest,
                "asgiref": ["~=3.0.0", "~=3.0"],
            },
            pys=select_pys(min_version="3.6"),
            command="pytest {cmdargs} tests/contrib/asgi",
        ),
        Venv(
            name="mariadb",
            command="pytest {cmdargs} tests/contrib/mariadb",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "mariadb": [
                            "~=1.0.0",
                            "~=1.0",
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="pyramid",
            venvs=[
                Venv(
                    command="pytest {cmdargs} tests/contrib/pyramid/test_pyramid.py",
                    pys=select_pys(),
                    pkgs={
                        "requests": [latest],
                        "webtest": [latest],
                        "tests/contrib/pyramid/pserve_app": [latest],
                        "pyramid": [
                            "~=1.7",
                            "~=1.8",
                            "~=1.9",
                            "~=1.10",
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            # aiobotocore: aiobotocore>=1.0 not yet supported
            name="aiobotocore",
            command="pytest {cmdargs} tests/contrib/aiobotocore",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.6"),
                    pkgs={
                        "aiobotocore": ["~=0.2", "~=0.3", "~=0.4"],
                    },
                ),
                # aiobotocore 0.2 and 0.4 do not work because they use async as a reserved keyword
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.8"),
                    pkgs={
                        "aiobotocore": ["~=0.5", "~=0.7", "~=0.8", "~=0.9"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "aiobotocore": ["~=0.10", "~=0.11"],
                    },
                ),
                # aiobotocore dropped Python 3.5 support in 0.12
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "aiobotocore": "~=0.12",
                    },
                ),
            ],
        ),
        Venv(
            name="fastapi",
            command="pytest {cmdargs} tests/contrib/fastapi",
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "fastapi": [">=0.51,<0.52", ">=0.55,<0.56", ">=0.60,<0.61", latest],
                        "httpx": latest,
                        "pytest-asyncio": latest,
                        "requests": latest,
                        "aiofiles": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="pytest",
            command="pytest {cmdargs} tests/contrib/pytest",
            venvs=[
                Venv(
                    pys=["2.7"],
                    # pytest==4.6 is last to support python 2.7
                    pkgs={"pytest": ">=4.0,<4.6", "msgpack": latest},
                ),
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.9"),
                    pkgs={
                        "pytest": [
                            ">=3.0,<4.0",
                            ">=4.0,<5.0",
                            ">=5.0,<6.0",
                            ">=6.0,<7.0",
                            latest,
                        ],
                        "msgpack": latest,
                        "more_itertools": "<8.11.0",
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "pytest": [
                            ">=6.0,<7.0",
                            latest,
                        ],
                        "msgpack": latest,
                        "more_itertools": "<8.11.0",
                    },
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
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "grpcio": [
                            "~=1.12.0",
                            "~=1.20.0",
                            "~=1.21.0",
                            "~=1.22.0",
                        ],
                    },
                ),
                Venv(
                    pys=["3.7"],
                    pkgs={
                        "grpcio": [
                            "~=1.20.0",
                            "~=1.21.0",
                            "~=1.22.0",
                            "~=1.24.0",
                            "~=1.26.0",
                            "~=1.28.0",
                            latest,
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8", max_version="3.9"),
                    pkgs={
                        "grpcio": ["~=1.24.0", "~=1.26.0", "~=1.28.0", latest],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.10"),
                    pkgs={
                        "grpcio": ["~=1.42.0", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="rq",
            command="pytest tests/contrib/rq",
            venvs=[
                Venv(
                    pys=select_pys(max_version="2.7"),
                    pkgs={
                        "rq": [
                            "~=1.0.0",
                            "~=1.1.0",
                            "~=1.2.0",
                            "~=1.3.0",
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "rq": [
                            "~=1.0.0",
                            "~=1.1.0",
                            "~=1.2.0",
                            "~=1.3.0",
                            "~=1.4.0",
                            "~=1.5.0",
                            "~=1.6.0",
                            "~=1.7.0",
                            "~=1.8.0",
                            "~=1.9.0",
                            "~=1.10.0",
                            latest,
                        ],
                        # https://github.com/rq/rq/issues/1469 rq [1.0,1.8] is incompatible with click 8.0+
                        "click": "==7.1.2",
                    },
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
                    "~=0.14.0",
                    "~=0.15.0",
                    "~=0.16.0",
                    "~=0.17.0",
                    "~=0.18.0",
                    "<1.0.0",
                    latest,
                ],
            },
        ),
        Venv(
            name="urllib3",
            command="pytest {cmdargs} tests/contrib/urllib3",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={"urllib3": ["~=1.22.0", ">=1.23,<1.27", latest]},
                ),
                Venv(
                    pys=select_pys(min_version="3.10"),
                    pkgs={"urllib3": [">=1.23,<1.27", latest]},
                ),
            ],
        ),
        Venv(
            name="cassandra",
            venvs=[
                # Python 3.9 requires a more recent release.
                Venv(
                    pys=select_pys(min_version="3.9"),
                    pkgs={"cassandra-driver": latest},
                ),
                # releases 3.7 and 3.8 are broken on Python >= 3.7
                # (see https://github.com/r4fek/django-cassandra-engine/issues/104)
                Venv(
                    pys=["3.7", "3.8"],
                    pkgs={"cassandra-driver": ["~=3.6.0", "~=3.15.0", latest]},
                ),
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={"cassandra-driver": [("~=3.%d.0" % m) for m in range(6, 9)] + ["~=3.15.0", latest]},
                ),
            ],
            command="pytest {cmdargs} tests/contrib/cassandra",
        ),
        Venv(
            name="aiopg",
            venvs=[
                Venv(
                    pys=["3.5", "3.6"],
                    pkgs={
                        "aiopg": ["~=0.12.0", "~=0.15.0"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.7", max_version="3.9"),
                    pkgs={
                        "aiopg": ["~=0.15.0", "~=0.16.0"],  # TODO: add latest
                    },
                ),
            ],
            pkgs={
                "sqlalchemy": latest,
            },
            command="pytest {cmdargs} tests/contrib/aiopg",
        ),
        Venv(
            name="aiohttp",
            command="pytest {cmdargs} tests/contrib/aiohttp",
            pkgs={
                "pytest-aiohttp": [latest],
            },
            venvs=[
                Venv(
                    pys=select_pys(min_version="3.5", max_version="3.6"),
                    pkgs={
                        "aiohttp": ["~=2.0", "~=2.1", "~=2.2", "~=2.3"],
                        "aiohttp_jinja2": ["~=0.12", "~=0.13", "~=0.15"],
                        "async-timeout": ["<4.0.0"],
                        "yarl": "~=0.18.0",
                    },
                ),
                Venv(
                    # Python 3.5 is deprecated for aiohttp >= 3.0
                    pys=select_pys(min_version="3.6", max_version="3.9"),
                    pkgs={
                        "aiohttp": ["~=3.0", "~=3.1", "~=3.2", "~=3.3", "~=3.4", "~=3.5", "~=3.6"],
                        "aiohttp_jinja2": "~=0.15",
                        "yarl": "~=1.0",
                    },
                ),
            ],
        ),
        Venv(
            name="jinja2",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.9"),
                    pkgs={"jinja2": [("~=2.%d.0" % m) for m in range(7, 12)]},
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={"jinja2": ["~=3.0.0", latest]},
                ),
            ],
            command="pytest {cmdargs} tests/contrib/jinja2",
        ),
        Venv(
            name="rediscluster",
            pys=select_pys(),
            command="pytest {cmdargs} tests/contrib/rediscluster",
            pkgs={
                "redis-py-cluster": [">=1.3,<1.4", ">=2.0,<2.1", ">=2.1,<2.2", latest],
            },
        ),
        Venv(
            name="redis",
            pys=select_pys(),
            command="pytest {cmdargs} tests/contrib/redis",
            pkgs={
                "redis": [
                    ">=2.10,<2.11",
                    ">=3.0,<3.1",
                    ">=3.1,<3.2",
                    ">=3.2,<3.3",
                    ">=3.3,<3.4",
                    ">=3.4,<3.5",
                    ">=3.5,<3.6",
                    latest,
                ]
            },
        ),
        Venv(
            name="aredis",
            pys=select_pys(min_version="3.6", max_version="3.9"),
            command="pytest {cmdargs} tests/contrib/aredis",
            pkgs={
                "pytest-asyncio": latest,
                "aredis": [
                    "~=1.1.0",
                    latest,
                ],
            },
        ),
        Venv(
            name="yaaredis",
            pys=select_pys(min_version="3.6", max_version="3.9"),
            command="pytest {cmdargs} tests/contrib/yaaredis",
            pkgs={
                "pytest-asyncio": latest,
                "yaaredis": [
                    "~=2.0.0",
                    latest,
                ],
            },
        ),
        Venv(
            name="snowflake",
            command="pytest {cmdargs} tests/contrib/snowflake",
            pkgs={
                "responses": "~=0.16.0",
            },
            venvs=[
                Venv(
                    # 2.2.0 dropped 2.7 support
                    pys=select_pys(max_version="3.9"),
                    pkgs={
                        "snowflake-connector-python": [
                            "~=2.0.0",
                            "~=2.1.0",
                        ],
                    },
                ),
                Venv(
                    # 2.3.7 dropped 3.5 support
                    pys=select_pys(min_version="3.5", max_version="3.9"),
                    pkgs={
                        "snowflake-connector-python": [
                            "~=2.2.0",
                        ],
                    },
                ),
                Venv(
                    # 2.3.x needs pyarrow >=0.17,<0.18 which does not install on Python 3.9
                    pys=select_pys(min_version="3.6", max_version="3.8"),
                    pkgs={
                        "snowflake-connector-python": [
                            "~=2.3.0",
                        ],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6", max_version="3.9"),
                    pkgs={
                        "snowflake-connector-python": [
                            "~=2.4.0",
                            "~=2.5.0",
                            "~=2.6.0",
                            latest,
                        ],
                    },
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
            pys=select_pys(min_version="3.6"),
            command="pytest {cmdargs} tests/contrib/aioredis",
            pkgs={
                "pytest-asyncio": latest,
                "aioredis": [
                    "~=1.3.0",
                    latest,
                ],
            },
        ),
    ],
)
