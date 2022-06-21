import re
import hashlib
import base64
import logging
import os
import uuid
import json

from datetime import datetime

from typing import TYPE_CHECKING

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal import runtime
from ddtrace.internal import periodic
from ddtrace.internal.utils.time import StopWatch, parse_isoformat

import attr

log = logging.getLogger(__name__)

DEFAULT_REMOTECONFIG_POLL_SECONDS = 30
TARGET_FORMAT = re.compile(r"^(datadog/\d+|employee)/([^/]+)/([^/]+)/([^/]+)$")


def get_poll_interval_seconds():
    # type:() -> int
    return int(os.getenv("DD_REMOTECONFIG_POLL_SECONDS", default=DEFAULT_REMOTECONFIG_POLL_SECONDS))


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


if TYPE_CHECKING:
    from typing import Any, Callable, Optional, Mapping, MutableMapping, Tuple, Sequence

    ProductCallback = Callable[[ConfigMetadata, Optional[Mapping[str, Any]]], None]


class Client(periodic.PeriodicService):
    """
    The Remote Configuration client regularly checks for updates on the agent
    and dispatches configurations to registered products.
    """

    def __init__(
        self,
        runtime_id=runtime.get_runtime_id(),
        agent_url=agent.get_trace_url(),
        poll_interval=get_poll_interval_seconds(),
    ):
        # type: (str, str, float) -> None
        super(Client, self).__init__(interval=poll_interval)
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
        self._products = dict()  # type: MutableMapping[str, ProductCallback]
        self._applied_configs = dict()  # type: Mapping[str, ConfigMetadata]
        self._last_targets_version = 0
        self._last_error = None  # type: Optional[str]

    def register_product(self, product_name, func=None):
        # type: (str, Optional[ProductCallback]) -> None
        if func is not None:
            self._products[product_name] = func
        else:
            self._products.pop(product_name, None)

    def _request(self, state):
        # type: (Mapping[str, Any]) -> None
        payload = json.dumps(self._build_payload(state))
        with StopWatch() as sw:
            try:
                self._conn.request("POST", "v0.7/config", payload, self._headers)
                resp = self._conn.getresponse()
                data = resp.read()
            finally:
                self._conn.close()

        t = sw.elapsed()
        if t >= self.interval:
            log_level = logging.WARNING
        else:
            log_level = logging.DEBUG
        log.log(log_level, "request config with %db in %.5fs to %s", len(payload), t, self.agent_url)

        if resp.status == 404:
            # Remote configuration is not enabled or unsupported by the agent
            return

        if resp.status >= 200 and resp.status < 300:
            self._process_response(json.loads(data))
        else:
            log.warning("Unexpected error: HTTP error status %s, reason %s", resp.status, resp.reason)

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
        if has_error:
            state["error"] = self._last_error
        return state

    def _process_targets(self, payload):
        # type: (Mapping[str, Any]) -> Tuple[Optional[int], Optional[Mapping[str, ConfigMetadata]]]
        b64_targets = payload.get("targets", None)
        if b64_targets is None:
            log.debug("no targets received")
            return None, None

        try:
            raw_targets = base64.b64decode(b64_targets)
            parsed_targets = json.loads(raw_targets)
        except Exception:
            log.debug("invalid targets received", exc_info=True)
            return None, None

        signed = parsed_targets.get("signed")
        if signed is None:
            raise RemoteConfigError("no signed targets received")

        signed_type = signed.get("_type", "")
        signed_targets = signed.get("targets")
        if signed_type != "targets" or signed_targets is None:
            raise RemoteConfigError("invalid targets type")

        signed_expiration = signed.get("expires")
        if signed_expiration is None:
            raise RemoteConfigError("targets have no expiration time")

        try:
            expiration = parse_isoformat(signed_expiration)
            if expiration <= datetime.utcnow():
                raise RemoteConfigError("targets are expired, expiration date was {}".format(signed_expiration))
        except Exception:
            raise RemoteConfigError("invalid targets expiration, expiration date was {!r}", signed_expiration)

        targets = dict()
        for target, metadata in signed_targets.items():
            config = self._parse_target(target, metadata)
            if config is not None:
                targets[target] = config

        return signed.get("version"), targets

    def _parse_target(self, target, metadata):
        # type: (str, Mapping[str, Any]) -> ConfigMetadata
        m = TARGET_FORMAT.match(target)
        if m is None:
            raise RemoteConfigError("unexpected target format {!r}".format(target))
        _, product_name, config_id, _ = m.groups()
        return ConfigMetadata(
            id=config_id,
            product_name=product_name,
            sha256_hash=metadata.get("hashes", dict()).get("sha256"),
            tuf_version=metadata.get("custom", dict()).get("v"),
        )

    def _extract_target_file(self, target_files, target, config):
        # type: (Sequence[Mapping[str, Any]], str, ConfigMetadata) -> Optional[Mapping[str, Any]]
        candidates = [item.get("raw") for item in target_files if item.get("path") == target]
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

    def _process_response(self, payload):
        # type: (Mapping[str, Any]) -> None

        # 1. Deserialize targets
        last_targets_version, targets = self._process_targets(payload)
        if last_targets_version is None or targets is None:
            return

        client_configs = {k: v for k, v in targets.items() if k in payload.get("client_configs", [])}

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
                log.debug("%r is already applied", config)
                continue

            config_content = self._extract_target_file(payload.get("target_files") or [], target, config)
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

    def periodic(self):
        # type: () -> None
        try:
            state = self._build_state()
            self._request(state)
        except RemoteConfigError as e:
            self._last_error = str(e)
            log.warning("remote configuration client reported an error", exc_info=True)
        except Exception:
            log.warning("Unexpected error", exc_info=True)
        else:
            self._last_error = None


if __name__ == "__main__":
    import time

    def debug(metadata, config):
        print("hello {!r}".format(metadata))

    logging.basicConfig(level=logging.DEBUG)
    c = Client(poll_interval=5)
    c.register_product("ASM_DD", debug)
    c.register_product("FEATURES", debug)
    c.start()
    time.sleep(360)
