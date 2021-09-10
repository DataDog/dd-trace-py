from typing import List
from typing import Mapping
from typing import Optional

import attr


@attr.s(frozen=True)
class IP(object):
    address = attr.ib(type=Optional[str], default=None)


@attr.s(frozen=True)
class Actor_0_1_0(object):
    ip = attr.ib(type=IP)
    context_version = attr.ib(default="0.1.0")


@attr.s(frozen=True)
class HttpRequest(object):
    method = attr.ib(type=str)
    url = attr.ib(type=str)
    remote_ip = attr.ib(type=str)
    remote_port = attr.ib(type=int)
    headers = attr.ib(type=Mapping[str, List[str]], factory=dict)
    resource = attr.ib(type=Optional[str], default=None)
    id = attr.ib(type=Optional[str], default=None)


@attr.s(frozen=True)
class HttpResponse(object):
    status = attr.ib(type=Optional[int], default=None)
    blocked = attr.ib(type=bool, default=False)
    headers = attr.ib(type=Mapping[str, List[str]], factory=dict)


@attr.s(frozen=True)
class Http_0_1_0(object):
    request = attr.ib(type=HttpRequest)
    response = attr.ib(type=Optional[HttpResponse], default=None)
    context_version = attr.ib(default="0.1.0")


@attr.s
class Context_0_1_0(object):
    actor = attr.ib(type=Optional[Actor_0_1_0], default=None)
    http = attr.ib(type=Optional[Http_0_1_0], default=None)


def get_required_context():
    # type: () -> Context_0_1_0
    return Context_0_1_0()
