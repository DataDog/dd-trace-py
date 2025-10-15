"""Setup for local flake8 plugin - integrated with hatch lint environment."""

from setuptools import setup


setup(
    name="flake8-environ-rule",
    version="1.0.0",
    py_modules=["environ_checker"],
    entry_points={
        "flake8.extension": [
            "ENV = environ_checker:EnvironChecker",
        ],
    },
    install_requires=[
        "flake8>=3.0.0",
    ],
)
