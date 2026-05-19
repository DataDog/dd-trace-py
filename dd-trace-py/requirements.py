import os


from pathlib import Path  # isort: skip


HERE = Path(__file__).resolve().parent


if os.getenv("CI_COMMIT_TAG") is not None:
    ddtrace_internal_spec = ""
else:
    root = os.getenv("CI_PROJECT_DIR", HERE.parent)
    ddtrace_internal_spec = f" @ file://{root}/pywheels/ddtrace_internal-0.0.0-py3-none-any.whl"

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
