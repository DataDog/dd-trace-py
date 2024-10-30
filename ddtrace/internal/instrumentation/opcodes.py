import dis

CACHE = dis.opmap["CACHE"]
CALL = dis.opmap["CALL"]
IMPORT_FROM = dis.opmap["IMPORT_FROM"]
IMPORT_NAME = dis.opmap["IMPORT_NAME"]
LOAD_CONST = dis.opmap["LOAD_CONST"]
LOAD_FAST = dis.opmap["LOAD_FAST"]
POP_TOP = dis.opmap["POP_TOP"]
PRECALL = dis.opmap["PRECALL"]
PUSH_EXC_INFO = dis.opmap["PUSH_EXC_INFO"]
PUSH_NULL = dis.opmap["PUSH_NULL"]
RESUME = dis.opmap["RESUME"]
RETURN_VALUE = dis.opmap["RETURN_VALUE"]
STORE_FAST = dis.opmap["STORE_FAST"]

__all__ = [
    "CACHE",
    "CALL",
    "IMPORT_FROM",
    "IMPORT_NAME",
    "LOAD_CONST",
    "LOAD_FAST",
    "POP_TOP",
    "PRECALL",
    "PUSH_EXC_INFO",
    "PUSH_NULL",
    "RESUME",
    "RETURN_VALUE",
    "STORE_FAST",
]
