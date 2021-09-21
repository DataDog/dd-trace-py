import os

from ddtrace.profiling.exporter import file

from .. import utils
from ..exporter import test_pprof


def test_export(tmp_path):
    filename = str(tmp_path / "pprof")
    exp = file.PprofFileExporter(filename)
    exp.export(test_pprof.TEST_EVENTS, 0, 1)
    utils.check_pprof_file(filename + "." + str(os.getpid()) + ".1")
