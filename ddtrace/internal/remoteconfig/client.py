import base64
from datetime import datetime
import hashlib
import json
import logging
import re
import sys
from typing import Any
from typing import List
from typing import Mapping
from typing import Optional
from typing import Set
from typing import TYPE_CHECKING
import uuid

import attr
import cattr
import six

import ddtrace
from ddtrace.appsec.utils import _appsec_rc_capabilities
from ddtrace.internal import agent
from ddtrace.internal import runtime
from ddtrace.internal.utils.time import parse_isoformat


if TYPE_CHECKING:  # pragma: no cover
    from typing import Callable
    from typing import Dict
    from typing import MutableMapping
    from typing import Tuple
    from typing import Union

    ProductCallback = Callable[[Optional["ConfigMetadata"], Optional[Mapping[str, Any]]], None]


log = logging.getLogger(__name__)

TARGET_FORMAT = re.compile(r"^(datadog/\d+|employee)/([^/]+)/([^/]+)/([^/]+)$")


class RemoteConfigError(Exception):
    """
    An error occurred during the configuration update procedure.
    The error is reported to the agent.
    """


@attr.s(frozen=True)
class ConfigMetadata(object):
    """
    Configuration TUF target metadata
    """

    id = attr.ib(type=str)
    product_name = attr.ib(type=str)
    sha256_hash = attr.ib(type=Optional[str])
    tuf_version = attr.ib(type=Optional[int])


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
    custom = attr.ib(type=Mapping[str, Any])
    expires = attr.ib(type=datetime, converter=parse_isoformat)
    spec_version = attr.ib(type=str, validator=attr.validators.in_(("1.0", "1.0.0")))
    targets = attr.ib(type=Mapping[str, TargetDesc])
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


def _load_json(data):
    # type: (Union[str, bytes]) -> Dict[str, Any]
    if (3, 6) > sys.version_info > (3,) and isinstance(data, six.binary_type):
        data = str(data, encoding="utf-8")
    return json.loads(data)


def _extract_target_file(payload, target, config):
    # type: (AgentPayload, str, ConfigMetadata) -> Optional[Mapping[str, Any]]
    candidates = [item.raw for item in payload.target_files if item.path == target]
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
        return _load_json(raw)
    except Exception:
        raise RemoteConfigError("invalid JSON content for target {!r}".format(target))


def _parse_target(target, metadata):
    # type: (str, TargetDesc) -> ConfigMetadata
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


class RemoteConfigClient(object):
    """
    The Remote Configuration client regularly checks for updates on the agent
    and dispatches configurations to registered products.
    """

    def __init__(self):
        # type: () -> None
        self.id = str(uuid.uuid4())
        self.agent_url = agent_url = agent.get_trace_url()
        self._conn = agent.get_connection(agent_url, timeout=agent.get_trace_agent_timeout())
        self._headers = {"content-type": "application/json"}
        self._client_tracer = dict(
            runtime_id=runtime.get_runtime_id(),
            language="python",
            # The library uses a PEP 440-compliant versioning scheme, but the
            # RCM spec requires that we use a SemVer-compliant version. We only
            # expect that the first occurrence of "rc" in the version string to
            # break the SemVer format, so we replace it with "-rc" for
            # simplicity.
            tracer_version=ddtrace.__version__.replace("rc", "-rc", 1),
            service=ddtrace.config.service,
            env=ddtrace.config.env,
            app_version=ddtrace.config.version,
        )

        self.converter = cattr.Converter()

        # cattrs doesn't implement datetime converter in Py27, we should register
        def date_to_fromisoformat(val, cls):
            return val

        self.converter.register_structure_hook(datetime, date_to_fromisoformat)

        def base64_to_struct(val, cls):
            raw = base64.b64decode(val)
            obj = _load_json(raw)
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
        # type: (str) -> Optional[Mapping[str, Any]]
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
                capabilities=_appsec_rc_capabilities(),
            ),
            cached_target_files=[],  # TODO
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
            signed_expiration = datetime.strftime(signed.expires, "%Y-%m-%dT%H:%M:%SZ")
            raise RemoteConfigError("targets are expired, expiration date was {}".format(signed_expiration))

        targets = dict()
        for target, metadata in signed.targets.items():
            config = _parse_target(target, metadata)
            if config is not None:
                targets[target] = config

        backend_state = signed.custom.get("opaque_backend_state")
        return signed.version, backend_state, targets

    def _process_response(self, data):
        # type: (Mapping[str, Any]) -> None
        try:
            # log.debug("response payload: %r", data)
            payload = self.converter.structure_attrs_fromdict(data, AgentPayload)
        except Exception:
            log.debug("invalid agent payload received: %r", data, exc_info=True)
            raise RemoteConfigError("invalid agent payload received")

        # TODO: Also check among cached targets
        paths = {_.path for _ in payload.target_files}
        if not set(payload.client_configs) <= paths:
            raise RemoteConfigError("Not all client configurations have target files")

        # 1. Deserialize targets
        last_targets_version, backend_state, targets = self._process_targets(payload)
        if last_targets_version is None or targets is None:
            log.debug("No targets in configuration payload")
            for cb in self._products.values():
                cb(None, None)
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
            log.debug("new configuration for product %s", config.product_name)
            callback = self._products.get(config.product_name)
            if callback is None:
                continue

            applied_config = self._applied_configs.get(target)
            if applied_config == config:
                continue

            config_content = _extract_target_file(payload, target, config)
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
            payload = json.dumps(self._build_payload(state), indent=2)
            # log.debug("request payload: %r", payload)
            response = self._send_request(payload)
            if response is None:
                return
            self._process_response(response)
        except RemoteConfigError as e:
            self._last_error = str(e)
            log.warning("remote configuration client reported an error", exc_info=True)
        except Exception:
            log.warning("Unexpected error", exc_info=True)
        else:
            self._last_error = None
