"""Fast coverage instrumentation for Python 3.8 and 3.9."""

import dis
import sys
from types import CodeType
import typing as t

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info < (3, 10)  # nosec


def add_file_coverage_hook(code: CodeType, hook_func: t.Callable[[str], None], file_path: str) -> CodeType:
    """Add a single hook call at the beginning of a code object for Python 3.8/3.9."""
    
    # Only instrument module-level code for simplicity
    if code.co_name != "<module>":
        return code
    
    # Create new constants list with our hook function and file path
    new_consts = list(code.co_consts)
    hook_index = len(new_consts)
    new_consts.append(hook_func)
    file_path_index = len(new_consts)
    new_consts.append(file_path)
    
    # Create simple bytecode for: hook_func(file_path)
    # Python 3.8/3.9 bytecode
    hook_bytecode = bytes([
        dis.opmap["LOAD_CONST"], hook_index,
        dis.opmap["LOAD_CONST"], file_path_index,
        dis.opmap["CALL_FUNCTION"], 1,
        dis.opmap["POP_TOP"], 0,
    ])
    
    # Prepend hook bytecode to original code
    new_code = hook_bytecode + code.co_code
    
    # Update line number table to account for added instructions
    new_lnotab = _adjust_lnotab(code.co_lnotab, len(hook_bytecode))
    
    # Create new code object
    return code.replace(
        co_code=new_code,
        co_consts=tuple(new_consts),
        co_lnotab=new_lnotab,
        co_stacksize=max(code.co_stacksize, 2),  # Ensure enough stack space
    )


def _adjust_lnotab(original_lnotab: bytes, added_bytes: int) -> bytes:
    """Adjust line number table to account for added bytecode for Python 3.8/3.9."""
    
    if not original_lnotab:
        return original_lnotab
    
    # For Python 3.8/3.9, lnotab format is pairs of (byte_increment, line_increment)
    # Add entry for added instructions with no line change
    added_instructions = added_bytes
    
    # Add entry for added instructions with no line change (0x80 means no line change)
    return bytes([added_instructions, 0]) + original_lnotab
