from riot import Suite, Case

global_deps = [
    "mock",
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
                ],
                pkgs=[("msgpack", [""])],
            ),
        ],
    ),
]
