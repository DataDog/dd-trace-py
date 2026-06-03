import os.path


def do_os_path_join(a, b):
    return os.path.join(a, b)


def do_os_path_normcase(a):
    return os.path.normcase(a)


def do_os_path_basename(a):
    return os.path.basename(a)


def do_os_path_dirname(a):
    return os.path.dirname(a)


def do_os_path_splitdrive(a):
    return os.path.splitdrive(a)


def do_os_path_splitroot(a):
    return os.path.splitroot(a)


def do_os_path_split(a):
    return os.path.split(a)


def do_os_path_splitext(a):
    return os.path.splitext(a)
