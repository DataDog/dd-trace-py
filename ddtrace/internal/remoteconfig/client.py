import base64
import dataclasses
import enum
import hashlib
import json
import os
import re
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any
from typing import Iterable
from typing import Mapping
from typing import Optional
from typing import Sequence
import uuid

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal import gitmetadata
from ddtrace.internal import process_tags
from ddtrace.internal import runtime
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import is_distribution_available
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import PayloadType
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.constants import REMOTE_CONFIG_AGENT_ENDPOINT
from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.utils.formats import parse_tags_str
from ddtrace.internal.utils.version import _pep440_to_semver


log = get_logger(__name__)

TARGET_FORMAT = re.compile(r"^(datadog/\d+|employee)/([^/]+)/([^/]+)/([^/]+)$")


REQUIRE_SKIP_SHUTDOWN = frozenset({"django-q"})


def derive_skip_shutdown(c: "RemoteConfigClientConfig") -> bool:
    return (
        c._skip_shutdown
        if c._skip_shutdown is not None
        else any(is_distribution_available(_) for _ in REQUIRE_SKIP_SHUTDOWN)
    )


class RemoteConfigClientConfig(DDConfig):
    __prefix__ = "_dd.remote_configuration"

    log_payloads = DDConfig.v(bool, "log_payloads", default=False)

    _skip_shutdown = DDConfig.v(Optional[bool], "skip_shutdown", default=None)
    skip_shutdown = DDConfig.d(bool, derive_skip_shutdown)


config = RemoteConfigClientConfig()


class RemoteConfigError(Exception):
    """
    An error occurred during the configuration update procedure.
    The error is reported to the agent.
    """


@dataclasses.dataclass
class Signature:
    keyid: str
    sig: str


@dataclasses.dataclass
class Key:
    keytype: str
    keyid_hash_algorithms: list[str]
    keyval: Mapping
    scheme: str


@dataclasses.dataclass
class Role:
    keyids: list[str]
    threshold: int


@dataclasses.dataclass
class Root:
    _type: str
    spec_version: str
    consistent_snapshot: bool
    expires: str
    keys: Mapping[str, Key]
    roles: Mapping[str, Role]
    version: int = 0

    def __post_init__(self):
        if self._type != "root":
            raise ValueError("Root: invalid root type")
        for k, v in self.keys.items():
            if isinstance(v, dict):
                self.keys[k] = Key(**v)
        for k, v in self.roles.items():
            if isinstance(v, dict):
                self.roles[k] = Role(**v)


@dataclasses.dataclass
class SignedRoot:
    signatures: list[Signature]
    signed: Root

    def __post_init__(self):
        for i in range(len(self.signatures)):
            if isinstance(self.signatures[i], dict):
                self.signatures[i] = Signature(**self.signatures[i])
        if isinstance(self.signed, dict):
            self.signed = Root(**self.signed)


@dataclasses.dataclass
class TargetDesc:
    length: int
    hashes: Mapping[str, str]
    custom: Mapping[str, Any]


@dataclasses.dataclass
class Targets:
    _type: str
    custom: Mapping[str, Any]
    expires: str
    spec_version: str
    targets: Mapping[str, TargetDesc]
    version: int = 0

    def __post_init__(self):
        if self._type != "targets":
            raise ValueError("Targets: invalid targets type")
        if self.spec_version not in ("1.0", "1.0.0"):
            raise ValueError("Targets: invalid spec version")
        for k, v in self.targets.items():
            if isinstance(v, dict):
                self.targets[k] = TargetDesc(**v)


@dataclasses.dataclass
class SignedTargets:
    signatures: list[Signature]
    signed: Targets
    version: int = 0

    def __post_init__(self):
        for i in range(len(self.signatures)):
            if isinstance(self.signatures[i], dict):
                self.signatures[i] = Signature(**self.signatures[i])
        if isinstance(self.signed, dict):
            self.signed = Targets(**self.signed)


@dataclasses.dataclass
class TargetFile:
    path: str
    raw: str


