import sys

import bytecode as bc
from bytecode import Instr

from ddtrace.internal.compat import PYTHON_VERSION_INFO as PY


# -----------------------------------------------------------------------------
# Coroutine and Async Generator Wrapping
# -----------------------------------------------------------------------------
# DEV: The wrapping of async generators is roughly equivalent to
#
# __ddgen = wrapper(wrapped, args, kwargs)
# __ddgensend = __ddgen.asend
# try:
#     value = await __ddgen.__anext__()
#     while True:
#         try:
#             tosend = yield value
#         except GeneratorExit:
#             await __ddgen.aclose()
#         except:
#             value = await __ddgen.athrow(*sys.exc_info())
#         else:
#             value = await __ddgensend(tosend)
# except StopAsyncIteration:
#     return
# -----------------------------------------------------------------------------

if PY >= (3, 12):

    def wrap_async(instrs, code, lineno):
        if bc.CompilerFlags.COROUTINE & code.co_flags:
            # DEV: This is just
            # >>> return await wrapper(wrapped, args, kwargs)
            instrs[0:0] = [
                Instr("RETURN_GENERATOR", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
            ]

            send = bc.Label()
            send_jb = bc.Label()

            instrs[-1:-1] = [
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb,
                Instr("SEND", send, lineno=lineno),
                Instr("YIELD_VALUE", 2, lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb, lineno=lineno),
                send,
            ]

        elif bc.CompilerFlags.ASYNC_GENERATOR & code.co_flags:
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

            send = [bc.Label() for _ in range(3)]
            send_jb = [bc.Label() for _ in range(3)]

            instrs[-1:] = [
                try_stopiter,
                Instr("COPY", 1, lineno=lineno),
                Instr("STORE_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", (False, "asend"), lineno=lineno),
                Instr("STORE_FAST", "__ddgensend", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", (True, "__anext__"), lineno=lineno),
                Instr("CALL", 0, lineno=lineno),
                loop,
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb[0],
                Instr("SEND", send[0], lineno=lineno),
                bc.TryEnd(try_stopiter),
                try_except,
                Instr("YIELD_VALUE", 3, lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb[0], lineno=lineno),
                send[0],
                Instr("END_SEND", lineno=lineno),
                _yield,
                Instr("CALL_INTRINSIC_1", bc.Intrinsic1Op.INTRINSIC_ASYNC_GEN_WRAP, lineno=lineno),
                Instr("YIELD_VALUE", 3, lineno=lineno),
                Instr("RESUME", 1, lineno=lineno),
                Instr("PUSH_NULL", lineno=lineno),
                Instr("SWAP", 2, lineno=lineno),
                Instr("LOAD_FAST", "__ddgensend", lineno=lineno),
                Instr("SWAP", 2, lineno=lineno),
                Instr("CALL", 1, lineno=lineno),
                Instr("JUMP_BACKWARD", loop, lineno=lineno),
                bc.TryEnd(try_except),
                try_stopiter_2,
                genexit,  # except GeneratorExit:
                Instr("PUSH_EXC_INFO", lineno=lineno),
                Instr("LOAD_CONST", GeneratorExit, lineno=lineno),
                Instr("CHECK_EXC_MATCH", lineno=lineno),
                Instr("POP_JUMP_IF_FALSE", exc, lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", (True, "aclose"), lineno=lineno),
                Instr("CALL", 0, lineno=lineno),
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb[1],
                Instr("SEND", send[1], lineno=lineno),
                Instr("YIELD_VALUE", 4, lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb[1], lineno=lineno),
                send[1],
                Instr("END_SEND", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("RETURN_CONST", None, lineno=lineno),
                exc,  # except:
                Instr("POP_TOP", lineno=lineno),
                Instr("PUSH_NULL", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", (False, "athrow"), lineno=lineno),
                Instr("PUSH_NULL", lineno=lineno),
                Instr("LOAD_CONST", sys.exc_info, lineno=lineno),
                Instr("CALL", 0, lineno=lineno),
                Instr("CALL_FUNCTION_EX", 0, lineno=lineno),
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb[2],
                Instr("SEND", send[2], lineno=lineno),
                Instr("YIELD_VALUE", 4, lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb[2], lineno=lineno),
                send[2],
                Instr("END_SEND", lineno=lineno),
                Instr("SWAP", 2, lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("JUMP_BACKWARD", _yield, lineno=lineno),
                bc.TryEnd(try_stopiter_2),
                stopiter,  # except StopAsyncIteration:
                Instr("PUSH_EXC_INFO", lineno=lineno),
                Instr("LOAD_CONST", StopAsyncIteration, lineno=lineno),
                Instr("CHECK_EXC_MATCH", lineno=lineno),
                Instr("POP_JUMP_IF_FALSE", propagate, lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("RETURN_CONST", None, lineno=lineno),
                propagate,  # finally:
                Instr("RERAISE", 0, lineno=lineno),
            ]


elif PY >= (3, 11):

    def wrap_async(instrs, code, lineno):
        if bc.CompilerFlags.COROUTINE & code.co_flags:
            # DEV: This is just
            # >>> return await wrapper(wrapped, args, kwargs)
            instrs[0:0] = [
                Instr("RETURN_GENERATOR", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
            ]

            send = bc.Label()
            send_jb = bc.Label()

            instrs[-1:-1] = [
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb,
                Instr("SEND", send, lineno=lineno),
                Instr("YIELD_VALUE", lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb, lineno=lineno),
                send,
            ]

        elif bc.CompilerFlags.ASYNC_GENERATOR & code.co_flags:
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

            send = [bc.Label() for _ in range(3)]
            send_jb = [bc.Label() for _ in range(3)]

            instrs[-1:] = [
                try_stopiter,
                Instr("COPY", 1, lineno=lineno),
                Instr("STORE_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "asend", lineno=lineno),
                Instr("STORE_FAST", "__ddgensend", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_METHOD", "__anext__", lineno=lineno),
                Instr("PRECALL", 0, lineno=lineno),
                Instr("CALL", 0, lineno=lineno),
                loop,
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb[0],
                Instr("SEND", send[0], lineno=lineno),
                bc.TryEnd(try_stopiter),
                try_except,
                Instr("YIELD_VALUE", lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb[0], lineno=lineno),
                send[0],
                _yield,
                Instr("ASYNC_GEN_WRAP", lineno=lineno),
                Instr("YIELD_VALUE", lineno=lineno),
                Instr("RESUME", 1, lineno=lineno),
                Instr("PUSH_NULL", lineno=lineno),
                Instr("SWAP", 2, lineno=lineno),
                Instr("LOAD_FAST", "__ddgensend", lineno=lineno),
                Instr("SWAP", 2, lineno=lineno),
                Instr("PRECALL", 1, lineno=lineno),
                Instr("CALL", 1, lineno=lineno),
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
                Instr("LOAD_METHOD", "aclose", lineno=lineno),
                Instr("PRECALL", 0, lineno=lineno),
                Instr("CALL", 0, lineno=lineno),
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb[1],
                Instr("SEND", send[1], lineno=lineno),
                Instr("YIELD_VALUE", lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb[1], lineno=lineno),
                send[1],
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
                exc,  # except:
                Instr("POP_TOP", lineno=lineno),
                Instr("PUSH_NULL", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "athrow", lineno=lineno),
                Instr("PUSH_NULL", lineno=lineno),
                Instr("LOAD_CONST", sys.exc_info, lineno=lineno),
                Instr("PRECALL", 0, lineno=lineno),
                Instr("CALL", 0, lineno=lineno),
                Instr("CALL_FUNCTION_EX", 0, lineno=lineno),
                Instr("GET_AWAITABLE", 0, lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                send_jb[2],
                Instr("SEND", send[2], lineno=lineno),
                Instr("YIELD_VALUE", lineno=lineno),
                Instr("RESUME", 3, lineno=lineno),
                Instr("JUMP_BACKWARD_NO_INTERRUPT", send_jb[2], lineno=lineno),
                send[2],
                Instr("SWAP", 2, lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("JUMP_BACKWARD", _yield, lineno=lineno),
                bc.TryEnd(try_stopiter_2),
                stopiter,  # except StopAsyncIteration:
                Instr("PUSH_EXC_INFO", lineno=lineno),
                Instr("LOAD_CONST", StopAsyncIteration, lineno=lineno),
                Instr("CHECK_EXC_MATCH", lineno=lineno),
                Instr("POP_JUMP_FORWARD_IF_FALSE", propagate, lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
                propagate,  # finally:
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

    def _setup_block(label, lineno):
        if PY < (3, 8):
            return Instr("SETUP_EXCEPT", label, lineno=lineno)
        return Instr("SETUP_FINALLY", label, lineno=lineno)

    def wrap_async(instrs, code, lineno):
        if bc.CompilerFlags.COROUTINE & code.co_flags:
            # DEV: This is just
            # >>> return await wrapper(wrapped, args, kwargs)
            instrs[-1:-1] = [
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
            ]
        elif bc.CompilerFlags.ASYNC_GENERATOR & code.co_flags:
            stopiter = bc.Label()
            loop = bc.Label()
            genexit = bc.Label()
            exc = bc.Label()
            propagate = bc.Label()
            _yield = bc.Label()

            instrs[-1:] = [
                _setup_block(stopiter, lineno=lineno),
                Instr("DUP_TOP", lineno=lineno),
                Instr("STORE_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "asend", lineno=lineno),
                Instr("STORE_FAST", "__ddgensend", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "__anext__", lineno=lineno),
                Instr("CALL_FUNCTION", 0, lineno=lineno),
                loop,
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
                _yield,
                _setup_block(genexit, lineno=lineno),
                Instr("YIELD_VALUE", lineno=lineno),
                Instr("POP_BLOCK", lineno=lineno),
                Instr("LOAD_FAST", "__ddgensend", lineno=lineno),
                Instr("ROT_TWO", lineno=lineno),
                Instr("CALL_FUNCTION", 1, lineno=lineno),
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
                Instr("LOAD_ATTR", "aclose", lineno=lineno),
                Instr("CALL_FUNCTION", 0, lineno=lineno),
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
                exc,  # except:
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("LOAD_FAST", "__ddgen", lineno=lineno),
                Instr("LOAD_ATTR", "athrow", lineno=lineno),
                Instr("LOAD_CONST", sys.exc_info, lineno=lineno),
                Instr("CALL_FUNCTION", 0, lineno=lineno),
                Instr("CALL_FUNCTION_EX", 0, lineno=lineno),
                Instr("GET_AWAITABLE", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("YIELD_FROM", lineno=lineno),
                # DEV: We cannot use ROT_FOUR because it was removed in 3.5 and added
                # back in 3.8
                Instr("STORE_FAST", "__value", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("LOAD_FAST", "__value", lineno=lineno),
                Instr("JUMP_ABSOLUTE", _yield, lineno=lineno),
                stopiter,  # except StopAsyncIteration:
                Instr("DUP_TOP", lineno=lineno),
                Instr("LOAD_CONST", StopAsyncIteration, lineno=lineno),
                _compare_exc(propagate, lineno),
                _jump_if_false(propagate, lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_TOP", lineno=lineno),
                Instr("POP_EXCEPT", lineno=lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
                propagate,  # finally:
                _end_finally(lineno),
                Instr("LOAD_CONST", None, lineno=lineno),
                Instr("RETURN_VALUE", lineno=lineno),
            ]
