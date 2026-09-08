import pytest


native_profiling = pytest.importorskip(
    "ddtrace.internal.native._native", reason="requires the profiling feature of the _native extension"
)

pytestmark = pytest.mark.skipif(
    not hasattr(native_profiling, "ProfileUploader"),
    reason="ProfileUploader is only built with the profiling Cargo feature",
)


# A minimal valid gzip-compressed pprof body isn't required here: send_blocking
# only needs a byte buffer to attach to the multipart request, and the
# file:// endpoint just dumps the raw HTTP request to disk without validation.
_FAKE_PPROF_BYTES = b"not-a-real-pprof-but-thats-fine-for-a-file-dump-test"


def _make_uploader(url):
    return native_profiling.ProfileUploader(
        library_name="dd-trace-py",
        library_version="0.0.0-test",
        family="python",
        url=url,
        tags=[("env", "test")],
    )


def test_send_blocking_writes_to_file(tmp_path):
    dump_path = tmp_path / "profile_dump.http"
    uploader = _make_uploader(f"file://{dump_path}")

    status = uploader.send_blocking(
        buffer=_FAKE_PPROF_BYTES,
        start_ns=12_000_000_034,
        end_ns=56_000_000_078,
    )

    # The file:// endpoint always reports success without making a network call.
    assert status == 200
    assert dump_path.exists()
    dumped = dump_path.read_bytes()
    assert _FAKE_PPROF_BYTES in dumped


def test_send_blocking_with_metadata(tmp_path):
    dump_path = tmp_path / "profile_dump_with_metadata.http"
    uploader = _make_uploader(f"file://{dump_path}")

    status = uploader.send_blocking(
        buffer=_FAKE_PPROF_BYTES,
        start_ns=0,
        end_ns=1,
        internal_metadata_json='{"no_signals_workaround_enabled": "false"}',
        info_json='{"application": {"env": "test"}}',
    )

    assert status == 200
    assert dump_path.exists()


def test_invalid_url_raises():
    with pytest.raises(ValueError):
        _make_uploader("not a valid url \x00")


def test_invalid_json_raises(tmp_path):
    dump_path = tmp_path / "profile_dump_invalid_json.http"
    uploader = _make_uploader(f"file://{dump_path}")

    with pytest.raises(ValueError):
        uploader.send_blocking(
            buffer=_FAKE_PPROF_BYTES,
            start_ns=0,
            end_ns=1,
            internal_metadata_json="{not valid json",
        )


def test_send_blocking_with_process_tags_and_additional_files(tmp_path):
    dump_path = tmp_path / "profile_dump_extra.http"
    uploader = _make_uploader(f"file://{dump_path}")

    status = uploader.send_blocking(
        buffer=_FAKE_PPROF_BYTES,
        start_ns=0,
        end_ns=1,
        process_tags="entrypoint.name:test",
        additional_files=[("code-provenance.json", b'{"v1": []}')],
        endpoints_stats=[("GET /foo", 3), ("GET /bar", 1)],
    )

    assert status == 200
    assert dump_path.exists()
    dumped = dump_path.read_bytes()
    assert b'{"v1": []}' in dumped


def test_output_filename_writes_pprof_metadata_and_info(tmp_path):
    output_base = tmp_path / "my-profile"
    uploader = native_profiling.ProfileUploader(
        library_name="dd-trace-py",
        library_version="0.0.0-test",
        family="python",
        url="file:///unused",
        tags=[("env", "test")],
        output_filename=str(output_base),
    )

    status = uploader.send_blocking(
        buffer=_FAKE_PPROF_BYTES,
        start_ns=0,
        end_ns=1,
        internal_metadata_json='{"no_signals_workaround_enabled": "false"}',
        info_json='{"application": {"env": "test"}}',
    )

    assert status == 200
    matches = sorted(tmp_path.glob("my-profile.*.*.pprof"))
    assert len(matches) == 1
    pprof_path = matches[0]
    base = str(pprof_path)[: -len(".pprof")]

    assert pprof_path.read_bytes() == _FAKE_PPROF_BYTES

    metadata_path = tmp_path / f"{base}.internal_metadata.json"
    assert metadata_path.exists()
    assert metadata_path.read_text() == '{"no_signals_workaround_enabled": "false"}'

    info_path = tmp_path / f"{base}.info.json"
    assert info_path.exists()
    assert info_path.read_text() == '{"application": {"env": "test"}}'


def test_output_filename_omits_info_json_when_absent(tmp_path):
    output_base = tmp_path / "my-profile-no-info"
    uploader = native_profiling.ProfileUploader(
        library_name="dd-trace-py",
        library_version="0.0.0-test",
        family="python",
        url="file:///unused",
        tags=[("env", "test")],
        output_filename=str(output_base),
    )

    status = uploader.send_blocking(
        buffer=_FAKE_PPROF_BYTES,
        start_ns=0,
        end_ns=1,
    )

    assert status == 200
    matches = list(tmp_path.glob("my-profile-no-info.*.*.pprof"))
    assert len(matches) == 1
    base = str(matches[0])[: -len(".pprof")]
    assert not (tmp_path / f"{base}.info.json").exists()
