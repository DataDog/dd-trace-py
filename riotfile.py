# type: ignore
from typing import List
from typing import Tuple

from riot import Venv
from riot import latest


SUPPORTED_PYTHON_VERSIONS = [(2, 7), (3, 5), (3, 6), (3, 7), (3, 8), (3, 9)]  # type: List[Tuple[int, int]]


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
    ['2.7', '3.5', '3.6', '3.7', '3.8', '3.9']
    >>> select_pys(min_version='3')
    ['3.5', '3.6', '3.7', '3.8', '3.9']
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
        # "coverage": latest,
        # "pytest-cov": latest,
        "opentracing": latest,
        "hypothesis": latest,
    },
    env={
        "DD_TESTING_RAISE": "1",
    },
    venvs=[
        Venv(
            pys=["3"],
            pkgs={"black": "==21.4b2"},
            venvs=[
                Venv(
                    name="fmt",
                    command="black .",
                ),
                Venv(
                    name="black",
                    command="black {cmdargs}",
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
                    command="flake8 {cmdargs} ddtrace/ tests/",
                ),
                Venv(
                    name="hook-flake8",
                    command="flake8 {cmdargs}",
                ),
            ],
        ),
        Venv(
            pys=["3"],
            name="mypy",
            command="mypy {cmdargs}",
            pkgs={
                # TODO: https://mypy-lang.blogspot.com/2021/05/the-upcoming-switch-to-modular-typeshed.html
                "mypy": "<0.900",
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
                "sphinx": latest,
                "sphinxcontrib-spelling": latest,
                "PyEnchant": latest,
            },
            command="scripts/build-docs",
        ),
        Venv(
            name="benchmarks",
            pys=select_pys(),
            pkgs={"pytest-benchmark": latest, "msgpack": latest},
            command="pytest --benchmark-warmup=on {cmdargs} tests/benchmarks",
            venvs=[
                Venv(
                    name="benchmarks-nogc",
                    command="pytest --benchmark-warmup=on --benchmark-disable-gc {cmdargs} tests/benchmarks",
                ),
            ],
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
                    },
                )
            ],
        ),
        Venv(
            name="runtime",
            command="pytest {cmdargs} tests/runtime/",
            venvs=[Venv(pys=select_pys(), pkgs={"msgpack": latest})],
        ),
        Venv(
            name="ddtracerun",
            command="pytest {cmdargs} tests/commands/test_runner.py",
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
        ),
        Venv(
            name="test_logging",
            command="pytest {cmdargs} tests/contrib/logging",
            pys=select_pys(),
        ),
        Venv(
            name="falcon",
            command="pytest {cmdargs} --ignore tests/contrib/falcon/test_autopatch.py tests/contrib/falcon",
            venvs=[
                # Falcon 1.x
                # Python 2.7+
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "falcon": [
                            "~=1.4.1",
                            "~=1.4",  # latest 1.x
                        ]
                    },
                ),
                Venv(
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/falcon/test_autopatch.py",
                    pys=select_pys(),
                    pkgs={
                        "falcon": [
                            "~=1.4.1",
                            "~=1.4",  # latest 1.x
                        ]
                    },
                    env={"DD_SERVICE": "my-falcon"},
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
                Venv(
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/falcon/test_autopatch.py",
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "falcon": [
                            "~=2.0.0",
                            "~=2.0",  # latest 2.x
                        ]
                    },
                    env={"DD_SERVICE": "my-falcon"},
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
                Venv(
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/falcon/test_autopatch.py",
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "falcon": [
                            "~=3.0.0",
                            "~=3.0",  # latest 3.x
                            latest,
                        ]
                    },
                    env={"DD_SERVICE": "my-falcon"},
                ),
            ],
        ),
        Venv(
            name="celery",
            command="pytest {cmdargs} tests/contrib/celery",
            venvs=[
                # Non-4.x celery should be able to use the older redis lib, since it locks to an older kombu
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
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
                        "celery": [
                            "~=4.0.2",
                            "~=4.1.1",
                        ],
                        "redis": "~=2.10.6",
                        "kombu": "~=4.3.0",
                        "pytest": "~=3.10",
                    },
                ),
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
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
                        "celery": "~=4.2.2",
                        "redis": "~=2.10.6",
                        "kombu": "~=4.3.0",
                    },
                ),
                # Celery 4.3 wants Kombu >= 4.4 and Redis >= 3.2
                Venv(
                    pys=select_pys(),
                    pkgs={
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
            command="pytest {cmdargs} tests/contrib/cherrypy",
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
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "cherrypy": [">=18.0,<19", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="pymongo",
            command="pytest {cmdargs} tests/contrib/pymongo",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.7"),
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
                            ">=3.12,<3.13",
                            latest,
                        ],
                        "mongoengine": latest,
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
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
                            ">=3.12,<3.13",
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
            command="pytest {cmdargs} tests/contrib/django",
            venvs=[
                Venv(
                    pys=select_pys(max_version="3.6"),
                    pkgs={
                        "django": [">=1.8,<1.9", ">=1.11,<1.12"],
                        "django-pylibmc": ">=0.6,<0.7",
                        "django-redis": ">=4.5,<4.6",
                        "pylibmc": latest,
                        "pytest-django": "==3.10.0",
                        "python-memcached": latest,
                        "redis": ">=2.10,<2.11",
                        "psycopg2": ["~=2.8.0"],
                    },
                ),
                Venv(
                    pys=["3.5"],
                    pkgs={
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3"],
                        "django-pylibmc": ">=0.6,<0.7",
                        "django-redis": ">=4.5,<4.6",
                        "pylibmc": latest,
                        "pytest-django": "==3.10.0",
                        "python-memcached": latest,
                        "redis": ">=2.10,<2.11",
                        "psycopg2": ["~=2.8.0"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1", latest],
                        "django-pylibmc": ">=0.6,<0.7",
                        "django-redis": ">=4.5,<4.6",
                        "pylibmc": latest,
                        "pytest-django": "==3.10.0",
                        "python-memcached": latest,
                        "redis": ">=2.10,<2.11",
                        "psycopg2": ["~=2.8.0"],
                    },
                ),
                Venv(
                    pys=select_pys(max_version="3.6"),
                    env={"TEST_DATADOG_DJANGO_MIGRATION": "1"},
                    pkgs={
                        "pytest-django": "==3.10.0",
                        "django": [">=1.8,<1.9", ">=1.11,<1.12"],
                    },
                ),
                Venv(
                    pys=["3.5"],
                    env={"TEST_DATADOG_DJANGO_MIGRATION": "1"},
                    pkgs={
                        "pytest-django": "==3.10.0",
                        "django": [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
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
                    pys=select_pys(min_version="3.5"),
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
                        "django": latest,
                        "djangorestframework": ">=3.11,<3.12",
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
                            latest,
                        ]
                    },
                ),
                Venv(pys=select_pys(), pkgs={"elasticsearch1": ["~=1.10.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch2": ["~=2.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch5": ["~=5.5.0"]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch6": ["~=6.4.0", "~=6.8.0", latest]}),
                Venv(pys=select_pys(), pkgs={"elasticsearch7": ["~=7.6.0", "~=7.8.0", "~=7.10.0", latest]}),
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
                        "elasticsearch7": [latest],
                    },
                ),
            ],
        ),
        Venv(
            name="flask",
            command="pytest {cmdargs} tests/contrib/flask",
            pkgs={
                "blinker": latest,
            },
            venvs=[
                # Flask == 0.12.0
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "flask": ["~=0.12.0"],
                        "pytest": "~=3.0",
                    },
                ),
                Venv(
                    pys=select_pys(),
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/flask_autopatch",
                    env={
                        "DATADOG_SERVICE_NAME": "test.flask.service",
                        "DATADOG_PATCH_MODULES": "jinja2:false",
                    },
                    pkgs={
                        "flask": ["~=0.12.0"],
                        "pytest": "~=3.0",
                    },
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
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/flask_autopatch",
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
                    command="python tests/ddtrace_run.py pytest {cmdargs} tests/contrib/flask_autopatch",
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
            command="pytest {cmdargs} tests/contrib/flask_cache",
            pkgs={
                "python-memcached": latest,
                "redis": "~=2.0",
                "blinker": latest,
                "werkzeug": "<1.0",
            },
            venvs=[
                Venv(
                    pys=select_pys(max_version="2.7"),
                    pkgs={
                        "flask": ["~=0.10.0", "~=0.11.0"],
                        "Flask-Cache": ["~=0.12.0"],
                    },
                ),
                Venv(
                    pys=select_pys(),
                    pkgs={
                        "flask": ["~=0.10.0", "~=0.11.0", "~=0.12.0"],
                        "Flask-Cache": ["~=0.13.0", latest],
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
                    pys="2.7",
                    pkgs={"mysql-connector-python": ["==8.0.5", "<8.0.24"]},
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={"mysql-connector-python": ["==8.0.5", ">=8.0", latest]},
                ),
            ],
        ),
        Venv(
            name="psycopg",
            command="pytest {cmdargs} tests/contrib/psycopg",
            venvs=[
                Venv(
                    pys=select_pys(min_version="2.7", max_version="3.6"),
                    pkgs={"psycopg2": ["~=2.7.0", "~=2.8.0", latest]},
                ),
                Venv(
                    pys=["3.7"],
                    pkgs={"psycopg2": ["~=2.7.0", "~=2.8.0", latest]},
                ),
                Venv(
                    pys=select_pys(min_version="3.8"),
                    pkgs={"psycopg2": ["~=2.8.0", latest]},
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
                "moto": ">=1.0,<2.0",
            },
            venvs=[
                Venv(pys=select_pys(min_version="3.5")),
                Venv(
                    pys=["2.7"],
                    pkgs={
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
                    pys=select_pys(max_version="3"),
                    pkgs={
                        "sqlalchemy": ["~=1.0.0", "~=1.1.0", "~=1.2.0", "~=1.3.0", latest],
                        "psycopg2": ["~=2.8.0"],
                        "mysql-connector-python": ["<8.0.24"],
                    },
                ),
                Venv(
                    pys=select_pys(min_version="3.6"),
                    pkgs={
                        "sqlalchemy": ["~=1.0.0", "~=1.1.0", "~=1.2.0", "~=1.3.0", latest],
                        "psycopg2": ["~=2.8.0"],
                        "mysql-connector-python": latest,
                    },
                ),
            ],
        ),
        Venv(
            name="requests",
            command="pytest {cmdargs} tests/contrib/requests",
            venvs=[
                Venv(
                    pys=select_pys(),
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
            venvs=[Venv(pys=select_pys(max_version="3.6"), pkgs={"boto": latest, "moto": ["<1.0"]})],
        ),
        Venv(
            name="botocore",
            command="pytest {cmdargs} tests/contrib/botocore",
            pkgs={"botocore": latest, "moto": [">=1.0,<2.0"]},
            venvs=[
                Venv(pys=select_pys(min_version="3.5")),
                Venv(pys=["2.7"], pkgs={"rsa": ["<4.7.1"]}),
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
                    pys=select_pys(),
                    pkgs={
                        # 0.20 dropped support for Python 2.7
                        "mongoengine": [">=0.15,<0.16", ">=0.16,<0.17", ">=0.17,<0.18", ">=0.18,<0.19"]
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
                    pkgs={"pytest": ">=4.0,<4.6"},
                ),
                Venv(
                    pys=select_pys(min_version="3.5"),
                    pkgs={
                        "pytest": [
                            ">=3.0,<4.0",
                            ">=4.0,<5.0",
                            ">=5.0,<6.0",
                            ">=6.0,<7.0",
                            latest,
                        ],
                    },
                ),
            ],
        ),
        Venv(
            name="grpc",
            command="pytest {cmdargs} tests/contrib/grpc",
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
                    pys=select_pys(min_version="3.8"),
                    pkgs={
                        "grpcio": ["~=1.24.0", "~=1.26.0", "~=1.28.0", latest],
                    },
                ),
            ],
        ),
        Venv(
            name="urllib3",
            pys=select_pys(),
            pkgs={"urllib3": ["~=1.22.0", ">=1.23,<1.27", latest]},
            command="pytest {cmdargs} tests/contrib/urllib3",
        ),
    ],
)
