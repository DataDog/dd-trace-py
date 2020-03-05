import os

from ddtrace.profile.exporter import file

from .. import test_main
from ..exporter import test_pprof


def test_export(tmp_path):
    filename = str(tmp_path / "pprof")
    exp = file.PprofFileExporter(filename)
    exp.export(test_pprof.TEST_EVENTS)
    test_main.check_pprof_file(filename + "." + str(os.getpid()) + ".1")
