"""Fast coverage instrumentation for Python 3.13."""

import dis
import sys
from types import CodeType
import typing as t

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 13) and sys.version_info < (3, 14)  # nosec


def add_file_coverage_hook(code: CodeType, hook_func: t.Callable[[str], None], file_path: str) -> CodeType:
    """Add a single hook call at the beginning of a code object for Python 3.13."""
    
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
    # Python 3.13+ bytecode (same as 3.12, PRECALL was removed in 3.12)
    hook_bytecode = bytes([
        dis.opmap["PUSH_NULL"], 0,
        dis.opmap["LOAD_CONST"], hook_index,
        dis.opmap["LOAD_CONST"], file_path_index,
        dis.opmap["CALL"], 1,
        dis.opmap["POP_TOP"], 0,
    ])
    
    # Prepend hook bytecode to original code
    new_code = hook_bytecode + code.co_code
    
    # Update line number table to account for added instructions
    new_linetable = _adjust_linetable(code.co_linetable, len(hook_bytecode))
    
    # Create new code object
    return code.replace(
        co_code=new_code,
        co_consts=tuple(new_consts),
        co_linetable=new_linetable,
        co_stacksize=max(code.co_stacksize, 2),  # Ensure enough stack space
    )


def _adjust_linetable(original_linetable: bytes, added_bytes: int) -> bytes:
    """Adjust line number table to account for added bytecode for Python 3.13."""
    
    if not original_linetable:
        return original_linetable
    
    # Python 3.13+ format - add entry for added instructions with no line change
    added_instructions = added_bytes // 2
    
    # Add entry for added instructions with no line change
    return bytes([added_instructions - 1, 0x80]) + original_linetable
