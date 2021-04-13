import gzip

from ddtrace.profiling.exporter import pprof_pb2


def check_pprof_file(
    filename,  # type: str
):
    # type: (...) -> None
    with gzip.open(filename, "rb") as f:
        content = f.read()
    p = pprof_pb2.Profile()
    p.ParseFromString(content)
    assert len(p.sample_type) == 11
    assert p.string_table[p.sample_type[0].type] == "cpu-samples"
    assert len(p.sample) >= 1
