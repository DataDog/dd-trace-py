from envier import En


class AppSecConfig(En):
    __prefix__ = "appsec"

    enabled = En.v(bool, "enabled", default=False)
