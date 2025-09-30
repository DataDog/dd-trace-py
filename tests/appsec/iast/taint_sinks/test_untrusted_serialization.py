import io

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from ddtrace.appsec._iast.constants import VULN_UNTRUSTED_SERIALIZATION
from ddtrace.appsec._iast.taint_sinks import untrusted_serialization as untrusted_mod
from tests.appsec.iast.iast_utils import _get_iast_data


@pytest.fixture(autouse=True)
def _ensure_patch():
    """Ensure the untrusted serialization sinks are patched before each test."""
    untrusted_mod.patch()
    yield


def _assert_one_untrusted_vuln():
    data = _get_iast_data()
    vulnerabilities = data.get("vulnerabilities", [])
    assert len(vulnerabilities) == 1
    vulnerability = vulnerabilities[0]
    print(vulnerability)
    assert vulnerability["type"] == VULN_UNTRUSTED_SERIALIZATION


def _assert_no_untrusted_vuln():
    data = _get_iast_data()
    assert len(data.get("vulnerabilities", [])) == 0


def test_untrusted_serialization_yaml_unsafe_load(iast_context_defaults):
    yaml = pytest.importorskip("yaml")

    payload = "key: value"
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    yaml.unsafe_load(tainted)

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_yaml_load(iast_context_defaults):
    yaml = pytest.importorskip("yaml")

    payload = "key: value"
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    # yaml.load may require a Loader; calling unsafe_load in previous test already ensures coverage.
    # Here we still call load directly to exercise the wrapper.

    yaml.load(tainted, Loader=getattr(yaml, "UnsafeLoader", None))

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_pickle_loads(iast_context_defaults):
    import pickle

    # Example object
    data = [1, 2, 3, {"x": 10, "y": 20}]

    # Serialize to bytes
    payload = pickle.dumps(data)

    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    pickle.loads(tainted)

    _assert_one_untrusted_vuln()


def test_untrusted_serialization__pickle_loads(iast_context_defaults):
    _pickle = pytest.importorskip("_pickle")

    payload = b"\x80\x04N."
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    _pickle.loads(tainted)

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_dill_loads(iast_context_defaults):
    dill = pytest.importorskip("dill")

    payload = b"\x80\x04N."
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    dill.loads(tainted)

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_pickle_load(iast_context_defaults):
    import pickle

    data = {"a": 1}
    payload = pickle.dumps(data)
    bio = io.BytesIO(payload)
    tainted_bio = bio  # stream is not tainted; taint the payload argument for Unpickler path

    # pickle.load reads from file-like; to ensure sink is hit, create Unpickler with tainted buffer
    # but pickle.load should be wrapped itself; call directly
    pickle.load(tainted_bio)

    _assert_no_untrusted_vuln()


def test_untrusted_serialization_pickle_unpickler_load(iast_context_defaults):
    import pickle

    data = ["x", 2]
    payload = pickle.dumps(data)
    bio = io.BytesIO(payload)
    unpickler = pickle.Unpickler(bio)

    # The method load() is wrapped via "pickle\n_Unpickler.load" mapping in the module
    unpickler.load()

    _assert_no_untrusted_vuln()


def test_untrusted_serialization__pickle_load(iast_context_defaults):
    _pickle = pytest.importorskip("_pickle")
    import pickle

    payload = pickle.dumps({"k": "v"})
    bio = io.BytesIO(payload)

    _pickle.load(bio)

    _assert_no_untrusted_vuln()


def test_untrusted_serialization__pickle_unpickler_load(iast_context_defaults):
    _pickle = pytest.importorskip("_pickle")
    import pickle

    payload = pickle.dumps(123)
    bio = io.BytesIO(payload)
    unpickler = _pickle.Unpickler(bio)

    unpickler.load()

    _assert_no_untrusted_vuln()


def test_untrusted_serialization_dill_load(iast_context_defaults):
    dill = pytest.importorskip("dill")
    import pickle

    payload = pickle.dumps({"z": 9})
    bio = io.BytesIO(payload)

    dill.load(bio)

    _assert_no_untrusted_vuln()


def test_untrusted_serialization_yaml_load_all(iast_context_defaults):
    yaml = pytest.importorskip("yaml")

    payload = "---\na: 1\n---\nb: 2\n"
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    list(yaml.load_all(tainted, Loader=getattr(yaml, "UnsafeLoader", None)))

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_yaml_unsafe_load_all(iast_context_defaults):
    yaml = pytest.importorskip("yaml")

    payload = "---\na: 1\n---\nb: 2\n"
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    list(yaml.unsafe_load_all(tainted))

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_yaml_full_load(iast_context_defaults):
    yaml = pytest.importorskip("yaml")

    payload = "a: 1"
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    yaml.full_load(tainted)

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_yaml_full_load_all(iast_context_defaults):
    yaml = pytest.importorskip("yaml")

    payload = "---\na: 1\n---\nb: 2\n"
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    list(yaml.full_load_all(tainted))

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_pickle__loads_internal(iast_context_defaults):
    import pickle

    if not hasattr(pickle, "_loads"):
        pytest.skip("pickle._loads not available")

    data = {"int": 1}
    payload = pickle.dumps(data)
    tainted = taint_pyobject(payload, source_name="path", source_value=payload, source_origin=OriginType.PATH)

    pickle._loads(tainted)

    _assert_one_untrusted_vuln()


def test_untrusted_serialization_pickle__load_internal(iast_context_defaults):
    import io as _io
    import pickle

    if not hasattr(pickle, "_load"):
        pytest.skip("pickle._load not available")

    payload = pickle.dumps([1, 2, 3])
    bio = _io.BytesIO(payload)

    pickle._load(bio)

    _assert_no_untrusted_vuln()
