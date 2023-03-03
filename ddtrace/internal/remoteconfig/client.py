import abc
import base64
from datetime import datetime
import hashlib
import json
import re
import sys
from typing import Any
from typing import Dict
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
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.runtime import container
from ddtrace.internal.utils.time import parse_isoformat

from ..utils.version import _pep440_to_semver


if TYPE_CHECKING:  # pragma: no cover
    from typing import MutableMapping
    from typing import Tuple
    from typing import Union


log = get_logger(__name__)

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
    length = attr.ib(type=Optional[int])
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


AppliedConfigType = Dict[str, ConfigMetadata]
TargetsType = Dict[str, ConfigMetadata]


class RemoteConfigCallBack(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def __call__(self, metadata, config):
        # type: (Optional[ConfigMetadata], Any) -> None
        pass


class RemoteConfigCallBackAfterMerge(RemoteConfigCallBack):
    configs = {}  # type: Dict[str, Any]

    def append(self, config):
        self.configs.update(config)

    def dispatch(self):
        try:
            self.__call__(None, self.configs)
        finally:
            self.configs = {}


class RemoteConfigClient(object):
    """
    The Remote Configuration client regularly checks for updates on the agent
    and dispatches configurations to registered products.
    """

    def __init__(self):
        # type: () -> None
        tracer_version = _pep440_to_semver()

        self.id = str(uuid.uuid4())
        self.agent_url = agent_url = agent.get_trace_url()
        self._conn = agent.get_connection(agent_url, timeout=agent.get_trace_agent_timeout())
        self._headers = {"content-type": "application/json"}

        container_info = container.get_container_info()
        if container_info is not None:
            container_id = container_info.container_id
            if container_id is not None:
                self._headers["Datadog-Container-Id"] = container_id

        tags = ddtrace.config.tags.copy()
        if ddtrace.config.env:
            tags["env"] = ddtrace.config.env
        if ddtrace.config.version:
            tags["version"] = ddtrace.config.version
        tags["tracer_version"] = tracer_version
        tags["host_name"] = get_hostname()

        self._client_tracer = dict(
            runtime_id=runtime.get_runtime_id(),
            language="python",
            tracer_version=tracer_version,
            service=ddtrace.config.service,
            env=ddtrace.config.env,
            app_version=ddtrace.config.version,
            tags=[":".join(_) for _ in tags.items()],
        )
        self.cached_target_files = []  # type: List[AppliedConfigType]
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

        self._products = dict()  # type: MutableMapping[str, RemoteConfigCallBack]
        self._applied_configs = dict()  # type: AppliedConfigType
        self._last_targets_version = 0
        self._last_error = None  # type: Optional[str]
        self._backend_state = None  # type: Optional[str]

    def register_product(self, product_name, func=None):
        # type: (str, Optional[RemoteConfigCallBack]) -> None
        if func is not None:
            self._products[product_name] = func
        else:
            self._products.pop(product_name, None)

    def unregister_product(self, product_name):
        # type: (str) -> None
        self._products.pop(product_name, None)

    def _send_request(self, payload):
        # type: (str) -> Optional[Mapping[str, Any]]
        try:
            self._conn.request("POST", REMOTE_CONFIG_AGENT_ENDPOINT, payload, self._headers)
            resp = self._conn.getresponse()
            data = resp.read()
        except OSError as e:
            log.warning("Unexpected connection error in remote config client request: %s", str(e))  # noqa: G200
            return None
        finally:
            self._conn.close()

        if resp.status == 404:
            # Remote configuration is not enabled or unsupported by the agent
            return None

        if resp.status < 200 or resp.status >= 300:
            log.warning("Unexpected error: HTTP error status %s, reason %s", resp.status, resp.reason)
            return None

        return json.loads(data)

    @staticmethod
    def _extract_target_file(payload, target, config):
        # type: (AgentPayload, str, ConfigMetadata) -> Optional[Dict[str, Any]]
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

    @staticmethod
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
            length=metadata.length,
            tuf_version=metadata.custom.get("v"),
        )

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
            cached_target_files=self.cached_target_files,
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
        # type: (AgentPayload) -> Tuple[Optional[int], Optional[str], Optional[TargetsType]]
        if payload.targets is None:
            # no targets received
            return None, None, None

        signed = payload.targets.signed
        targets = dict()  # type: TargetsType

        for target, metadata in signed.targets.items():
            config = self._parse_target(target, metadata)
            if config is not None:
                targets[target] = config

        backend_state = signed.custom.get("opaque_backend_state")
        return signed.version, backend_state, targets

    def _remove_previously_applied_configurations(self, applied_configs, client_configs, targets):
        # type: (AppliedConfigType, TargetsType, TargetsType) -> None
        for target, config in self._applied_configs.items():
            callback_action = None
            if target in client_configs and targets.get(target) == config:
                # The configuration has not changed.
                applied_configs[target] = config
                continue
            elif target not in client_configs:
                log.debug("Disable configuration: %s", target)
                callback_action = False

            callback = self._products.get(config.product_name)
            if callback:
                try:
                    callback(config, callback_action)
                except Exception:
                    log.debug("error while removing product %s config %r", config.product_name, config)
                    continue

    def _load_new_configurations(self, applied_configs, client_configs, payload):
        # type: (AppliedConfigType, TargetsType, AgentPayload) -> None
        list_callbacks = []
        for target, config in client_configs.items():
            callback = self._products.get(config.product_name)
            if callback:
                applied_config = self._applied_configs.get(target)
                if applied_config == config:
                    continue

                config_content = self._extract_target_file(payload, target, config)
                if config_content is None:
                    continue

                try:
                    log.debug("Load new configuration: %s. content ", target)
                    if isinstance(callback, RemoteConfigCallBackAfterMerge):
                        callback.append(config_content)
                        if callback not in list_callbacks:
                            list_callbacks.append(callback)
                    else:
                        callback(config, config_content)
                except Exception:
                    log.debug(
                        "Failed to load configuration %s for product %r", config, config.product_name, exc_info=True
                    )
                    continue
                else:
                    applied_configs[target] = config
        for callback in list_callbacks:
            callback.dispatch()

    def _add_apply_config_to_cache(self):
        if self._applied_configs:
            cached_data = []
            for target, config in self._applied_configs.items():
                cached_data.append(
                    {
                        "path": target,
                        "length": config.length,
                        "hashes": [{"algorithm": "sha256", "hash": config.sha256_hash}],
                    }
                )
            self.cached_target_files = cached_data
        else:
            self.cached_target_files = []

    def _validate_config_exists_in_target_paths(self, payload_client_configs, payload_target_files):
        # type: (Set[str], List[TargetFile]) -> None
        paths = {_.path for _ in payload_target_files}
        paths = paths.union({_["path"] for _ in self.cached_target_files})

        # !(payload.client_configs is a subset of paths or payload.client_configs is equal to paths)
        if not set(payload_client_configs) <= paths:
            raise RemoteConfigError("Not all client configurations have target files")

    @staticmethod
    def _validate_signed_target_files(payload_target_files, payload_targets_signed, client_configs):
        # type: (List[TargetFile], Targets, TargetsType) -> None
        for target in payload_target_files:
            if (payload_targets_signed.targets and not payload_targets_signed.targets.get(target.path)) and (
                client_configs and not client_configs.get(target.path)
            ):
                raise RemoteConfigError(
                    "target file %s not exists in client_config and signed targets" % (target.path,)
                )

    def _process_response(self, data):
        # type: (Mapping[str, Any]) -> None
        try:
            # log.debug("response payload: %r", data)
            payload = self.converter.structure_attrs_fromdict(data, AgentPayload)
        except Exception:
            log.debug("invalid agent payload received: %r", data, exc_info=True)
            raise RemoteConfigError("invalid agent payload received")

        self._validate_config_exists_in_target_paths(payload.client_configs, payload.target_files)

        # 1. Deserialize targets
        last_targets_version, backend_state, targets = self._process_targets(payload)
        if last_targets_version is None or targets is None:
            log.debug("No targets in configuration payload")
            for callback in self._products.values():
                callback(None, None)
            return

        client_configs = {k: v for k, v in targets.items() if k in payload.client_configs}
        log.debug("Retrieved client configs last version %s: %s", last_targets_version, client_configs)

        self._validate_signed_target_files(payload.target_files, payload.targets.signed, client_configs)

        # 2. Remove previously applied configurations
        applied_configs = dict()  # type: AppliedConfigType
        self._remove_previously_applied_configurations(applied_configs, client_configs, targets)

        # 3. Load new configurations
        self._load_new_configurations(applied_configs, client_configs, payload)

        self._last_targets_version = last_targets_version
        self._applied_configs = applied_configs
        self._backend_state = backend_state

        self._add_apply_config_to_cache()

    def request(self):
        # type: () -> bool
        try:
            state = self._build_state()
            payload = json.dumps(self._build_payload(state))
            response = self._send_request(payload)
            if response is None:
                return False
            self._process_response(response)
            self._last_error = None
            return True

        except RemoteConfigError as e:
            self._last_error = str(e)
            log.debug("remote configuration client reported an error", exc_info=True)
        except ValueError as e:
            log.debug("Unexpected response data: %s", e)  # noqa: G200
        except Exception as e:
            log.debug("Unexpected error: %s", e)  # noqa: G200

        return False
