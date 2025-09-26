"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import os
import subprocess


def pt_os_system(cmd, param):
    # label pt_os_system
    os.system(cmd + param)


def pt_subprocess_popen(cmd):
    # label pt_subprocess_popen
    subp = subprocess.Popen(args=cmd)
    subp.communicate()
    subp.wait()


def pt_subprocess_popen_shell(cmd):
    # label pt_subprocess_popen_shell
    subp = subprocess.Popen(cmd, shell=True)
    subp.communicate()
    subp.wait()


def pt_subprocess_run(cmd):
    # label pt_subprocess_run
    return subprocess.run(cmd)


def pt_spawnl(mode, command, *ags):
    # label pt_spawnl
    return os.spawnl(mode, command, *ags)


def pt_spawnlp(mode, command, *ags):
    # label pt_spawnlp
    return os.spawnlp(mode, command, *ags)


def pt_spawnv(mode, command, *ags):
    # label pt_spawnv
    return os.spawnv(mode, command, *ags)


def pt_spawnvp(mode, command, *ags):
    # label pt_spawnvp
    return os.spawnvp(mode, command, *ags)
