if __name__ == "__main__":
    from ddtrace._monkey import _PATCHED_MODULES

    assert (
        "flask" in _PATCHED_MODULES
    ), "DD_PATCH_MODULES enables flask, this should override any value set by DD_TRACE_FLASK_ENABLED"

    assert (
        "gevent" in _PATCHED_MODULES
    ), "DD_PATCH_MODULES enables gevent, this should override any value set by DD_TRACE_GEVENT_ENABLED"

    assert (
        "django" not in _PATCHED_MODULES
    ), "DD_PATCH_MODULES enables django, this setting should override all other configurations"

    assert (
        "boto" in _PATCHED_MODULES
    ), "DD_PATCH_MODULES disables boto, this setting should override all other configurations"

    assert (
        "falcon" not in _PATCHED_MODULES
    ), "DD_PATCH_MODULES disables falcon, this setting override any value set by DD_TRACE_FALCON_ENABLED"

    print("Test success")
