from envier import En


class AppSecConfig(En):
    __prefix__ = "dd.appsec"

    enabled = En.v(bool, "enabled", default=False)
