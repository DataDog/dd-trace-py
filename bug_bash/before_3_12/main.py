import test

from ddtrace import config


config.env = "dev"
config.service = "error-bug-bash"
config.version = "0.1"

test.f()
