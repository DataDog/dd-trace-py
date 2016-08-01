from ddtrace import __version__
from setuptools import setup, find_packages
import os

tests_require = [
    'mock',
    'nose',
    # contrib
    'blinker',
    'cassandra-driver',
    'django',
    'elasticsearch',
    'flask',
    'mongoengine',
    'psycopg2',
    'pymongo',
    'redis',
]


version = __version__
# Append a suffix to the version for dev builds
if os.environ.get('VERSION_SUFFIX'):
    version = '{v}+{s}'.format(
        v=version,
        s=os.environ.get('VERSION_SUFFIX'),
    )

setup(
    name='ddtrace',
    version=version,
    description='Datadog tracing code',
    url='https://github.com/DataDog/dd-trace-py',
    author='Datadog, Inc.',
    author_email='dev@datadoghq.com',
    license='BSD',
    packages=find_packages(exclude=['tests*']),
    tests_require=tests_require,
    test_suite="nose.collector",
    install_requires=[
        "wrapt"
    ]
)
