import re
import hashlib
import base64
import logging
import uuid
import json

from datetime import datetime

from typing import TYPE_CHECKING, Any, List, Mapping, Set

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal import runtime
from ddtrace.internal.utils.time import parse_isoformat

import attr
import cattr

log = logging.getLogger(__name__)

TARGET_FORMAT = re.compile(r"^(datadog/\d+|employee)/([^/]+)/([^/]+)/([^/]+)$")


class RemoteConfigError(Exception):
    """
    An error occured during the configuration update procedure.
    The error is reported to the agent.
    """


@attr.s(frozen=True)
class ConfigMetadata(object):
    """
    Configuration TUF target metadata
    """

    id = attr.ib(type=str)
    product_name = attr.ib(type=str)
    sha256_hash = attr.ib(type=str)
    tuf_version = attr.ib(type=int)


@attr.s
class Signature(object):
    keyid = attr.ib(type=str)
    sig = attr.ib(type=str)


@attr.s
class Key(object):
    keytype = attr.ib(type=str)
    keyid_hash_algorithms = attr.ib(type=List[str])
    keyval = attr.ib(type=Mapping)
    scheme = attr.ib(type=str)


@attr.s
class Role(object):
    keyids = attr.ib(type=List[str])
    threshold = attr.ib(type=int)


@attr.s
class Root(object):
    _type = attr.ib(type=str, validator=attr.validators.in_(("root",)))
    spec_version = attr.ib(type=str)
    consistent_snapshot = attr.ib(type=bool)
    expires = attr.ib(type=datetime, converter=parse_isoformat)
    keys = attr.ib(type=Mapping[str, Key])
    roles = attr.ib(type=Mapping[str, Role])
    version = attr.ib(type=int)


@attr.s
class SignedRoot(object):
    signatures = attr.ib(type=List[Signature])
    signed = attr.ib(type=Root)


@attr.s
class TargetDesc(object):
    length = attr.ib(type=int)
    hashes = attr.ib(type=Mapping[str, str])
    custom = attr.ib(type=Mapping[str, Any])


@attr.s
class Targets(object):
    _type = attr.ib(type=str, validator=attr.validators.in_(("targets",)))
    targets = attr.ib(type=Mapping[str, TargetDesc])
    expires = attr.ib(type=datetime, converter=parse_isoformat)
    custom = attr.ib(type=Mapping[str, Any])
    version = attr.ib(type=int)


@attr.s
class SignedTargets(object):
    signatures = attr.ib(type=List[Signature])
    signed = attr.ib(type=Targets)


@attr.s
class TargetFile(object):
    path = attr.ib(type=str)
    raw = attr.ib(type=str)


@attr.s
class AgentPayload(object):

    roots = attr.ib(type=List[SignedRoot], default=None)
    targets = attr.ib(type=SignedTargets, default=None)
    target_files = attr.ib(type=List[TargetFile], default=[])
    client_configs = attr.ib(type=Set[str], default={})


if TYPE_CHECKING:
    from typing import Callable, Optional, MutableMapping, Tuple, Sequence

    ProductCallback = Callable[[ConfigMetadata, Optional[Mapping[str, Any]]], None]


