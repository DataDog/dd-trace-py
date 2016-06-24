from setuptools import setup, find_packages

tests_require = [
    'nose',
    'flask',
    'blinker',
    'elasticsearch',

    # Not installed as long as we don't hace a proper CI setup
    #'psycopg2',
    #'sqlite3',
]

setup(
    name='ddtrace',
    version='0.1.4',
    description='Datadog tracing code',
    url='https://github.com/DataDog/dd-trace-py',
    author='Datadog, Inc.',
    author_email='dev@datadoghq.com',
    license='BSD',
    packages=find_packages(exclude=['tests*']),
    tests_require=tests_require,
    test_suite="nose.collector",
)
