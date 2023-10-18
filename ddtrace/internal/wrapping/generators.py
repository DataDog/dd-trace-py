import sys

import bytecode as bc
from bytecode import Instr

from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY


# -----------------------------------------------------------------------------
# Generator Wrapping
# -----------------------------------------------------------------------------
# DEV: This is roughly equivalent to
#
# __ddgen = wrapper(wrapped, args, kwargs)
# __ddgensend = __ddgen.send
# try:
#     value = next(__ddgen)
#     while True:
#         try:
#             tosend = yield value
#         except GeneratorExit:
#             return __ddgen.close()
#         except:
#             value = __ddgen.throw(*sys.exc_info())
#         else:
#             value = __ddgensend(tosend)
# except StopIteration:
#     return
# -----------------------------------------------------------------------------
if PY >= (3, 11):

    def wrap_generator(instrs, code, lineno):
        instrs[0:0] = [
            Instr("RETURN_GENERATOR", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
        ]

        stopiter = bc.Label()
        loop = bc.Label()
        genexit = bc.Label()
        exc = bc.Label()
        propagate = bc.Label()
        _yield = bc.Label()

        try_stopiter = bc.TryBegin(stopiter, push_lasti=False)
        try_stopiter_2 = bc.TryBegin(stopiter, push_lasti=False)
        try_except = bc.TryBegin(genexit, push_lasti=True)

        instrs[-1:] = [
            try_stopiter,
            Instr("COPY", 1, lineno=lineno),
            Instr("STORE_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "send", lineno=lineno),
            Instr("STORE_FAST", "__ddgensend", lineno=lineno),
            Instr("PUSH_NULL", lineno=lineno),
            Instr("LOAD_CONST", next, lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            loop,
            Instr("PRECALL", 1, lineno=lineno),
            Instr("CALL", 1, lineno=lineno),
            bc.TryEnd(try_stopiter),
            _yield,
            try_except,
            Instr("YIELD_VALUE", lineno=lineno),
            Instr("RESUME", 1, lineno=lineno),
            Instr("PUSH_NULL", lineno=lineno),
            Instr("SWAP", 2, lineno=lineno),
            Instr("LOAD_FAST", "__ddgensend", lineno=lineno),
            Instr("SWAP", 2, lineno=lineno),
            Instr("JUMP_BACKWARD", loop, lineno=lineno),
            bc.TryEnd(try_except),
            try_stopiter_2,
            genexit,  # except GeneratorExit:
            Instr("PUSH_EXC_INFO", lineno=lineno),
            Instr("LOAD_CONST", GeneratorExit, lineno=lineno),
            Instr("CHECK_EXC_MATCH", lineno=lineno),
            Instr("POP_JUMP_FORWARD_IF_FALSE", exc, lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_METHOD", "close", lineno=lineno),
            Instr("PRECALL", 0, lineno=lineno),
            Instr("CALL", 0, lineno=lineno),
            Instr("SWAP", 2, lineno=lineno),
            Instr("POP_EXCEPT", lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
            exc,  # except:
            Instr("POP_TOP", lineno=lineno),
            Instr("PUSH_NULL", lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "throw", lineno=lineno),
            Instr("PUSH_NULL", lineno=lineno),
            Instr("LOAD_CONST", sys.exc_info, lineno=lineno),
            Instr("PRECALL", 0, lineno=lineno),
            Instr("CALL", 0, lineno=lineno),
            Instr("CALL_FUNCTION_EX", 0, lineno=lineno),
            Instr("SWAP", 2, lineno=lineno),
            Instr("POP_EXCEPT", lineno=lineno),
            Instr("JUMP_BACKWARD", _yield, lineno=lineno),
            bc.TryEnd(try_stopiter_2),
            stopiter,  # except StopIteration:
            Instr("PUSH_EXC_INFO", lineno=lineno),
            Instr("LOAD_CONST", StopIteration, lineno=lineno),
            Instr("CHECK_EXC_MATCH", lineno=lineno),
            Instr("POP_JUMP_FORWARD_IF_FALSE", propagate, lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_EXCEPT", lineno=lineno),
            Instr("LOAD_CONST", None, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
            propagate,
            Instr("RERAISE", 0, lineno=lineno),
        ]


else:

    def _compare_exc(label, lineno):
        """Compat helper for comparing exceptions."""
        if PY < (3, 9):
            return Instr("COMPARE_OP", bc.Compare.EXC_MATCH, lineno=lineno)
        return Instr("JUMP_IF_NOT_EXC_MATCH", label, lineno=lineno)

    def _jump_if_false(label, lineno):
        """Compat helper for jumping if false after comparing exceptions."""
        if PY < (3, 9):
            return Instr("POP_JUMP_IF_FALSE", label, lineno=lineno)
        return Instr("NOP", lineno=lineno)

    def _end_finally(lineno):
        """Compat helper for ending finally blocks."""
        if PY < (3, 9):
            return Instr("END_FINALLY", lineno=lineno)
        if PY < (3, 10):
            return Instr("RERAISE", lineno=lineno)
        return Instr("RERAISE", 0, lineno=lineno)

    def _pop_except(lineno):
        """Compat helper for popping except blocks."""
        if PY >= (3,):
            return Instr("POP_EXCEPT", lineno=lineno)
        return Instr("NOP", lineno=lineno)

    def _setup_block(label, lineno):
        if PY < (3, 8):
            return Instr("SETUP_EXCEPT", label, lineno=lineno)
        return Instr("SETUP_FINALLY", label, lineno=lineno)

    def _call_variadic(lineno):
        if PY < (3, 6):
            return Instr("CALL_FUNCTION_VAR", 0, lineno=lineno)
        return Instr("CALL_FUNCTION_EX", 0, lineno=lineno)

    def wrap_generator(instrs, code, lineno):
        stopiter = bc.Label()
        loop = bc.Label()
        genexit = bc.Label()
        exc = bc.Label()
        propagate = bc.Label()
        _yield = bc.Label()

        instrs[-1:] = [
            _setup_block(stopiter, lineno),
            Instr("DUP_TOP", lineno=lineno),
            Instr("STORE_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "send", lineno=lineno),
            Instr("STORE_FAST", "__ddgensend", lineno=lineno),
            Instr("LOAD_CONST", next, lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            loop,
            Instr("CALL_FUNCTION", 1, lineno=lineno),
            _yield,
            _setup_block(genexit, lineno=lineno),
            Instr("YIELD_VALUE", lineno=lineno),
            Instr("POP_BLOCK", lineno=lineno),
            Instr("LOAD_FAST", "__ddgensend", lineno=lineno),
            Instr("ROT_TWO", lineno=lineno),
            Instr("JUMP_ABSOLUTE", loop, lineno=lineno),
            genexit,  # except GeneratorExit:
            Instr("DUP_TOP", lineno=lineno),
            Instr("LOAD_CONST", GeneratorExit, lineno=lineno),
            _compare_exc(exc, lineno),
            _jump_if_false(exc, lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "close", lineno=lineno),
            Instr("CALL_FUNCTION", 0, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
            exc,  # except:
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("LOAD_FAST", "__ddgen", lineno=lineno),
            Instr("LOAD_ATTR", "throw", lineno=lineno),
            Instr("LOAD_CONST", sys.exc_info, lineno=lineno),
            Instr("CALL_FUNCTION", 0, lineno=lineno),
            _call_variadic(lineno),
            # DEV: We cannot use ROT_FOUR because it was removed in 3.5 and added
            # back in 3.8
            Instr("STORE_FAST", "__value", lineno=lineno),
            _pop_except(lineno),
            Instr("LOAD_FAST", "__value", lineno=lineno),
            Instr("JUMP_ABSOLUTE", _yield, lineno=lineno),
            stopiter,  # except StopIteration:
            Instr("DUP_TOP", lineno=lineno),
            Instr("LOAD_CONST", StopIteration, lineno=lineno),
            _compare_exc(propagate, lineno),
            _jump_if_false(propagate, lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            Instr("POP_TOP", lineno=lineno),
            _pop_except(lineno),
            Instr("LOAD_CONST", None, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
            propagate,
            _end_finally(lineno),
            Instr("LOAD_CONST", None, lineno=lineno),
            Instr("RETURN_VALUE", lineno=lineno),
        ]
