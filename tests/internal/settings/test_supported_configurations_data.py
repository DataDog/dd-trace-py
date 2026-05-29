import scripts.supported_configurations as gen


FAKE = {
    "version": "2",
    "supportedConfigurations": {
        "DD_FOO_ENABLED": [{"implementation": "A", "type": "boolean", "default": "true"}],
        "DD_FOO_COUNT": [{"implementation": "A", "type": "int", "default": "7"}],
        "DD_FOO_HOST": [{"implementation": "A", "type": "string", "default": None}],
    },
}


def _exec(src):
    ns: dict = {}
    exec(compile(src, "<generated>", "exec"), ns)
    return ns


def test_emits_types_and_defaults():
    ns = _exec(gen.generate_module(FAKE))
    assert ns["CONFIGURATION_TYPES"] == {
        "DD_FOO_COUNT": "int",
        "DD_FOO_ENABLED": "boolean",
        "DD_FOO_HOST": "string",
    }
    assert ns["CONFIGURATION_DEFAULTS"] == {
        "DD_FOO_COUNT": "7",
        "DD_FOO_ENABLED": "true",
        "DD_FOO_HOST": None,
    }


def test_types_keys_sorted():
    ns = _exec(gen.generate_module(FAKE))
    keys = list(ns["CONFIGURATION_TYPES"])
    assert keys == sorted(keys)
