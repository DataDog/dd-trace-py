from riot import *


def test_expand():
    specs = list(
        expand_specs(
            [
                ("django-pylibmc", [">=0.6,<0.7"]),
                ("django-redis", [">=4.5,<4.6"]),
                ("pylibmc", [""]),
                ("python-memcached", [""]),
            ]
        )
    )

    assert len(specs) == 1
    (spec,) = specs
    assert len(spec) == 4

    specs = list(
        expand_specs(
            [
                ("django-pylibmc", [">=0.6,<0.7"]),
                ("django-redis", [">=4.5,<4.6"]),
                ("pylibmc", [""]),
                ("python-memcached", [None, ""]),
            ]
        )
    )

    assert len(specs) == 2
    for s in specs:
        assert len(s) == 4


def test_suites_iter():
    suites = [
        Suite(
            name="tracer",
            command="pytest tests/test_tracer.py",
            cases=[
                Case(
                    pys=[2.7, 3.5, 3.6, 3.7, 3.8,],
                    pkgs=[("msgpack", [None, "==0.5.0", ">=0.5,<0.6", ">=0.6.0,<1.0"])],
                ),
            ],
        ),
    ]

    instances = list(suites_iter(suites, pattern=re.compile(".*")))
    assert len(instances) == (5 * 4)

    suites = [
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
                Suite(
                    env=[("TEST_DATADOG_DJANGO_MIGRATION", [None, "1"])],
                    pys=[3.6, 3.7, 3.8],
                    pkgs=[
                        ("django-pylibmc", [">=0.6,<0.7"]),
                        ("django-redis", [">=4.5,<4.6"]),
                        ("pylibmc", [""]),
                        ("python-memcached", [""]),
                        (
                            "django",
                            [">=2.0,<2.1", ">=2.1,<2.2", ">=2.2,<2.3", ">=3.0,<3.1"],
                        ),
                    ],
                ),
            ],
        ),
    ]

    instances = list(suites_iter(suites, pattern=re.compile(".*")))
    assert len(instances) == (2 * 3 * 2) + (2 * 1 * 2) + (2 * 3 * 4)
