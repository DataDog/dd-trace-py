from riot import Suite, Case

global_deps = [
    "mock",
    "pytest<4",
    "pytest-benchmark",
]

global_env = [("PYTEST_ADDOPTS", "--color=yes")]

suites = [
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
