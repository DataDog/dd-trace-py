"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import glob
import os
import pickle
import shutil
import tarfile
from zipfile import ZipFile


def pt_open_secure(origin_string):
    root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    try:
        if os.path.commonprefix((os.path.realpath(origin_string), root_dir)) == root_dir:
            open(origin_string)
    except (FileNotFoundError, IsADirectoryError):
        pass
    return origin_string


def pt_open(origin_string):
    result = ""
    try:
        m = open(origin_string)
        result = m.read()
    except (FileNotFoundError, IsADirectoryError):
        pass
    return result


def path__io_open(origin_string):
    result = ""
    try:
        # label path__io_open
        m = open(origin_string)
        result = m.read()
    except (FileNotFoundError, IsADirectoryError):
        pass
    return result


def path_io_open(origin_string):
    # label path_io_open
    m = open(origin_string)
    return m.read()


def path_os_remove(origin_string):
    try:
        # label path_os_remove
        os.remove(origin_string)
    except Exception:
        pass


def path_os_rename(origin_string):
    try:
        # label path_os_rename
        os.rename(origin_string, "test.txt")
    except Exception:
        pass


def path_os_mkdir(origin_string):
    try:
        # label path_os_mkdir
        os.mkdir(origin_string)
    except Exception:
        pass


def path_os_rmdir(origin_string):
    try:
        # label path_os_rmdir
        os.rmdir(origin_string)
    except Exception:
        pass


def path_os_listdir(origin_string):
    try:
        # label path_os_listdir
        os.listdir(origin_string)
    except Exception:
        pass


def path_shutil_copy(origin_string):
    try:
        # label path_shutil_copy
        shutil.copy(origin_string, "not_exists.txt2")
    except Exception:
        pass


def path_shutil_copytree(origin_string):
    try:
        # label path_shutil_copytree
        shutil.copytree(origin_string, "not_exists.txt2")
    except Exception:
        pass


def path_shutil_move(origin_string):
    try:
        # label path_shutil_move
        shutil.move(origin_string, "not_exists.txt2")
    except Exception:
        pass


def path_shutil_rmtree(origin_string):
    try:
        # label path_shutil_rmtree
        shutil.rmtree(origin_string)
    except Exception:
        pass


def path_glob_glob(origin_string):
    try:
        # label path_glob_glob
        glob.glob(origin_string)
    except Exception:
        pass


def path_zipfile_ZipFile(origin_string):
    try:
        # label path_zipfile_ZipFile
        with ZipFile(origin_string, "w") as myzip:
            myzip.write("eggs.txt")
    except Exception:
        pass


def path_pickle_load(origin_string):
    try:
        # label path_pickle_load
        pickle.load(origin_string)
    except Exception:
        pass


def path_tarfile_open(origin_string):
    try:
        # label path_tarfile_open
        tarfile.open(origin_string)
    except Exception:
        pass
