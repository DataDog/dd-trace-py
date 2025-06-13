#!/bin/bash

# A script to separate dependency installation and test execution for riot-based tests.

set -euo pipefail

usage() {
    echo "Usage: $0 install <venv_name> <python_version>"
    echo "       $0 run <venv_name> <python_version> [<pytest_args>...]"
    echo ""
    echo "This script helps split the testing process into two stages:"
    echo "1. 'install': Installs dependencies for a given riot venv."
    echo "2. 'run': Executes tests for a given riot venv, assuming dependencies are already installed."
    echo ""
    echo "Example:"
    echo "  # Install dependencies for requests on Python 3.8"
    echo "  ./scripts/dd-test.sh install requests 3.8"
    echo ""
    echo "  # Run all tests for requests on Python 3.8"
    echo "  ./scripts/dd-test.sh run requests 3.8"
    echo ""
    echo "  # Run specific tests for requests on Python 3.8"
    echo "  ./scripts/dd-test.sh run requests 3.8 tests/contrib/requests/test_requests.py -vv"
    exit 1
}

if [ "$#" -lt 3 ]; then
    usage
fi

ACTION=$1
VENV_NAME=$2
PYTHON_VERSION=$3
shift 3

# --- ACTION: install ---
if [ "$ACTION" == "install" ]; then
    echo "--- Installing dependencies for $VENV_NAME on Python $PYTHON_VERSION ---"

    # Generate requirement files if they don't exist by running 'riot list'
    # This is how riot discovers and resolves dependencies.
    if [ ! -d ".riot/requirements" ]; then
        echo "Running 'riot list' to generate requirement files..."
        riot list > /dev/null
    fi

    # Find the hash for the specified venv and python version
    echo "Looking up hash for $VENV_NAME on Python $PYTHON_VERSION..."
    # The `riot list` command can return multiple hashes for the same venv and python version
    # if the riotfile defines a matrix of dependencies. We select the last one, which is
    # usually the one with the 'latest' dependencies.
    HASH=$(riot list "$VENV_NAME" | grep "Interpreter(_hint='$PYTHON_VERSION')" | awk '{print $2}' | tail -n 1)

    if [ -z "$HASH" ]; then
        echo "Error: Could not find hash for venv '$VENV_NAME' on Python $PYTHON_VERSION." >&2
        echo "Please ensure the venv name and python version are correct." >&2
        exit 1
    fi
    echo "Found hash: $HASH"

    REQS_FILE=".riot/requirements/${HASH}.txt"

    if [ ! -f "$REQS_FILE" ]; then
        echo "Error: Requirements file $REQS_FILE not found, even after running 'riot list'." >&2
        exit 1
    fi

    echo "Installing dependencies from $REQS_FILE..."
    # Assuming pip is available and configured for the correct python environment.
    pip install -r "$REQS_FILE"

    echo "--- Dependencies installed successfully. ---"
    exit 0
fi

# --- ACTION: run ---
if [ "$ACTION" == "run" ]; then
    echo "--- Running tests for $VENV_NAME on Python $PYTHON_VERSION ---"

    # Determine the base test path from the venv name.
    # This is a convention in this project.
    TEST_PATH=""
    if [ -d "tests/${VENV_NAME}" ]; then
        TEST_PATH="tests/${VENV_NAME}"
    elif [ -d "tests/contrib/${VENV_NAME}" ]; then
        TEST_PATH="tests/contrib/${VENV_NAME}"
    fi

    PYTEST_ARGS=("$@")
    # If no specific test files/args are passed, use the determined path.
    if [ ${#PYTEST_ARGS[@]} -eq 0 ] && [ -n "$TEST_PATH" ]; then
        PYTEST_ARGS=("$TEST_PATH")
    elif [ ${#PYTEST_ARGS[@]} -eq 0 ] && [ -z "$TEST_PATH" ]; then
        echo "Error: No pytest arguments provided and could not determine a default test path for '$VENV_NAME'." >&2
        exit 1
    fi

    echo "Running pytest with arguments: ${PYTEST_ARGS[*]}"

    # The user should ensure that any necessary environment variables (e.g., DD_AGENT_PORT)
    # are set when calling this script.
    pytest "${PYTEST_ARGS[@]}"

    echo "--- Test run finished. ---"
    exit 0
fi

echo "Error: Invalid action '$ACTION'. Must be 'install' or 'run'." >&2
usage 