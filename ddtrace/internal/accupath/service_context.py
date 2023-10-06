import json
import os
from ddtrace.internal.utils.fnv import fnv1_64


def get_bytes(s):
    return bytes(s, encoding="utf-8")


class AccuPathServiceContext:
    def __init__(self, service, env, hostname):
        self.service = service
        self.env = env
        self.hostname = hostname

    def to_string_dict(self):
        return json.dumps({
            "service": self.service,
            "env": self.env,
            "hostname": self.hostname
        })

    def to_hash(self):
        b = self.to_bytes()
        node_hash = fnv1_64(b)
        return node_hash

    def to_bytes(self):
        return get_bytes(self.service) + get_bytes(self.env) + get_bytes(self.hostname)

    @classmethod
    def from_local_env(cls):
        service = os.environ.get("DD_SERVICE", "unnamed-python-service")
        env = os.environ.get("DD_ENV", "none")
        hostname = os.environ.get("DD_HOSTNAME", "testhost")

        return cls(service, env, hostname)

    @classmethod
    def from_string_dict(cls, string_dict):
        info = json.loads(string_dict)
        return cls(info["service"], info["env"], info["hostname"])

    def __repr__(self):
        return f"AccuPathServiceContext(service={self.service}, env={self.env}, hostname={self.hostname})"