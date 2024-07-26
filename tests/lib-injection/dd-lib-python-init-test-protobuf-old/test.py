import socket

import addressbook_pb2


# First, open a socket on port 18080 to allow the fixture to continue
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.bind(("localhost", 18080))

# Accept a connection from the fixture
s.listen(1)
conn, addr = s.accept()

# Drain the request from the fixture
data = conn.recv(4096)

# Do some stuff with the protobuf
person = addressbook_pb2.Person()
person.id = 1234
person.name = "John Doe"
person.email = "john@ibm.com"

print(person)

# If we're here, then everything was successful.
# This test takes advantage of an existing fixture for these kinds of injection tests, so
# we just write the string "OK" to a magic file named `mock-telemetry.out` to signal success.
with open("mock-telemetry.out", "w") as f:
    f.write("OK")

# Send a standard response back to the fixture and teardown
conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
s.close()
