"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import os


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def propagation_no_path(origin_string):
    from Crypto.Cipher import AES

    key = b"Sixteen byte key"
    data = b"abcdefgh"
    crypt_obj = AES.new(key, AES.MODE_EAX)
    # label propagation_no_path
    result = crypt_obj.encrypt(data)
    return result


def propagation_path_1_source_1_prop(origin_string):
    if type(origin_string) is str:
        string1 = str(origin_string)  # 1 Range
    else:
        string1 = str(origin_string, encoding="utf-8")  # 1 Range
    result = ""
    try:
        # label propagation_path_1_source_1_prop
        m = open(ROOT_DIR + "/" + string1 + ".txt")
        result = m.read()
    except Exception:
        pass
    return result


def propagation_path_1_source_2_prop(origin_string):
    if type(origin_string) is str:
        string1 = str(origin_string)  # 1 Range
    else:
        string1 = str(origin_string, encoding="utf-8")  # 1 Range
    string2 = string1 + string1  # 2 Ranges
    result = ""
    try:
        # label propagation_path_1_source_2_prop
        m = open(ROOT_DIR + "/" + string2 + ".txt")
        result = m.read()
    except Exception:
        pass
    return result


def propagation_path_2_source_2_prop(origin_string1, tainted_string_2):
    if type(origin_string1) is str:
        string1 = str(origin_string1)  # 1 Range
    else:
        string1 = str(origin_string1, encoding="utf-8")  # 1 Range
    if type(tainted_string_2) is str:
        string2 = str(tainted_string_2)  # 1 Range
    else:
        string2 = str(tainted_string_2, encoding="utf-8")  # 1 Range
    string3 = string1 + string2  # 2 Ranges
    result = ""
    try:
        # label propagation_path_2_source_2_prop
        m = open(ROOT_DIR + "/" + string3 + ".txt")
        result = m.read()
    except Exception:
        pass
    return result


def propagation_path_3_prop(origin_string1, tainted_string_2):
    if type(origin_string1) is str:
        string1 = str(origin_string1)  # 1 Range
    else:
        string1 = str(origin_string1, encoding="utf-8")  # 1 Range
    if type(tainted_string_2) is str:
        string2 = str(tainted_string_2)  # 1 Range
    else:
        string2 = str(tainted_string_2, encoding="utf-8")  # 1 Range
    string3 = string1 + string2  # 2 Ranges
    string4 = "-".join([string3, string3, string3])  # 6 Ranges
    result = ""
    try:
        # label propagation_path_3_prop
        m = open(ROOT_DIR + "/" + string4 + ".txt")
        result = m.read()
    except Exception:
        pass
    return result


def propagation_path_5_prop(origin_string1, tainted_string_2):
    if type(origin_string1) is str:
        string1 = str(origin_string1)  # 1 Range
    else:
        string1 = str(origin_string1, encoding="utf-8")  # 1 Range
    if type(tainted_string_2) is str:
        string2 = str(tainted_string_2)  # 1 Range
    else:
        string2 = str(tainted_string_2, encoding="utf-8")  # 1 Range
    string3 = string1 + string2  # 2 Ranges
    string4 = "-".join([string3, string3, string3])  # 6 Ranges
    string5 = string4[1:5]  # 2 Ranges
    result = ""
    try:
        # label propagation_path_5_prop
        m = open(ROOT_DIR + "/" + string5 + ".txt")
        result = m.read()
    except Exception:
        pass
    return result


def propagation_memory_check(origin_string1, tainted_string_2):
    if type(origin_string1) is str:
        string1 = str(origin_string1)  # 1 Range
    else:
        string1 = str(origin_string1, encoding="utf-8")  # 1 Range
    if type(tainted_string_2) is str:
        string2 = str(tainted_string_2)  # 1 Range
    else:
        string2 = str(tainted_string_2, encoding="utf-8")  # 1 Range
    string3 = string1 + string2  # 2 Ranges
    string4 = "-".join([string3, string3, string3])  # 6 Ranges
    string5 = string4[0 : (len(string4) - 1)]
    string6 = string5.title()
    string7 = string6.upper()
    string8 = "%s_notainted" % string7
    string9 = "notainted_{}".format(string8)
    try:
        # label propagation_memory_check
        m = open(ROOT_DIR + "/" + string9 + ".txt")
        _ = m.read()
    except Exception:
        pass
    return string9
