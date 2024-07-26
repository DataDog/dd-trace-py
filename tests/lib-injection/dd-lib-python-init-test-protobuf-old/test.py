import addressbook_pb2


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
