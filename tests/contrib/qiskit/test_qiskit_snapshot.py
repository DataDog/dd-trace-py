import subprocess
import os
import sys

from tests.utils import snapshot


def do_test(tmpdir):
    code = """
from qiskit import QuantumCircuit, transpile
from qiskit_ibm_runtime.fake_provider import FakeManilaV2

# Create circuit
circ = QuantumCircuit(2)
circ.h(0)
circ.cx(0, 1)
circ.measure_all()
backend = FakeManilaV2()
transpiled_circuit = transpile(circ, backend)
backend.run(transpiled_circuit)
    """
    pyfile = tmpdir.join("test.py")
    pyfile.write(code)
    p = subprocess.Popen(
        ["ddtrace-run", sys.executable, str(pyfile)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=sys.platform != "win32",
    )
    
    p.wait()
    assert p.returncode == 0

@snapshot()
def test_simulation(tmpdir):
    do_test(tmpdir)
