"""
Patch ``pathlib.Path.open`` to emit file open events for
App and API Protection's Local File Inclusion (LFI) exploit prevention.

It is automatically enabled and can be turned off with: DD_TRACE_PATHLIB_ENABLED=false
"""
