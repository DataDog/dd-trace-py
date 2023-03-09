# Datadog Git Metadata embedding library

The library allows easily tag telemetry for 
[Datadog Source Code Integration](https://docs.datadoghq.com/integrations/guide/source-code-integration/)
 
It embeds git metadata:
* **Repository URL**
* **Commit hash**

to the python package metadata field `Project-URL` with name `source_code_link`

It will override setup method from the standard library and put `source_code_link` to the `project_urls["source_code_link"]`
in the format: `repository_url#commit_hash`

Usage: import the module in the setup.py file, e.g.
```
import ddgitmetadata
from setuptools import setup

setup(
    name="somepackagename",
    version="1.0",
    description="Some Package Description",
    author="First Last",
    author_email="example@mail.net",
    packages=["somepackage"],
    project_urls={
        "Bug Tracker": "https://bug.tracker.link",
        "Documentation": "https://some.documentation.link/",
        "Source Code": "https://github.com/sashacmc/dd-git-metadata-poc",
    },
    zip_safe=True,
)
```

This metadata will be extracted by [Datadog's APM client Python package](https://github.com/DataDog/dd-trace-py) and emebedded to the telemetry.
