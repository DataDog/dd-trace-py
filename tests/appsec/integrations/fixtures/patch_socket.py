import types


code = """
import socket

def do_socketpair_roundtrip() -> str:
    s1, s2 = socket.socketpair()
    try:
        msg = b"ping"
        s1.sendall(msg)
        data = s2.recv(16)
        ok = data == msg
        return f"OK:{ok}"
    finally:
        s1.close()
        s2.close()
"""


def socketpair_roundtrip():
    """Create a module at runtime and exercise socket open/send/recv/close.

    This simulates late imports and runtime execution paths post-IAST patching.
    """
    module_name = "test_" + "socketpair"
    compiled_code = compile(code, "tests/appsec/integrations/packages_tests/", mode="exec")
    module_changed = types.ModuleType(module_name)
    exec(compiled_code, module_changed.__dict__)
    result = eval("do_socketpair_roundtrip()", module_changed.__dict__)
    return result
