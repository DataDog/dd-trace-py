from tests.appsec.iast.fixtures.aspects.obj_callees import *
from tests.appsec.iast.fixtures.aspects.obj_callees import _number_generator
_ng = _number_generator()


def generator_call_bytearray(*args, **kwargs):
    return next(_ng).bytearray(*args, **kwargs)
            

def generator_call_bytes(*args, **kwargs):
    return next(_ng).bytes(*args, **kwargs)
            

def generator_call_capitalize(*args, **kwargs):
    return next(_ng).capitalize(*args, **kwargs)
            

def generator_call_casefold(*args, **kwargs):
    return next(_ng).casefold(*args, **kwargs)
            

def generator_call_decode(*args, **kwargs):
    return next(_ng).decode(*args, **kwargs)
            

def generator_call_encode(*args, **kwargs):
    return next(_ng).encode(*args, **kwargs)
            

def generator_call_extend(*args, **kwargs):
    return next(_ng).extend(*args, **kwargs)
            

def generator_call_format(*args, **kwargs):
    return next(_ng).format(*args, **kwargs)
            

def generator_call_format_map(*args, **kwargs):
    return next(_ng).format_map(*args, **kwargs)
            

def generator_call_ljust(*args, **kwargs):
    return next(_ng).ljust(*args, **kwargs)
            

def generator_call_lower(*args, **kwargs):
    return next(_ng).lower(*args, **kwargs)
            

def generator_call_repr(*args, **kwargs):
    return next(_ng).repr(*args, **kwargs)
            

def generator_call_str(*args, **kwargs):
    return next(_ng).str(*args, **kwargs)
            

def generator_call_swapcase(*args, **kwargs):
    return next(_ng).swapcase(*args, **kwargs)
            

def generator_call_title(*args, **kwargs):
    return next(_ng).title(*args, **kwargs)
            

def generator_call_translate(*args, **kwargs):
    return next(_ng).translate(*args, **kwargs)
            

def generator_call_upper(*args, **kwargs):
    return next(_ng).upper(*args, **kwargs)
            

def generator_call_zfill(*args, **kwargs):
    return next(_ng).zfill(*args, **kwargs)
            