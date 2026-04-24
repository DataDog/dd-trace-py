"""
Patch the ``builtins.open`` function to emit file open events for
App and API Protection's Local File Inclusion (LFI) exploit prevention feature.

It is automatically enabled and can be turned off with: DD_TRACE_BUILTINS_ENABLED=false
"""
