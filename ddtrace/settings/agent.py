from envier import En


class AgentConfig(En):
    __prefix__ = "dd"

    hostname = En.v(str, "agent.host", default="localhost")
    port = En.v(int, "agent.port", default=8126)
    stats_port = En.v(int, "dogstatsd_port", default=8125)

    url = En.d(str, lambda c: "http://%s:%s" % (c.hostname, c.port))
    stats_url = En.d(str, lambda c: "http://%s:%s" % (c.hostname, c.stats_port))