@dataclasses.dataclass
class AgentPayload:
    roots: Optional[list[SignedRoot]] = None
    targets: Optional[SignedTargets] = None
    target_files: list[TargetFile] = dataclasses.field(default_factory=list)
    client_configs: set[str] = dataclasses.field(default_factory=set)

    def __post_init__(self):
        if self.roots is not None:
            for i in range(len(self.roots)):
                if isinstance(self.roots[i], str):
                    self.roots[i] = SignedRoot(**json.loads(base64.b64decode(self.roots[i])))
        if isinstance(self.targets, str):
            self.targets = SignedTargets(**json.loads(base64.b64decode(self.targets)))
        for i in range(len(self.target_files)):
            if isinstance(self.target_files[i], dict):
                self.target_files[i] = TargetFile(**self.target_files[i])


AppliedConfigType = dict[str, ConfigMetadata]
TargetsType = dict[str, ConfigMetadata]


class RemoteConfigClient:
    """
    The Remote Configuration client regularly checks for updates on the agent
    and dispatches configurations to registered products.
    """

    def __init__(self) -> None:
        tracer_version = _pep440_to_semver()

        self.id = str(uuid.uuid4())
        self.agent_url = agent_config.trace_agent_url

        self._headers = {"content-type": "application/json"}
        additional_header_str = os.environ.get("_DD_REMOTE_CONFIGURATION_ADDITIONAL_HEADERS")
        if additional_header_str is not None:
            self._headers.update(parse_tags_str(additional_header_str))

        tags = ddtrace.config.tags.copy()

        # Add git metadata tags, if available
        gitmetadata.add_tags(tags)

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
            extra_services=list(ddtrace.config._get_extra_services()),
            env=ddtrace.config.env,
            app_version=ddtrace.config.version,
            tags=[":".join(_) for _ in tags.items()],
        )

        if p_tags_list := process_tags.process_tags_list:
            self._client_tracer["process_tags"] = p_tags_list

        self.cached_target_files: list[AppliedConfigType] = []

        # Product callbacks for single subscriber architecture
        self._product_callbacks: dict[str, RCCallback] = {}

        # Track which products are enabled
        self._enabled_products: set[str] = set()

        # Single global connector and subscriber for all products
        self._global_connector = PublisherSubscriberConnector()
        self._global_subscriber = RemoteConfigSubscriber(
            self._global_connector, self._dispatch_to_products, "GlobalSubscriber"
        )

        self._applied_configs: AppliedConfigType = {}
        self._last_targets_version = 0
        self._last_error: Optional[str] = None
        self._backend_state: Optional[str] = None
        self._capabilities: int = 0

    def _encode_capabilities(self, capabilities: int) -> str:
        return base64.b64encode(capabilities.to_bytes((capabilities.bit_length() + 7) // 8, "big")).decode()

    def _dispatch_to_products(self, payloads: Sequence[Payload]) -> None:
        """Dispatch payloads from the global subscriber to registered product callbacks.

        This method runs in child processes and performs the following:
        1. Calls periodic() on all registered callbacks (happens every poll cycle)
        2. Groups payloads by product and dispatches them to their callbacks

        Args:
            payloads: Sequence of configuration payloads to dispatch
        """
        # Make a copy of the product callbacks at the time of dispatch to avoid
        # issues if callbacks are registered/unregistered while dispatching
        product_callbacks = self._product_callbacks.copy()

        # Call periodic method for all registered callbacks
        for product_name, callback in product_callbacks.items():
            try:
                log.debug(
                    "[%s][P: %s] Calling periodic method for product %s",
                    os.getpid(),
                    os.getppid(),
                    product_name,
                )
                callback.periodic()
            except Exception:
                log.error(
                    "[%s][P: %s] Error calling periodic method for product %s",
                    os.getpid(),
                    os.getppid(),
                    product_name,
                    exc_info=True,
                )

        if not payloads:
            return

        # Group payloads by product name
        product_payloads: dict[str, list[Payload]] = {}
        for payload in payloads:
            if payload.metadata and payload.metadata.product_name:
                product_name = payload.metadata.product_name
                if product_name not in product_payloads:
                    product_payloads[product_name] = []
                product_payloads[product_name].append(payload)

        # Dispatch to each product's callback
        for product_name, product_payload_list in product_payloads.items():
            product_callback = product_callbacks.get(product_name)
            if product_callback is not None:
                try:
                    log.debug(
                        "[%s][P: %s] Dispatching %d payloads to product %s",
                        os.getpid(),
                        os.getppid(),
                        len(product_payload_list),
                        product_name,
                    )
                    product_callback(product_payload_list)
                except Exception:
                    log.error(
                        "[%s][P: %s] Error dispatching to product %s",
                        os.getpid(),
                        os.getppid(),
                        product_name,
                        exc_info=True,
                    )

    def renew_id(self):
        # called after the process is forked to declare a new id
        self.id = str(uuid.uuid4())
        self._client_tracer["runtime_id"] = runtime.get_runtime_id()
        self._applied_configs.clear()

    def register_callback(
        self,
        product_name: str,
        callback: RCCallback,
    ) -> None:
        """
        Register a product callback for the single-subscriber architecture.

        Args:
            product_name: Name of the product (e.g., "ASM_FEATURES", "LIVE_DEBUGGING")
            callback: Callback function to invoke when payloads are received in child processes
        """
        self._product_callbacks[product_name] = callback
        log.debug("[%s][P: %s] Registered callback for product %s", os.getpid(), os.getppid(), product_name)

    def enable_product(self, product_name: str) -> None:
        """
        Enable a product to be included in client payloads sent to the agent.

        Enabling a product means it will be added to the 'products' list in the
        payload, signaling to the agent that this client wants to receive
        configurations for this product.

        Args:
            product_name: Name of the product to enable
        """
        self._enabled_products.add(product_name)
        log.debug("[%s][P: %s] Enabled product %s", os.getpid(), os.getppid(), product_name)

    def disable_product(self, product_name: str) -> None:
        """
        Disable a product, removing it from client payloads sent to the agent.

        The product's callback will remain registered and can still receive
        configurations if the agent sends them, but the client will not
        request configurations for this product.

        Args:
            product_name: Name of the product to disable
        """
        self._enabled_products.discard(product_name)
        log.debug("[%s][P: %s] Disabled product %s", os.getpid(), os.getppid(), product_name)

    def add_capabilities(self, capabilities: Iterable[enum.IntFlag]) -> None:
        for capability in capabilities:
            self._capabilities |= capability

    def update_product_callback(self, product_name: str, callback: RCCallback) -> bool:
        """Update the callback for a registered product."""
        if product_name in self._product_callbacks:
            self._product_callbacks[product_name] = callback
            log.debug("[%s][P: %s] Updated callback for product %s", os.getpid(), os.getppid(), product_name)
            return True
        return False

    def unregister_product(self, product_name: str) -> None:
        """Unregister a product."""
        self._product_callbacks.pop(product_name, None)
        log.debug("[%s][P: %s] Unregistered product %s", os.getpid(), os.getppid(), product_name)

    def is_subscriber_running(self) -> bool:
        """Check if the global subscriber is running."""
        return self._global_subscriber.status == ServiceStatus.RUNNING

    def start_subscriber(self) -> None:
        """Start the global subscriber thread."""
        if not self.is_subscriber_running():
            self._global_subscriber.start()
            log.debug("[%s][P: %s] Started global subscriber", os.getpid(), os.getppid())

    def stop_subscriber(self, join: bool = False) -> None:
        """Stop the global subscriber thread."""
        if self.is_subscriber_running():
            self._global_subscriber.stop(join=join)
            log.debug("[%s][P: %s] Stopped global subscriber", os.getpid(), os.getppid())

    def restart_subscriber(self, join: bool = False) -> None:
        """Restart the global subscriber thread."""
        self._global_subscriber.force_restart(join=join)
        log.debug("[%s][P: %s] Restarted global subscriber", os.getpid(), os.getppid())

    def reset_products(self) -> None:
        """Clear all registered products and enabled products."""
        self._product_callbacks = dict()
        self._enabled_products = set()

    def _send_request(self, payload: str) -> Optional[Mapping[str, Any]]:
        conn = None
        try:
            if config.log_payloads:
                log.debug("[%s][P: %s] RC request payload: %s", os.getpid(), os.getppid(), payload)

            conn = agent.get_connection(self.agent_url, timeout=agent_config.trace_agent_timeout_seconds)
            conn.request("POST", REMOTE_CONFIG_AGENT_ENDPOINT, payload, self._headers)
            resp = conn.getresponse()
            data_length = resp.headers.get("Content-Length")
            if data_length is not None and int(data_length) == 0:
                log.debug("[%s][P: %s] RC response payload empty", os.getpid(), os.getppid())
                return None
            data = resp.read()

            if config.log_payloads:
                log.debug("[%s][P: %s] RC response payload: %s", os.getpid(), os.getppid(), data.decode("utf-8"))
        except OSError as e:
            log.debug("Unexpected connection error in remote config client request: %s", str(e))
            return None
        finally:
            if conn is not None:
                conn.close()

        if resp.status == 404:
            # Remote configuration is not enabled or unsupported by the agent
            return None

        if resp.status < 200 or resp.status >= 300:
            log.debug("Unexpected error: HTTP error status %s, reason %s", resp.status, resp.reason)
            return None

        return json.loads(data)

    @staticmethod
    def _extract_target_file(payload: AgentPayload, target: str, config: ConfigMetadata) -> Optional[dict[str, Any]]:
        candidates = [item.raw for item in payload.target_files if item.path == target]
        if len(candidates) != 1 or candidates[0] is None:
            log.debug(
                "invalid target_files for %r. target files: %s", target, [item.path for item in payload.target_files]
            )
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

    def _build_payload(self, state: Mapping[str, Any]) -> Mapping[str, Any]:
        self._client_tracer["extra_services"] = list(ddtrace.config._get_extra_services())
        return dict(
            client=dict(
                id=self.id,
                products=list(self._enabled_products),
                is_tracer=True,
                client_tracer=self._client_tracer,
                state=state,
                capabilities=self._encode_capabilities(self._capabilities),
            ),
            cached_target_files=self.cached_target_files,
        )

    def _build_state(self) -> Mapping[str, Any]:
        has_error = self._last_error is not None
        state = dict(
            root_version=1,
            targets_version=self._last_targets_version,
            config_states=[
                (
                    dict(
                        id=config.id,
                        version=config.tuf_version,
                        product=config.product_name,
                        apply_state=config.apply_state,
                        apply_error=config.apply_error,
                    )
                    if config.apply_error
                    else dict(
                        id=config.id,
                        version=config.tuf_version,
                        product=config.product_name,
                        apply_state=config.apply_state,
                    )
                )
                for config in self._applied_configs.values()
            ],
            has_error=has_error,
        )
        if self._backend_state is not None:
            state["backend_client_state"] = self._backend_state
        if has_error:
            state["error"] = self._last_error
        return state

    @staticmethod
    def _accumulate_payload(
        payload_list: list[Payload],
        config_content: PayloadType,
        target: str,
        config_metadata: ConfigMetadata,
    ) -> None:
        """Accumulate a payload to be published to the global connector."""
        payload_list.append(Payload(config_metadata, target, config_content))

    def _remove_previously_applied_configurations(
        self,
        payload_list: list[Payload],
        applied_configs: AppliedConfigType,
        client_configs: TargetsType,
        targets: TargetsType,
    ) -> None:
        witness = object()
        for target, config in self._applied_configs.items():
            if client_configs.get(target, witness) == config:
                # The configuration has not changed.
                applied_configs[target] = config
                continue
            elif target not in targets:
                callback_action = None
            else:
                continue

            # Check if product is registered
            if config.product_name in self._product_callbacks:
                try:
                    log.debug("[%s][P: %s] Disabling configuration: %s", os.getpid(), os.getppid(), target)
                    self._accumulate_payload(payload_list, callback_action, target, config)
                except Exception:
                    log.debug("error while removing product %s config %r", config.product_name, config)
                    continue

    def _load_new_configurations(
        self,
        payload_list: list[Payload],
        applied_configs: AppliedConfigType,
        client_configs: TargetsType,
        payload: AgentPayload,
    ) -> None:
        for target, config in client_configs.items():
            # Check if product is registered
            if config.product_name in self._product_callbacks:
                applied_config = self._applied_configs.get(target)
                if applied_config == config:
                    continue
                config_content = self._extract_target_file(payload, target, config)
                if config_content is None:
                    continue

                try:
                    log.debug("[%s][P: %s] Load new configuration: %s. content", os.getpid(), os.getppid(), target)
                    self._accumulate_payload(payload_list, config_content, target, config)
                except Exception:
                    error_message = "Failed to apply configuration %s for product %r" % (config, config.product_name)
                    log.debug(error_message, exc_info=True)
                    config.apply_state = 3  # Error state
                    config.apply_error = error_message
                    applied_configs[target] = config
                    continue
                else:
                    config.apply_state = 2  # Acknowledged (applied)
                    applied_configs[target] = config

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

    def _validate_config_exists_in_target_paths(
        self, payload_client_configs: set[str], payload_target_files: list[TargetFile]
    ) -> None:
        paths = {_.path for _ in payload_target_files}
        paths = paths.union({_["path"] for _ in self.cached_target_files})

        # !(payload.client_configs is a subset of paths or payload.client_configs is equal to paths)
        if not set(payload_client_configs) <= paths:
            raise RemoteConfigError("Not all client configurations have target files")

    @staticmethod
    def _validate_signed_target_files(
        payload_target_files: list[TargetFile], payload_targets_signed: Targets, client_configs: TargetsType
    ) -> None:
        for target in payload_target_files:
            if (payload_targets_signed.targets and not payload_targets_signed.targets.get(target.path)) and (
                client_configs and not client_configs.get(target.path)
            ):
                raise RemoteConfigError(
                    "target file %s not exists in client_config and signed targets" % (target.path,)
                )

    def _publish_configuration(self, payload_list: list[Payload]) -> None:
        """Publish all accumulated payloads to the global connector."""
        if not payload_list:
            return

        log.debug(
            "[%s][P: %s] Publishing %d payloads to global connector",
            os.getpid(),
            os.getppid(),
            len(payload_list),
        )
        self._global_connector.write(payload_list)

    def _process_targets(self, payload: AgentPayload) -> tuple[Optional[int], Optional[str], Optional[TargetsType]]:
        if payload.targets is None:
            # no targets received
            return None, None, None
        signed = payload.targets.signed
        targets = dict()
        for target, metadata in signed.targets.items():
            m = TARGET_FORMAT.match(target)
            if m is None:
                raise RemoteConfigError("unexpected target format {!r}".format(target))
            _, product_name, config_id, _ = m.groups()
            targets[target] = ConfigMetadata(
                id=config_id,
                product_name=product_name,
                sha256_hash=metadata.hashes.get("sha256"),
                length=metadata.length,
                tuf_version=metadata.custom.get("v"),
            )
        backend_state = signed.custom.get("opaque_backend_state")
        return signed.version, backend_state, targets

    def _process_response(self, data: Mapping[str, Any]) -> None:
        try:
            payload = AgentPayload(**data)
        except Exception as e:
            log.debug("invalid agent payload received: %r", data, exc_info=True)
            msg = f"invalid agent payload received: {e}"
            raise RemoteConfigError(msg)

        self._validate_config_exists_in_target_paths(payload.client_configs, payload.target_files)

        # 1. Deserialize targets
        if payload.targets is None:
            return
        last_targets_version, backend_state, targets = self._process_targets(payload)
        if last_targets_version is None or targets is None:
            return

        client_configs = {k: v for k, v in targets.items() if k in payload.client_configs}
        log.debug(
            "[%s][P: %s] Retrieved client configs last version %s: %s",
            os.getpid(),
            os.getppid(),
            last_targets_version,
            client_configs,
        )

        self._validate_signed_target_files(payload.target_files, payload.targets.signed, client_configs)

        # 2. Remove previously applied configurations
        applied_configs: AppliedConfigType = dict()
        payload_list: list[Payload] = []
        self._remove_previously_applied_configurations(payload_list, applied_configs, client_configs, targets)

        # 3. Load new configurations
        self._load_new_configurations(payload_list, applied_configs, client_configs, payload)

        # 4. Publish all payloads to the global connector
        self._publish_configuration(payload_list)

        self._last_targets_version = last_targets_version
        self._applied_configs = applied_configs
        self._backend_state = backend_state

        self._add_apply_config_to_cache()

    def request(self) -> bool:
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
        except ValueError:
            log.debug("Unexpected response data", exc_info=True)
        except Exception:
            log.debug("Unexpected error", exc_info=True)

        return False
