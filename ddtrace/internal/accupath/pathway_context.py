import json
import os
import time
import uuid

from ddtrace.internal import core
from ddtrace.internal.accupath.service_context import AccuPathServiceContext
from ddtrace.internal.accupath.encoding import struct
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.fnv import fnv1_64

log = get_logger("accupath")

class AccuPathPathwayContext:
    def __init__(self, tag):
        current_node = AccuPathServiceContext.from_local_env()

        self.uid = str(uuid.uuid4())
        self.tag = tag
        self.resource_name = "undefined"
        self.operation_name = "undefined"
        self.current_node_info = current_node
        self.node_hash = current_node.to_hash()
        self.root_hash = 0
        self.parent_hash = 0
        self.root_checkpoint_time = time.time_ns()
        self.current_node_path = current_node.to_hash()
        self.success = True
        self.submitted = False

        self.checkpoint_times = {
            "request_in": -1,
            "request_out": -1,
            "response_in": -1,
            "response_out": -1,
        }

    def checkpoint(self, label, checkpoint_time=None):
        if label not in self.checkpoint_times:
            log.debug("Unknown checkpoint label: %s", label)
            return
        self.checkpoint_times[label] = checkpoint_time or time.time_ns()

    def __repr__(self):
        output = json.dumps({
            "uid": self.uid,
            "service": str(os.environ.get("DD_SERVICE")),
            "root_node_hash": str(self.root_hash),
            "parent_hash": str(self.parent_hash),
            "node_hash": str(self.node_hash),
            "resource_name": self.resource_name,
            "operation_name": self.operation_name,
            "tag": self.tag,
            "root_checkpoint_time": str(self.root_checkpoint_time),
            #"checkpoint_times": self.checkpoint_times,
        })
        return output

    @classmethod
    def from_headers(cls, headers):
        import pprint
        log.debug("Headers: \n%s", pprint.pformat(headers, indent=2))
        tag = _extract_single_header_value("accupath.pathway.tag", headers)
        pathway_uid = _extract_single_header_value("accupath.pathway.uid", headers)
        root_checkpoint_time = int(_extract_single_header_value("accupath.pathway.root_checkpoint_time", headers))
        last_node_hash = int(_extract_single_header_value("accupath.pathway.last_node_hash", headers))
        root_hash = int(_extract_single_header_value("accupath.pathway.root_node_hash", headers))

        to_return = cls(tag)
        to_return.uid = pathway_uid
        to_return.root_checkpoint_time = root_checkpoint_time
        to_return.parent_hash = last_node_hash  # not used by response
        to_return.node_hash = cls._calc_checkpoint_hash(last_node_hash)
        to_return.root_hash = root_hash

        return to_return

    @classmethod
    def from_request_pathway(cls, tag):
        to_return = cls(tag)
        request = core.get_item("accupath.request.context")
        to_return.uid = request.uid
        to_return.root_node_info = request.root_node_info
        to_return.root_checkpoint_time = request.root_checkpoint_time
        to_return.checkpoints = [request.checkpoints[-1]]

        return to_return

    @classmethod
    def _calc_checkpoint_hash(cls, last_node_hash):
        current_node_hash = AccuPathServiceContext.from_local_env().to_hash()
        result = fnv1_64(struct.pack("<Q", current_node_hash) + struct.pack("<Q", last_node_hash))
        return result


def _get_current_pathway_context(checkpoint_label="accupath.request.context", headers=None):
    current_pathway_context = core.get_item(checkpoint_label)
    if headers:
        current_pathway_context = AccuPathPathwayContext.from_headers(headers)
        core.set_item(checkpoint_label, current_pathway_context)
    
    if not current_pathway_context:
        current_pathway_context = AccuPathPathwayContext("default")
        core.set_item(checkpoint_label, current_pathway_context)

    return current_pathway_context


def _extract_single_header_value(var_name, headers):
    HEADER = _generate_header(var_name)
    log.debug(f"extracting value {var_name} from header {HEADER}")

    value = None
    if isinstance(headers, dict):
        value = headers[HEADER]
    elif isinstance(headers, list):
        for (k, v) in headers:
            if k == value:
                value = v.decode('utf-8')
                break
    log.debug(f"accupath - extracted value {value} from header {HEADER} and put it into {var_name}")
    return value


def _generate_header(var_name):
    return f"x-datadog-{var_name.replace('_', '-').replace('.', '-')}"


def _inject_request_pathway_context(headers):
    #log.debug(f"request.inject context {core._CURRENT_CONTEXT.get().identifier}")
    try:
        checkpoint_label = "accupath.request.context"
        current_pathway_context = _get_current_pathway_context(checkpoint_label)
        #log.debug(f"inject starting for {current_pathway_context}")

        to_inject = [
            ("accupath.pathway.tag", current_pathway_context.tag),
            ("accupath.pathway.uid", current_pathway_context.uid),
            ("accupath.pathway.root_node_hash", current_pathway_context.root_hash or current_pathway_context.node_hash),
            ("accupath.pathway.root_checkpoint_time", current_pathway_context.root_checkpoint_time),
            ("accupath.pathway.last_node_hash", current_pathway_context.node_hash),
        ]

        for var_name, value in to_inject:
            #log.debug(f"Starting to inject header for {var_name}")
            HEADER = _generate_header(var_name)

            if value is not None:
                value = str(value)
                if isinstance(headers, list):
                    headers.append((HEADER.encode('utf-8'), value.encode('utf-8')))
                else:
                    headers[HEADER] = value

            #log.debug(f"accupath - injected value {value} into header {HEADER}")
        #log.debug(f"Full headers are: {headers}")
    except:
        log.debug("Error in inject_request_pathway_context", exc_info=True)