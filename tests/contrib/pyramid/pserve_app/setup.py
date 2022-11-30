from setuptools import setup, find_packages

setup(
    name="pserve_test_app",
    packages=find_packages(),
    include_package_data=True,
    zip_safe=False,
    entry_points={
        "paste.app_factory": [
            "main = app:main",
        ],
    },
)
