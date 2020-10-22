from . import (
    appveyor,
    azure_pipelines,
    bitbucket,
    buildkite,
    circle_ci,
    github_actions,
    gitlab,
    jenkins,
    teamcity,
    travis,
)


PROVIDERS = (
    travis,
    bitbucket,
    circle_ci,
    jenkins,
    gitlab,
    appveyor,
    azure_pipelines,
    github_actions,
    teamcity,
    buildkite,
)

__all__ = ("PROVIDERS",)
