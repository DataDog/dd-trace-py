"""AppSec hooks for integrations, loaded through the ddtrace.integration_hooks entry point.

These modules must not import ddtrace.appsec at module scope: they load whenever their
integration is patched, including in processes where AppSec cannot be enabled.
"""
