import os


from pathlib import Path  # isort: skip


HERE = Path(__file__).resolve().parent.parent
ddtrace_internal_wheels = list(HERE.rglob("**/ddtrace_internal*.whl"))
internal_wheel_path = str(ddtrace_internal_wheels[0])


if os.getenv("CI_COMMIT_TAG") is not None:
    ddtrace_internal_spec = ""
else:
    ddtrace_internal_spec = f" @ file://{internal_wheel_path}"

install_requires = [
    "bytecode>=0.17.0,<1; python_version>='3.14.0'",
    "bytecode>=0.16.0,<1; python_version>='3.13.0'",
    "bytecode>=0.15.1,<1; python_version~='3.12.0'",
    "bytecode>=0.14.0,<1; python_version~='3.11.0'",
    "bytecode>=0.13.0,<1; python_version<'3.11'",
    f"ddtrace-internal{ddtrace_internal_spec}",
    "envier~=0.6.1",
    "opentelemetry-api>=1,<2",
    "wrapt>=1,<3",
]
