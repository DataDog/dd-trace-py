import typing as t

from ddtrace.internal.settings._core import DDConfig


class AgentlessConfig(DDConfig):
    # No __prefix__: _DD_APM_TRACING_AGENTLESS_ENABLED start with _.

    api_key = DDConfig.v(t.Optional[str], "dd.api_key", default=None)
    site = DDConfig.v(str, "dd.site", default="datadoghq.com")

    #: The global switch. Every product setting below defaults to it.
    enabled = DDConfig.v(bool, "dd.agentless.enabled", default=False)

    # Raw per-product overrides; None means "follow the global switch". Read the
    # resolved values below instead of these.
    _apm_tracing = DDConfig.v(t.Optional[bool], "_dd.apm.tracing.agentless.enabled", default=None)
    _ci_visibility = DDConfig.v(t.Optional[bool], "dd.civisibility.agentless.enabled", default=None)
    _llmobs = DDConfig.v(t.Optional[bool], "dd.llmobs.agentless.enabled", default=None)

    apm_tracing = DDConfig.d(bool, lambda c: c.enabled if c._apm_tracing is None else c._apm_tracing)
    ci_visibility = DDConfig.d(bool, lambda c: c.enabled if c._ci_visibility is None else c._ci_visibility)
    # LLM Observability keeps a third state: left unset (and with no global switch) it
    # probes the agent at startup and decides then, so it must not collapse to False.
    llmobs = DDConfig.d(t.Optional[bool], lambda c: True if c.enabled and c._llmobs is None else c._llmobs)

    #: Whether anything at all submits agentlessly. Products without a transport
    #: setting of their own (instrumentation telemetry) follow this.
    any_enabled = DDConfig.d(bool, lambda c: bool(c.enabled or c.apm_tracing or c.ci_visibility or c.llmobs))

    def __init__(self, *args: t.Any, **kwargs: t.Any) -> None:
        super().__init__(*args, **kwargs)

        if self.enabled and not self.api_key:
            msg = (
                "DD_AGENTLESS_ENABLED is set but DD_API_KEY is not. Agentless mode submits data "
                "straight to the Datadog intake, which is not possible without an API key. "
                "Set DD_API_KEY, or unset DD_AGENTLESS_ENABLED to submit through the agent."
            )
            raise ValueError(msg)

    def reported_configuration(self) -> "list[tuple[str, t.Any, str]]":
        """The (environment variable, effective value, origin) triples to report as telemetry.

        Agentless config is resolved early, and we thus must explicitly report our config.
        """
        return [
            (env_name, value, self.value_source(env_name))
            for env_name, value in (
                ("DD_AGENTLESS_ENABLED", self.enabled),
                ("DD_SITE", self.site),
                ("_DD_APM_TRACING_AGENTLESS_ENABLED", self.apm_tracing),
                ("DD_CIVISIBILITY_AGENTLESS_ENABLED", self.ci_visibility),
                ("DD_LLMOBS_AGENTLESS_ENABLED", self.llmobs),
            )
        ]


config = AgentlessConfig()
