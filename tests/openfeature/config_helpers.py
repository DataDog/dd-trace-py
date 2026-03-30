"""
Helper functions to create properly formatted FFE configurations for tests.
"""


def create_boolean_flag(flag_key, enabled=True, default_value=True):
    """Create a boolean flag with proper server format."""
    return {
        "key": flag_key,
        "enabled": enabled,
        "variationType": "BOOLEAN",
        "variations": {
            "true": {"key": "true", "value": True},
            "false": {"key": "false", "value": False},
        },
        "allocations": [
            {
                "key": "allocation-default",
                "splits": [{"variationKey": "true" if default_value else "false", "shards": []}],
                "doLog": True,
            }
        ],
    }


def create_string_flag(flag_key, value, enabled=True):
    """Create a string flag with proper server format."""
    return {
        "key": flag_key,
        "enabled": enabled,
        "variationType": "STRING",
        "variations": {value: {"key": value, "value": value}},
        "allocations": [
            {
                "key": "allocation-default",
                "splits": [{"variationKey": value, "shards": []}],
                "doLog": True,
            }
        ],
    }


def create_integer_flag(flag_key, value, enabled=True):
    """Create an integer flag with proper server format."""
    variation_key = f"var-{value}"
    return {
        "key": flag_key,
        "enabled": enabled,
        "variationType": "INTEGER",
        "variations": {variation_key: {"key": variation_key, "value": value}},
        "allocations": [
            {
                "key": "allocation-default",
                "splits": [{"variationKey": variation_key, "shards": []}],
                "doLog": True,
            }
        ],
    }


def create_float_flag(flag_key, value, enabled=True):
    """Create a float flag with proper server format."""
    variation_key = f"var-{value}"
    return {
        "key": flag_key,
        "enabled": enabled,
        "variationType": "NUMERIC",
        "variations": {variation_key: {"key": variation_key, "value": value}},
        "allocations": [
            {
                "key": "allocation-default",
                "splits": [{"variationKey": variation_key, "shards": []}],
                "doLog": True,
            }
        ],
    }


def create_json_flag(flag_key, value, enabled=True):
    """Create a JSON flag with proper server format."""
    variation_key = "var-object"
    return {
        "key": flag_key,
        "enabled": enabled,
        "variationType": "JSON",
        "variations": {variation_key: {"key": variation_key, "value": value}},
        "allocations": [
            {
                "key": "allocation-default",
                "splits": [{"variationKey": variation_key, "shards": []}],
                "doLog": True,
            }
        ],
    }


def create_config(*flags):
    """
    Create a complete FFE configuration with proper server format.

    Args:
        *flags: Flag dictionaries created by create_*_flag functions

    Returns:
        Complete configuration dict
    """
    config = {
        "id": "test-config-1",
        "createdAt": "2025-10-31T00:00:00Z",
        "format": "SERVER",
        "environment": {"name": "test"},
        "flags": {},
    }

    for flag in flags:
        config["flags"][flag["key"]] = flag

    return config
