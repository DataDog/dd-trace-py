#!/usr/bin/env python3
"""
Comprehensive test file for the environ_checker flake8 plugin.

This file contains examples of ALL environment variable access patterns that should be
detected and flagged by the environ_checker plugin with ENV001 violations.

Usage:
    hatch run lint:flake8 --select=ENV001 scripts/flake8_environ_rule/tests/test_all_patterns.py

Expected: All lines with environment variable access should be flagged with ENV001 errors.
"""

import os
from os import environ
from os import environ as env_dict
from os import getenv
from os import getenv as get_env


# ============================================================================
# SECTION 1: Direct os.environ access patterns
# ============================================================================
value1 = os.environ["HOME"]  # Subscript access
value2 = os.environ.get("PATH")  # Method call
env_copy = os.environ.copy()  # Method call
os.environ.clear()  # Method call
os.environ.update({"NEW_VAR": "value"})  # Method call
removed = os.environ.pop("TEMP_VAR", None)  # Method call
default_val = os.environ.setdefault("DEF", "default")  # Method call
all_keys = os.environ.keys()  # Method call
all_values = os.environ.values()  # Method call
all_items = os.environ.items()  # Method call

# ============================================================================
# SECTION 2: Membership tests and iteration
# ============================================================================
if "HOME" in os.environ:  # Membership test
    pass

# Iteration
for key in os.environ:  # Iteration
    pass
for k, v in os.environ.items():  # Iteration over method call
    pass

# ============================================================================
# SECTION 3: Direct environ usage (imported from os)
# ============================================================================
value3 = environ["USER"]  # Direct environ subscript
value4 = environ.get("SHELL")  # Direct environ method
env_keys = environ.keys()  # Direct environ method

# ============================================================================
# SECTION 4: Aliased environ usage
# ============================================================================
value5 = env_dict["HOME"]  # Aliased environ subscript
value6 = env_dict.get("PATH")  # Aliased environ method

# ============================================================================
# SECTION 5: os.getenv function calls
# ============================================================================
value7 = os.getenv("DEBUG")  # Direct os.getenv
value8 = os.getenv("TIMEOUT", "30")  # Direct os.getenv with default

# ============================================================================
# SECTION 6: Direct getenv calls (imported from os)
# ============================================================================
value9 = getenv("CONFIG")  # Direct getenv
value10 = getenv("MODE", "production")  # Direct getenv with default

# ============================================================================
# SECTION 7: Aliased getenv calls and direct attribute access
# ============================================================================
value11 = get_env("LEVEL")  # Aliased getenv
value12 = get_env("PORT", "8080")  # Aliased getenv with default

# Direct attribute access
env_ref = os.environ  # Direct attribute access
getenv_ref = os.getenv  # Direct attribute access (should this be caught?)

print("All patterns tested")