class Client(object):
    """
    The Remote Configuration client regularly checks for updates on the agent
    and dispatches configurations to registered products.
    """

    def __init__(
        self, runtime_id=runtime.get_runtime_id(), agent_url=agent.get_trace_url(),
    ):
        # type: (str, str, float) -> None
        self.agent_url = agent_url
        self.runtime_id = runtime_id
        self.id = str(uuid.uuid4())
        self._conn = agent.get_connection(agent_url, timeout=agent.get_trace_agent_timeout())
        self._headers = {"content-type": "application/json"}
        self._client_tracer = dict(
            runtime_id=runtime.get_runtime_id(),
            language="python",
            tracer_version=ddtrace.__version__,
            service=ddtrace.config.service,
            env=ddtrace.config.env,
            version=ddtrace.config.version,
        )

        self.converter = cattr.Converter()

        def base64_to_struct(val, cls):
            raw = base64.b64decode(val)
            obj = json.loads(raw)
            return self.converter.structure_attrs_fromdict(obj, cls)

        self.converter.register_structure_hook(SignedRoot, base64_to_struct)
        self.converter.register_structure_hook(SignedTargets, base64_to_struct)

        self._products = dict()  # type: MutableMapping[str, ProductCallback]
        self._applied_configs = dict()  # type: Mapping[str, ConfigMetadata]
        self._last_targets_version = 0
        self._last_error = None  # type: Optional[str]
        self._backend_state = None  # type: Optional[str]

    def register_product(self, product_name, func=None):
        # type: (str, Optional[ProductCallback]) -> None
        if func is not None:
            self._products[product_name] = func
        else:
            self._products.pop(product_name, None)

    def _send_request(self, payload):
        # type: (Mapping[str, Any]) -> None
        try:
            self._conn.request("POST", "v0.7/config", payload, self._headers)
            resp = self._conn.getresponse()
            data = resp.read()
        finally:
            self._conn.close()

        if resp.status == 404:
            # Remote configuration is not enabled or unsupported by the agent
            return None

        if resp.status < 200 or resp.status >= 300:
            log.warning("Unexpected error: HTTP error status %s, reason %s", resp.status, resp.reason)
            return None

        return json.loads(data)

    def _build_payload(self, state):
        # type: (Mapping[str, Any]) -> Mapping[str, Any]
        return dict(
            client=dict(
                id=self.id,
                products=list(self._products.keys()),
                is_tracer=True,
                client_tracer=self._client_tracer,
                state=state,
            )
        )

    def _build_state(self):
        # type: () -> Mapping[str, Any]
        has_error = self._last_error is not None
        state = dict(
            root_version=1,
            targets_version=self._last_targets_version,
            config_states=[
                dict(id=config.id, version=config.tuf_version, product=config.product_name)
                for config in self._applied_configs.values()
            ],
            has_error=has_error,
        )
        if self._backend_state is not None:
            state["backend_client_state"] = self._backend_state
        if has_error:
            state["error"] = self._last_error
        return state

    def _process_targets(self, payload):
        # type: (AgentPayload) -> Tuple[Optional[int], Optional[str], Optional[Mapping[str, ConfigMetadata]]]
        if payload.targets is None:
            # no targets received
            return None, None, None

        signed = payload.targets.signed
        if signed.expires <= datetime.utcnow():
            raise RemoteConfigError("targets are expired, expiration date was {}".format(signed_expiration))

        targets = dict()
        for target, metadata in signed.targets.items():
            config = self._parse_target(target, metadata)
            if config is not None:
                targets[target] = config

        backend_state = signed.custom.get("opaque_backend_state")
        return signed.version, backend_state, targets

    def _parse_target(self, target, metadata):
        # type: (str, Mapping[str, Any]) -> ConfigMetadata
        m = TARGET_FORMAT.match(target)
        if m is None:
            raise RemoteConfigError("unexpected target format {!r}".format(target))
        _, product_name, config_id, _ = m.groups()
        return ConfigMetadata(
            id=config_id,
            product_name=product_name,
            sha256_hash=metadata.hashes.get("sha256"),
            tuf_version=metadata.custom.get("v"),
        )

    def _extract_target_file(self, target_files, target, config):
        # type: (Sequence[TargetFile], str, ConfigMetadata) -> Optional[Mapping[str, Any]]
        candidates = [item.raw for item in target_files if item.path == target]
        if len(candidates) != 1 or candidates[0] is None:
            log.debug("invalid target_files for %r", target)
            return None

        try:
            raw = base64.b64decode(candidates[0])
        except Exception:
            raise RemoteConfigError("invalid base64 target_files for {!r}".format(target))

        computed_hash = hashlib.sha256(raw).hexdigest()
        if computed_hash != config.sha256_hash:
            raise RemoteConfigError(
                "mismatch between target {!r} hashes {!r} != {!r}".format(target, computed_hash, config.sha256_hash)
            )

        try:
            return json.loads(raw)
        except Exception:
            raise RemoteConfigError("invalid JSON content for target {!r}".format(target))

    def _process_response(self, data):
        # type: (Mapping[str, Any]) -> None
        try:
            payload = self.converter.structure_attrs_fromdict(data, AgentPayload)
        except Exception:
            log.debug("invalid agent payload received", exc_info=True)
            raise RemoteConfigError("invalid agent payload received")

        # 1. Deserialize targets
        last_targets_version, backend_state, targets = self._process_targets(payload)
        if last_targets_version is None or targets is None:
            return

        client_configs = {k: v for k, v in targets.items() if k in payload.client_configs}

        # 2. Remove previously applied configurations
        applied_configs = dict()
        for target, config in self._applied_configs.items():

            if target in client_configs and targets.get(target) == config:
                # The configuration has not changed.
                applied_configs[target] = config
                continue

            callback = self._products.get(config.product_name)
            if callback is None:
                continue

            try:
                callback(config, None)
            except Exception:
                log.debug("error while removing product %s config %r", config.product_name, config)
                continue

        # 3. Load new configurations
        for target, config in client_configs.items():
            callback = self._products.get(config.product_name)
            if callback is None:
                continue

            applied_config = self._applied_configs.get(target)
            if applied_config == config:
                continue

            config_content = self._extract_target_file(payload.target_files, target, config)
            if config_content is None:
                continue

            try:
                callback(config, config_content)
            except Exception:
                log.debug("error while loading product %s config %r", config.product_name, config)
                continue
            else:
                applied_configs[target] = config

        self._last_targets_version = last_targets_version
        self._applied_configs = applied_configs
        self._backend_state = backend_state

    def request(self):
        # type: () -> None
        try:
            state = self._build_state()
            payload = json.dumps(self._build_payload(state))
            self._process_response(self._send_request(payload))
        except RemoteConfigError as e:
            self._last_error = str(e)
            log.warning("remote configuration client reported an error", exc_info=True)
        except Exception:
            log.warning("Unexpected error", exc_info=True)
        else:
            self._last_error = None
