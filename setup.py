from setuptools import setup

tests_require = [
    'nose',
    #'psycopg2',
    #'sqlite3'
    'flask',
    'blinker',
]

setup(
    name='ddtrace',
    version='0.1.2',
    description='Datadog tracing code',
    url='https://github.com/DataDog/dd-trace-py',
    author='Datadog, Inc.',
    author_email='dev@datadoghq.com',
    license='BSD',
    packages=[
        'ddtrace',
        'ddtrace.contrib',
        'ddtrace.contrib.flask',
        'ddtrace.contrib.psycopg',
        'ddtrace.contrib.pylons',
        'ddtrace.contrib.sqlite3',
        'ddtrace.ext',
    ],
    tests_require=tests_require,
    test_suite="nose.collector",
)
