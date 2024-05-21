from qiskit import QuantumCircuit, transpile
from qiskit_ibm_runtime.fake_provider import FakeManilaV2

# Create circuit
circ = QuantumCircuit(2)
circ.h(0)
circ.cx(0, 1)
circ.measure_all()


def simul():
    backend = FakeManilaV2()
    transpiled_circuit = transpile(circ, backend)
    job = backend.run(transpiled_circuit)
    return job.result()