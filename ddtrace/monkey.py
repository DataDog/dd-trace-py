
from ddtrace.contrib import autopatch

def patch_all():
    autopatch.autopatch()

def get_patched_modules():
    return autopatch.get_patched_modules()
