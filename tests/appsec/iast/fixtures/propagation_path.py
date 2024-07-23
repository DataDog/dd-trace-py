"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import os
import sys


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
    import os.path

    if type(origin_string1) is str:
        string1 = str(origin_string1)  # 1 Range
    else:
        string1 = str(origin_string1, encoding="utf-8")  # 1 Range
    # string1 = taintsource
    if type(tainted_string_2) is str:
        string2 = str(tainted_string_2)  # 1 Range
    else:
        string2 = str(tainted_string_2, encoding="utf-8")  # 1 Range
    # string2 = taintsource2
    string3 = string1 + string2  # 2 Ranges
    # taintsource1taintsource2
    string4 = "-".join([string3, string3, string3])  # 6 Ranges
    # taintsource1taintsource2-taintsource1taintsource2-taintsource1taintsource2
    string5 = string4[0 : (len(string4) - 1)]
    # taintsource1taintsource2-taintsource1taintsource2-taintsource1taintsource
    string6 = string5.title()
    # Taintsource1Taintsource2-Taintsource1Taintsource2-Taintsource1Taintsource
    string7 = string6.upper()
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE
    string8 = "%s_notainted" % string7
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string9 = "notainted#{}".format(string8)
    # notainted#TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string10 = string9.split("#")[1]
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string11 = "notainted#{}".format(string10)
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string12 = string11.rsplit("#")[1]
    string13_pre = string12 + "\n"
    string13 = string13_pre + "notainted"
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted\nnotainted
    string14 = string13.splitlines()[0]  # string14 = string12
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string15 = os.path.join("foo", "bar", string14)
    # /foo/bar/TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string16 = os.path.split(string15)[1]
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string17 = string16 + ".jpg"
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted.jpg
    string18 = os.path.splitext(string17)[0]
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string19_pre = os.sep + string18
    string19 = os.path.join(string19_pre, "nottainted_notdir")
    # /TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted/nottainted_notdir
    string20 = os.path.dirname(string19)
    # /TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    string21 = os.path.basename(string20)
    # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted

    if sys.version_info >= (3, 12):
        string22 = os.sep + string21
        # /TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
        string23 = os.path.splitroot(string22)[2]
        # TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE2-TAINTSOURCE1TAINTSOURCE_notainted
    else:
        string23 = string21

    try:
        # label propagation_memory_check
        m = open(ROOT_DIR + "/" + string23 + ".txt")
        _ = m.read()
    except Exception:
        pass
    return string23
