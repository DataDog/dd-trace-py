# This test succeeds as long as the test application can print the string "OK" to the
import sys


in_text = sys.stdin.read()
print(in_text)
assert "OK" == in_text
