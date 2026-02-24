.. _linting_guidelines:

Linting
=======

This project uses a collection of self-bootstrapping lint scripts under ``scripts/``.
You only need `uv <https://docs.astral.sh/uv/>`_ on your PATH — each script declares
its own pinned dependencies and installs them automatically on first run. If uv itself
is not found, the scripts will attempt to install it for you.

Quick reference
---------------

.. code-block:: bash

    # See all available commands
    $ scripts/lint help

    # Run all checks (default when no command given)
    $ scripts/lint
    $ scripts/lint checks

    # Format code after editing
    $ scripts/lint fmt path/to/file.py

Common workflows
----------------

**After editing a Python file:**

.. code-block:: bash

    $ scripts/lint fmt ddtrace/tracer.py

This runs ``ruff format`` to reformat the file, then ``ruff check --fix`` to
auto-fix any lint violations, then re-validates with style checks.

**Before opening a pull request:**

.. code-block:: bash

    $ scripts/lint checks

This runs the full suite of checks — style, typing, spelling, security, riot
validation, ast-grep analysis, suitespec coverage, and error log validation.

**Check style without modifying files:**

.. code-block:: bash

    $ scripts/lint style
    $ scripts/lint style ddtrace/contrib/flask/

**Type-check specific files:**

.. code-block:: bash

    $ scripts/lint typing ddtrace/tracer.py

**Check spelling in documentation:**

.. code-block:: bash

    $ scripts/lint spelling docs/ releasenotes/

**Security scan:**

.. code-block:: bash

    $ scripts/lint security
    $ scripts/lint security -- -r ddtrace/contrib/

**Format snapshot files after updating tests:**

.. code-block:: bash

    $ scripts/lint fmt-snapshots

Available commands
------------------

Run ``scripts/lint help`` to see all available commands. The commands are
implemented as individual scripts named ``scripts/lint-<command>``, so they
can also be invoked directly if needed.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Command
     - Description
   * - ``checks``
     - Run all checks (default). Equivalent to running all of the below.
   * - ``fmt``
     - Format code with Ruff and re-validate style. Modifies files.
   * - ``style``
     - Validate style with Ruff, cython-lint, clang-format, and cmake-format. Read-only.
   * - ``typing``
     - Type-check with mypy.
   * - ``spelling``
     - Check spelling with codespell across ``ddtrace/``, ``tests/``, ``releasenotes/``, ``docs/``.
   * - ``security``
     - Security scan with bandit.
   * - ``riot``
     - Doctest ``riotfile.py`` to validate venv definitions.
   * - ``sg``
     - Static analysis with ast-grep.
   * - ``sg-test``
     - Validate ast-grep rule definitions.
   * - ``fmt-snapshots``
     - Format snapshot files in ``tests/snapshots/``.
   * - ``scripts-test``
     - Doctest utility scripts (``needs_testrun.py``, ``get-target-milestone.py``, ``suitespec.py``).

Passing arguments
-----------------

Arguments are forwarded to the underlying tool. Use ``--`` to separate
lint-script flags from tool flags where needed:

.. code-block:: bash

    # Type-check a specific directory
    $ scripts/lint typing ddtrace/contrib/

    # Security scan with explicit recursive flag
    $ scripts/lint security -- -r ddtrace/contrib/

    # Spelling check specific paths
    $ scripts/lint spelling docs/ releasenotes/

Each command has a built-in default target (e.g. ``style`` defaults to ``.``,
``spelling`` defaults to ``ddtrace/ tests/ releasenotes/ docs/``), so running
a command with no arguments checks the whole project.
