#!/usr/bin/env python3


import logging
import sys

logger = logging.getLogger()

try:
    import yaml
except:
    logger.exception("Yaml not found")
    sys.exit(1)


circleci_config = {
    "version": 2.1,
    "default_resource_class": "medium",
    "cimg_base_image": "cimg/base:stable",
    "python310_image": "cimg/python:3.10",
    "ddtrace_dev_image": "datadog/dd-trace-py:buster",
    "redis_image": "redis:4.0-alpine",
    "memcached_image": "memcached:1.5-alpine",
    "cassandra_image": "cassandra:3.11.7",
    "consul_image": "consul:1.6.0",
    "moto_image": "palazzem/moto:1.0.1",
    "mysql_image": "mysql:5.7",
    "postgres_image": "postgres:11-alpine",
    "mongo_image": "mongo:3.6",
    "httpbin_image": "kennethreitz/httpbin@sha256:2c7abc4803080c22928265744410173b6fea3b898872c01c5fd0f0f9df4a59fb",
    "vertica_image": "sumitchawla/vertica:latest",
    "rabbitmq_image": "rabbitmq:3.7-alpine",
    "orbs": {"win": "circleci/windows@5.0"},
    "machine_executor": {
        "machine": {"image": "ubuntu-2004:current"},
        "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
        "steps": [{"run": {"name": "Set global pyenv", "command": "pyenv global 3.9.4\n"}}],
    },
    "contrib_job": {"executor": "ddtrace_dev", "parallelism": 4},
    "contrib_job_small": {"executor": "ddtrace_dev_small", "parallelism": 1},
    "commands": {
        "save_coverage": {
            "description": "Save coverage.py results to workspace",
            "steps": [
                {
                    "run": "set -ex\nmkdir coverage\nif [ -f .coverage ];\nthen\n  cp .coverage ./coverage/$CIRCLE_BUILD_NUM-$CIRCLE_JOB-$CIRCLE_NODE_INDEX.coverage\nfi\n"
                },
                {"persist_to_workspace": {"root": "coverage", "paths": ["*.coverage"]}},
                {"store_artifacts": {"path": "coverage"}},
            ],
        },
        "setup_tox": {"description": "Install tox", "steps": [{"run": "pip install -U tox"}]},
        "setup_riot": {"description": "Install riot", "steps": [{"run": "pip3 install riot"}]},
        "restore_tox_cache": {
            "description": "Restore .tox directory from previous runs for faster installs",
            "steps": [
                {
                    "restore_cache": {
                        "key": 'tox-cache-{{ .Environment.CIRCLE_JOB }}-{{ checksum "tox.ini" }}-{{ checksum "setup.py" }}'
                    }
                }
            ],
        },
        "save_tox_cache": {
            "description": "Save .tox directory into cache for faster installs next time",
            "steps": [
                {
                    "save_cache": {
                        "key": 'tox-cache-{{ .Environment.CIRCLE_JOB }}-{{ checksum "tox.ini" }}-{{ checksum "setup.py" }}',
                        "paths": [".tox"],
                    }
                }
            ],
        },
        "save_pip_cache": {
            "description": "Save pip cache directory",
            "steps": [
                {
                    "save_cache": {
                        "key": 'pip-cache-{{ .Environment.CIRCLE_JOB }}-{{ .Environment.CIRCLE_NODE_INDEX }}-{{ checksum "riotfile.py" }}-{{ checksum "setup.py" }}',
                        "paths": [".cache/pip"],
                    }
                }
            ],
        },
        "restore_pip_cache": {
            "description": "Restore pip cache directory",
            "steps": [
                {
                    "restore_cache": {
                        "key": 'pip-cache-{{ .Environment.CIRCLE_JOB }}-{{ .Environment.CIRCLE_NODE_INDEX }}-{{ checksum "riotfile.py" }}-{{ checksum "setup.py" }}'
                    }
                }
            ],
        },
        "start_docker_services": {
            "description": "Start Docker services",
            "parameters": {"env": {"type": "string", "default": ""}, "services": {"type": "string", "default": ""}},
            "steps": [
                {
                    "run": "for i in {1..3}; do docker-compose pull -q << parameters.services >> && break || sleep 3; done"
                },
                {"run": "<< parameters.env >> docker-compose up -d << parameters.services >>"},
                {"run": {"command": "docker-compose logs -f", "background": True}},
            ],
        },
        "run_test": {
            "description": "Run tests matching a pattern",
            "parameters": {
                "pattern": {"type": "string", "default": ""},
                "wait": {"type": "string", "default": ""},
                "snapshot": {"type": "boolean", "default": False},
                "docker_services": {"type": "string", "default": ""},
                "store_coverage": {"type": "boolean", "default": True},
                "riotfile": {"type": "string", "default": "riotfile.py"},
            },
            "steps": [
                {"attach_workspace": {"at": "."}},
                "checkout",
                "restore_pip_cache",
                {
                    "when": {
                        "condition": "<< parameters.snapshot >>",
                        "steps": [
                            "setup_riot",
                            {
                                "start_docker_services": {
                                    "env": "SNAPSHOT_CI=1",
                                    "services": "testagent << parameters.docker_services >>",
                                }
                            },
                            {
                                "run": {
                                    "environment": {"DD_TRACE_AGENT_URL": "http://localhost:9126"},
                                    "command": "mv .riot .ddriot\nriot -f '<<parameters.riotfile>>' list -i '<<parameters.pattern>>' | circleci tests split | xargs -I PY ./scripts/ddtest riot -f '<<parameters.riotfile>>' -v run --python=PY --exitfirst --pass-env -s '<< parameters.pattern >>'\n",
                                }
                            },
                        ],
                    }
                },
                {
                    "unless": {
                        "condition": "<< parameters.snapshot >>",
                        "steps": [
                            {
                                "when": {
                                    "condition": "<< parameters.wait >>",
                                    "steps": [
                                        "setup_tox",
                                        {
                                            "run": {
                                                "name": "Waiting for << parameters.wait >>",
                                                "command": "tox -e 'wait' << parameters.wait >>",
                                            }
                                        },
                                    ],
                                }
                            },
                            "setup_riot",
                            {
                                "run": {
                                    "command": "riot list -i '<<parameters.pattern>>' | circleci tests split | xargs -I PY riot -v run --python=PY --exitfirst --pass-env -s '<< parameters.pattern >>'"
                                }
                            },
                        ],
                    }
                },
                "save_pip_cache",
                {"when": {"condition": "<< parameters.store_coverage >>", "steps": ["save_coverage"]}},
                {"store_test_results": {"path": "test-results"}},
                {"store_artifacts": {"path": "test-results"}},
            ],
        },
        "run_tox_scenario_with_testagent": {
            "description": "Run scripts/run-tox-scenario with setup, caching persistence and the testagent",
            "parameters": {"pattern": {"type": "string"}, "wait": {"type": "string", "default": ""}},
            "steps": [
                "checkout",
                "restore_tox_cache",
                {
                    "when": {
                        "condition": "<< parameters.wait >>",
                        "steps": [
                            {
                                "run": {
                                    "name": "Waiting for << parameters.wait >>",
                                    "command": "tox -e 'wait' << parameters.wait >>",
                                }
                            }
                        ],
                    }
                },
                {"start_docker_services": {"env": "SNAPSHOT_CI=1", "services": "memcached redis testagent"}},
                {
                    "run": {
                        "name": "Run scripts/run-tox-scenario",
                        "environment": {"DD_TRACE_AGENT_URL": "http://localhost:9126"},
                        "command": "./scripts/ddtest scripts/run-tox-scenario '<< parameters.pattern >>'",
                    }
                },
                "save_tox_cache",
            ],
        },
        "run_tox_scenario": {
            "description": "Run scripts/run-tox-scenario with setup, caching and persistence",
            "parameters": {
                "pattern": {"type": "string"},
                "wait": {"type": "string", "default": ""},
                "store_coverage": {"type": "boolean", "default": True},
            },
            "steps": [
                "checkout",
                "setup_tox",
                "restore_tox_cache",
                {
                    "when": {
                        "condition": "<< parameters.wait >>",
                        "steps": [
                            {
                                "run": {
                                    "name": "Waiting for << parameters.wait >>",
                                    "command": "tox -e 'wait' << parameters.wait >>",
                                }
                            }
                        ],
                    }
                },
                {
                    "run": {
                        "name": "Run scripts/run-tox-scenario",
                        "command": "scripts/run-tox-scenario '<< parameters.pattern >>'",
                    }
                },
                "save_tox_cache",
                {"when": {"condition": "<< parameters.store_coverage >>", "steps": ["save_coverage"]}},
                {"store_test_results": {"path": "test-results"}},
                {"store_artifacts": {"path": "test-results"}},
            ],
        },
    },
    "executors": {
        "cimg_base": {"docker": [{"image": "cimg/base:stable"}], "resource_class": "medium"},
        "python310": {"docker": [{"image": "cimg/python:3.10"}], "resource_class": "large"},
        "ddtrace_dev": {"docker": [{"image": "datadog/dd-trace-py:buster"}], "resource_class": "medium"},
        "ddtrace_dev_small": {"docker": [{"image": "datadog/dd-trace-py:buster"}], "resource_class": "small"},
    },
    "httpbin_local": {
        "image": "kennethreitz/httpbin@sha256:2c7abc4803080c22928265744410173b6fea3b898872c01c5fd0f0f9df4a59fb",
        "name": "httpbin.org",
    },
    "mysql_server": {
        "image": "mysql:5.7",
        "environment": ["MYSQL_ROOT_PASSWORD=admin", "MYSQL_PASSWORD=test", "MYSQL_USER=test", "MYSQL_DATABASE=test"],
    },
    "postgres_server": {
        "image": "postgres:11-alpine",
        "environment": ["POSTGRES_PASSWORD=postgres", "POSTGRES_USER=postgres", "POSTGRES_DB=postgres"],
    },
    "jobs": {
        "run_tox_contrib_job": {
            "parameters": {"pattern": {"type": "string", "default": ""}},
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "<<parameters.pattern>>"}}],
        },
        "run_tox_contrib_job_small": {
            "parameters": {"pattern": {"type": "string", "default": ""}},
            "executor": "ddtrace_dev_small",
            "parallelism": 1,
            "steps": [{"run_tox_scenario": {"pattern": "<<parameters.pattern>>"}}],
        },
        "run_test_machine_executor": {
            "parameters": {"pattern": {"type": "string", "default": ""}},
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}],
            "steps": [{"run_test": {"pattern": "<<parameters.pattern>>", "snapshot": True}}],
        },
        "run_test_contrib_job": {
            "parameters": {"pattern": {"type": "string", "default": ""}},
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_test": {"pattern": "<<parameters.pattern>>"}}],
        },
        "run_test_contrib_job_small": {
            "parameters": {"pattern": {"type": "string", "default": ""}},
            "executor": "ddtrace_dev_small",
            "parallelism": 1,
            "steps": [{"run_test": {"pattern": "<<parameters.pattern>>"}}],
        },
        "pre_check": {
            "executor": "python310",
            "steps": [
                "checkout",
                "setup_riot",
                {"run": {"name": "Formatting check", "command": "riot run -s fmt && git diff --exit-code"}},
                {"run": {"name": "Flake8 check", "command": "riot run -s flake8"}},
                {"run": {"name": "Slots check", "command": "riot run -s slotscheck"}},
                {"run": {"name": "Mypy check", "command": "riot run -s mypy"}},
                {"run": {"name": "Codespell check", "command": "riot run -s codespell"}},
                {
                    "run": {
                        "name": "Test agent snapshot check",
                        "command": "riot run -s snapshot-fmt && git diff --exit-code",
                    }
                },
            ],
        },
        "ccheck": {
            "executor": "cimg_base",
            "steps": [
                "checkout",
                {"run": "sudo apt-get update"},
                {
                    "run": "sudo apt-get install --yes clang-format gcc-10 g++-10 python3 python3-setuptools python3-pip cppcheck"
                },
                {"run": "scripts/cformat.sh"},
                {"run": "scripts/cppcheck.sh"},
                {"run": "DD_COMPILE_DEBUG=1 DD_TESTING_RAISE=1 CC=gcc-10 CXX=g++-10 pip -vvv install ."},
            ],
        },
        "coverage_report": {
            "executor": "python310",
            "steps": [
                "checkout",
                {"attach_workspace": {"at": "."}},
                {"run": "pip install coverage codecov diff_cover"},
                {"run": "ls -hal *.coverage"},
                {"run": "coverage combine *.coverage"},
                {"run": "codecov"},
                {"run": "coverage xml --ignore-errors"},
                {"store_artifacts": {"path": "coverage.xml"}},
                {"run": "coverage json --ignore-errors"},
                {"store_artifacts": {"path": "coverage.json"}},
                {"run": "coverage report --ignore-errors --omit=tests/"},
                {"run": "coverage report --ignore-errors --omit=ddtrace/"},
                {"run": "diff-cover --compare-branch $(git rev-parse --abbrev-ref origin/HEAD) coverage.xml"},
            ],
        },
        "build_base_venvs": {
            "resource_class": "large",
            "docker": [{"image": "datadog/dd-trace-py:buster"}],
            "parallelism": 7,
            "steps": [
                "checkout",
                "setup_riot",
                {"run": {"name": "Run riotfile.py tests", "command": "riot run -s riot-helpers"}},
                {"run": {"name": "Run scripts/*.py tests", "command": "riot run -s scripts"}},
                {
                    "run": {
                        "name": "Generate base virtual environments.",
                        "command": "riot list -i tracer | circleci tests split | xargs -I PY riot -v generate --python=PY",
                    }
                },
                {"persist_to_workspace": {"root": ".", "paths": ["."]}},
            ],
        },
        "build_latest_venvs": {
            "resource_class": "large",
            "docker": [{"image": "datadog/dd-trace-py:buster"}],
            "parallelism": 7,
            "steps": [
                "checkout",
                "setup_riot",
                {"run": {"name": "Run riotfile.py tests", "command": "riot -f riotfile-latest.py run -s riot-helpers"}},
                {"run": {"name": "Run scripts/*.py tests", "command": "riot -f riotfile-latest.py run -s scripts"}},
                {
                    "run": {
                        "name": "Generate base virtual environments.",
                        "command": "riot -f riotfile-latest.py list -i tracer | circleci tests split | xargs -I PY riot -f riotfile-latest.py -v generate --python=PY",
                    }
                },
                {"persist_to_workspace": {"root": ".", "paths": ["."]}},
            ],
        },
        "appsec": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "appsec", "snapshot": True}}],
        },
        "tracer": {
            "executor": "ddtrace_dev",
            "parallelism": 7,
            "steps": [
                {"run_test": {"pattern": "tracer"}},
                {"run_tox_scenario": {"pattern": "^py.\\+-tracer_test_http"}},
            ],
        },
        "telemetry": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "telemetry", "snapshot": True, "store_coverage": False}}],
        },
        "debugger": {"executor": "ddtrace_dev", "parallelism": 7, "steps": [{"run_test": {"pattern": "debugger"}}]},
        "opentracer": {
            "executor": "ddtrace_dev",
            "parallelism": 7,
            "steps": [{"run_tox_scenario": {"pattern": "^py.\\+-opentracer"}}],
        },
        "profile-windows-35": {
            "executor": {"name": "win/default", "shell": "bash.exe"},
            "steps": [
                {"run": "choco install -y python --version=3.5.4 --side-by-side"},
                {"run_tox_scenario": {"store_coverage": False, "pattern": "^py35-profile"}},
            ],
        },
        "profile-windows-36": {
            "executor": {"name": "win/default", "shell": "bash.exe"},
            "steps": [
                {"run": "choco install -y python --version=3.6.8 --side-by-side"},
                {"run_tox_scenario": {"store_coverage": False, "pattern": "^py36-profile"}},
            ],
        },
        "profile-windows-38": {
            "executor": {"name": "win/default", "shell": "bash.exe"},
            "steps": [
                {"run": "choco install -y python --version=3.8.10 --side-by-side"},
                {"run_tox_scenario": {"store_coverage": False, "pattern": "^py38-profile"}},
            ],
        },
        "profile-windows-39": {
            "executor": {"name": "win/default", "shell": "bash.exe"},
            "steps": [
                {"run": "choco install -y python --version=3.9.12 --side-by-side"},
                {"run_tox_scenario": {"store_coverage": False, "pattern": "^py39-profile"}},
            ],
        },
        "profile-windows-310": {
            "executor": {"name": "win/default", "shell": "bash.exe"},
            "steps": [{"run_tox_scenario": {"store_coverage": False, "pattern": "^py310-profile"}}],
        },
        "profile": {
            "executor": "ddtrace_dev",
            "parallelism": 7,
            "resource_class": "large",
            "steps": [{"run_tox_scenario": {"store_coverage": False, "pattern": "^py.\\+-profile"}}],
        },
        "integration_agent5": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {"attach_workspace": {"at": "."}},
                "checkout",
                {"start_docker_services": {"services": "ddagent5"}},
                {"run": {"command": "mv .riot .ddriot\n./scripts/ddtest riot -v run --pass-env -s 'integration-v5'\n"}},
            ],
        },
        "integration_agent": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {"attach_workspace": {"at": "."}},
                "checkout",
                {"start_docker_services": {"services": "ddagent"}},
                {
                    "run": {
                        "command": "mv .riot .ddriot\n./scripts/ddtest riot -v run --pass-env -s 'integration-latest'\n"
                    }
                },
            ],
        },
        "integration_testagent": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {"attach_workspace": {"at": "."}},
                "checkout",
                {"start_docker_services": {"env": "SNAPSHOT_CI=1", "services": "testagent"}},
                {
                    "run": {
                        "environment": {"DD_TRACE_AGENT_URL": "http://localhost:9126"},
                        "command": "mv .riot .ddriot\n./scripts/ddtest riot -v run --pass-env -s 'integration-snapshot'\n",
                    }
                },
            ],
        },
        "boto": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "^boto", "snapshot": True, "docker_services": "localstack"}}],
            "parallelism": 4,
        },
        "ddtracerun": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "redis:4.0-alpine"}],
            "steps": [{"run_test": {"store_coverage": False, "pattern": "ddtracerun"}}],
        },
        "asyncpg": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "asyncpg", "snapshot": True, "docker_services": "postgres"}}],
        },
        "aiohttp": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "aiohttp", "snapshot": True, "docker_services": "httpbin_local"}}],
            "parallelism": 6,
        },
        "aiohttp_latest": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {
                    "run_test": {
                        "pattern": "aiohttp",
                        "riotfile": "riotfile-latest.py",
                        "snapshot": True,
                        "docker_services": "httpbin_local",
                    }
                }
            ],
            "parallelism": 5,
        },
        "cassandra": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster", "environment": {"CASS_DRIVER_NO_EXTENSIONS": 1}},
                {"image": "cassandra:3.11.7", "environment": ["MAX_HEAP_SIZE=512M", "HEAP_NEWSIZE=256M"]},
            ],
            "steps": [{"run_test": {"wait": "cassandra", "pattern": "cassandra"}}],
        },
        "celery": {
            "executor": "ddtrace_dev",
            "parallelism": 7,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {"image": "redis:4.0-alpine"},
                {"image": "rabbitmq:3.7-alpine"},
            ],
            "steps": [{"run_test": {"pattern": "celery"}}],
        },
        "cherrypy": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "cherrypy", "snapshot": True}}],
            "parallelism": 6,
        },
        "consul": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "consul:1.6.0"}],
            "steps": [{"run_tox_scenario": {"pattern": "^consul_contrib-"}}],
        },
        "elasticsearch": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "elasticsearch", "snapshot": True, "docker_services": "elasticsearch"}}],
            "parallelism": 4,
        },
        "django": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {"run_test": {"pattern": "django$", "snapshot": True, "docker_services": "memcached redis postgres"}}
            ],
            "parallelism": 6,
        },
        "djangorestframework": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {"run_test": {"pattern": "djangorestframework", "snapshot": True, "docker_services": "memcached redis"}}
            ],
            "parallelism": 6,
        },
        "flask": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {
                    "run_test": {
                        "store_coverage": False,
                        "snapshot": True,
                        "pattern": "flask",
                        "docker_services": "memcached redis",
                    }
                }
            ],
            "parallelism": 7,
        },
        "gevent": {
            "executor": "ddtrace_dev",
            "parallelism": 7,
            "steps": [{"run_tox_scenario": {"pattern": "^gevent_contrib-"}}],
        },
        "grpc": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "grpc", "snapshot": True}}],
            "parallelism": 7,
        },
        "httplib": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "httplib", "snapshot": True, "docker_services": "httpbin_local"}}],
        },
        "httpx": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "httpx", "snapshot": True, "docker_services": "httpbin_local"}}],
        },
        "mariadb": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "mariadb$", "snapshot": True, "docker_services": "mariadb"}}],
        },
        "mysqlconnector": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {
                    "image": "mysql:5.7",
                    "environment": [
                        "MYSQL_ROOT_PASSWORD=admin",
                        "MYSQL_PASSWORD=test",
                        "MYSQL_USER=test",
                        "MYSQL_DATABASE=test",
                    ],
                },
            ],
            "steps": [{"run_test": {"wait": "mysql", "pattern": "mysql"}}],
        },
        "mysqlpython": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {
                    "image": "mysql:5.7",
                    "environment": [
                        "MYSQL_ROOT_PASSWORD=admin",
                        "MYSQL_PASSWORD=test",
                        "MYSQL_USER=test",
                        "MYSQL_DATABASE=test",
                    ],
                },
            ],
            "steps": [{"run_tox_scenario": {"wait": "mysql", "pattern": "^mysqldb_contrib-.*-mysqlclient"}}],
        },
        "pymysql": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {
                    "image": "mysql:5.7",
                    "environment": [
                        "MYSQL_ROOT_PASSWORD=admin",
                        "MYSQL_PASSWORD=test",
                        "MYSQL_USER=test",
                        "MYSQL_DATABASE=test",
                    ],
                },
            ],
            "steps": [{"run_test": {"wait": "mysql", "pattern": "pymysql"}}],
        },
        "pylibmc": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "memcached:1.5-alpine"}],
            "steps": [{"run_tox_scenario": {"pattern": "^pylibmc_contrib-"}}],
        },
        "pymemcache": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "memcached:1.5-alpine"}],
            "steps": [{"run_test": {"pattern": "pymemcache"}}],
        },
        "mongoengine": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "mongoengine", "snapshot": True, "docker_services": "mongo"}}],
            "parallelism": 1,
        },
        "pymongo": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "mongo:3.6"}],
            "steps": [{"run_test": {"pattern": "pymongo"}}],
        },
        "pyramid_latest": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "pyramid", "riotfile": "riotfile-latest.py", "snapshot": True}}],
        },
        "requests": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {
                    "image": "kennethreitz/httpbin@sha256:2c7abc4803080c22928265744410173b6fea3b898872c01c5fd0f0f9df4a59fb",
                    "name": "httpbin.org",
                },
            ],
            "steps": [{"run_test": {"pattern": "requests"}}],
        },
        "snowflake": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "snowflake", "snapshot": True}}],
            "parallelism": 4,
        },
        "sqlalchemy": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {
                    "image": "postgres:11-alpine",
                    "environment": ["POSTGRES_PASSWORD=postgres", "POSTGRES_USER=postgres", "POSTGRES_DB=postgres"],
                },
                {
                    "image": "mysql:5.7",
                    "environment": [
                        "MYSQL_ROOT_PASSWORD=admin",
                        "MYSQL_PASSWORD=test",
                        "MYSQL_USER=test",
                        "MYSQL_DATABASE=test",
                    ],
                },
            ],
            "steps": [{"run_test": {"wait": "postgres mysql", "pattern": "sqlalchemy"}}],
        },
        "psycopg": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "psycopg", "snapshot": True, "docker_services": "postgres"}}],
            "parallelism": 4,
        },
        "aiobotocore": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "palazzem/moto:1.0.1"}],
            "steps": [{"run_test": {"pattern": "aiobotocore"}}],
        },
        "aiobotocore_latest": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "palazzem/moto:1.0.1"}],
            "steps": [{"run_test": {"riotfile": "riotfile-latest.py", "pattern": "aiobotocore"}}],
        },
        "aiomysql": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [
                {"run_test": {"docker_services": "mysql", "wait": "mysql", "pattern": "aiomysql", "snapshot": True}}
            ],
        },
        "aiopg": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {
                    "image": "postgres:11-alpine",
                    "environment": ["POSTGRES_PASSWORD=postgres", "POSTGRES_USER=postgres", "POSTGRES_DB=postgres"],
                },
            ],
            "steps": [{"run_test": {"wait": "postgres", "pattern": "aiopg"}}],
        },
        "aioredis": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"docker_services": "redis", "pattern": "aioredis$", "snapshot": True}}],
            "parallelism": 4,
        },
        "aredis": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"docker_services": "redis", "pattern": "aredis$", "snapshot": True}}],
            "parallelism": 4,
        },
        "yaaredis": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"docker_services": "redis", "pattern": "yaaredis$", "snapshot": True}}],
            "parallelism": 4,
        },
        "redis": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"docker_services": "redis", "pattern": "redis$", "snapshot": True}}],
            "parallelism": 4,
        },
        "rediscluster": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "rediscluster", "docker_services": "rediscluster", "snapshot": True}}],
        },
        "rq": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "rq", "snapshot": True, "docker_services": "redis"}}],
            "parallelism": 4,
        },
        "urllib3": {
            "machine": {"image": "ubuntu-2004:current"},
            "environment": [{"BOTO_CONFIG": "/dev/null"}, {"PYTHONUNBUFFERED": 1}],
            "steps": [{"run_test": {"pattern": "urllib3", "snapshot": True, "docker_services": "httpbin_local"}}],
        },
        "vertica": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [
                {"image": "datadog/dd-trace-py:buster"},
                {
                    "image": "sumitchawla/vertica:latest",
                    "environment": ["VP_TEST_USER=dbadmin", "VP_TEST_PASSWORD=abc123", "VP_TEST_DATABASE=docker"],
                },
            ],
            "steps": [{"run_tox_scenario": {"wait": "vertica", "pattern": "^vertica_contrib-"}}],
        },
        "kombu": {
            "executor": "ddtrace_dev",
            "parallelism": 7,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "rabbitmq:3.7-alpine"}],
            "steps": [{"run_tox_scenario": {"wait": "rabbitmq", "pattern": "^kombu_contrib-"}}],
        },
        "benchmarks": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_test": {"store_coverage": False, "pattern": "^benchmarks"}}],
        },
        "build_docs": {
            "executor": "ddtrace_dev",
            "steps": [
                "setup_riot",
                "checkout",
                {"run": {"command": "riot -v run docs\nmkdir -p /tmp/docs\ncp -r docs/_build/html/* /tmp/docs\n"}},
                {"store_artifacts": {"path": "/tmp/docs"}},
            ],
        },
    },
    "requires_pre_check": {"requires": ["pre_check", "ccheck"]},
    "requires_base_venvs": {"requires": ["pre_check", "ccheck", "build_base_venvs"]},
    "requires_latest_venvs": {"requires": ["ccheck", "build_latest_venvs"]},
    "requires_tests": {
        "requires": [
            "run_test_contrib_job",
            "run_test_contrib_job_small",
            "run_test_machine_executor",
            "run_tox_contrib_job",
            "run_tox_contrib_job_small",
            "aiopg",
            "aioredis",
            "asyncpg",
            "benchmarks",
            "boto",
            "cassandra",
            "celery",
            "cherrypy",
            "consul",
            "ddtracerun",
            "django",
            "djangorestframework",
            "elasticsearch",
            "flask",
            "gevent",
            "grpc",
            "httplib",
            "httpx",
            "integration_agent5",
            "integration_agent",
            "integration_testagent",
            "profile",
            "kombu",
            "mariadb",
            "mongoengine",
            "mysqlconnector",
            "mysqlpython",
            "opentracer",
            "psycopg",
            "pylibmc",
            "pymemcache",
            "pymongo",
            "pymysql",
            "aredis",
            "yaaredis",
            "redis",
            "rediscluster",
            "requests",
            "rq",
            "snowflake",
            "sqlalchemy",
            "tracer",
            "telemetry",
            "debugger",
            "appsec",
            "urllib3",
            "vertica",
            "profile-windows-35",
            "profile-windows-36",
            "profile-windows-38",
            "profile-windows-39",
            "profile-windows-310",
        ]
    },
    "workflows": {
        "version": 2,
        "test": {
            "jobs": [
                "pre_check",
                "ccheck",
                "build_base_venvs",
                {"build_docs": {"requires": ["pre_check", "ccheck"]}},
                {
                    "run_test_contrib_job": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {"parameters": {"pattern": ["jinja2", "pynamodb", "falcon", "internal"]}},
                    }
                },
                {
                    "run_test_contrib_job_small": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {
                            "parameters": {
                                "pattern": [
                                    "vendor",
                                    "test_logging",
                                    "pylons",
                                    "mako",
                                    "asgi$",
                                    "pytest$",
                                    "pytest-bdd",
                                ]
                            }
                        },
                    }
                },
                {
                    "run_test_machine_executor": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {
                            "parameters": {
                                "pattern": [
                                    "fastapi",
                                    "django_hosts$",
                                    "graphene",
                                    "graphql",
                                    "pyramid",
                                    "sanic",
                                    "starlette",
                                    "wsgi",
                                ]
                            }
                        },
                    }
                },
                {
                    "run_tox_contrib_job": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {
                            "parameters": {
                                "pattern": [
                                    "^sqlite3_contrib-",
                                    "^algoliasearch_contrib-",
                                    "^requests_gevent_contrib-",
                                    "^molten_contrib-",
                                    "^dogpile_contrib-",
                                    "^bottle_contrib\\(_autopatch\\)\\?-",
                                    "^tornado_contrib-",
                                    "^pyodbc_contrib-",
                                    "^dbapi_contrib-",
                                ]
                            }
                        },
                    }
                },
                {
                    "run_tox_contrib_job_small": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {"parameters": {"pattern": ["^futures_contrib-", "^asyncio_contrib-"]}},
                    }
                },
                {"aiobotocore": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aiohttp": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aiomysql": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aiopg": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aioredis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"asyncpg": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"benchmarks": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"boto": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"cassandra": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"celery": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"cherrypy": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"consul": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"ddtracerun": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"django": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"djangorestframework": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"elasticsearch": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"flask": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"gevent": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"grpc": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"httplib": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"httpx": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"integration_agent5": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"integration_agent": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"integration_testagent": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"profile": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"kombu": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mariadb": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mongoengine": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mysqlconnector": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mysqlpython": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"opentracer": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"psycopg": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pylibmc": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pymemcache": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pymongo": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pymysql": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aredis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"yaaredis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"redis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"rediscluster": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"requests": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"rq": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"snowflake": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"sqlalchemy": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"tracer": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"telemetry": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"debugger": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"appsec": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"urllib3": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"vertica": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"profile-windows-35": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-36": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-38": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-39": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-310": {"requires": ["pre_check", "ccheck"]}},
                {
                    "coverage_report": {
                        "requires": [
                            "run_test_contrib_job",
                            "run_test_contrib_job_small",
                            "run_test_machine_executor",
                            "run_tox_contrib_job",
                            "run_tox_contrib_job_small",
                            "aiopg",
                            "aioredis",
                            "asyncpg",
                            "benchmarks",
                            "boto",
                            "cassandra",
                            "celery",
                            "cherrypy",
                            "consul",
                            "ddtracerun",
                            "django",
                            "djangorestframework",
                            "elasticsearch",
                            "flask",
                            "gevent",
                            "grpc",
                            "httplib",
                            "httpx",
                            "integration_agent5",
                            "integration_agent",
                            "integration_testagent",
                            "profile",
                            "kombu",
                            "mariadb",
                            "mongoengine",
                            "mysqlconnector",
                            "mysqlpython",
                            "opentracer",
                            "psycopg",
                            "pylibmc",
                            "pymemcache",
                            "pymongo",
                            "pymysql",
                            "aredis",
                            "yaaredis",
                            "redis",
                            "rediscluster",
                            "requests",
                            "rq",
                            "snowflake",
                            "sqlalchemy",
                            "tracer",
                            "telemetry",
                            "debugger",
                            "appsec",
                            "urllib3",
                            "vertica",
                            "profile-windows-35",
                            "profile-windows-36",
                            "profile-windows-38",
                            "profile-windows-39",
                            "profile-windows-310",
                        ]
                    }
                },
            ]
        },
        "test_latest": {
            "jobs": [
                "ccheck",
                "build_latest_venvs",
                {"aiobotocore_latest": {"requires": ["ccheck", "build_latest_venvs"]}},
                {"aiohttp_latest": {"requires": ["ccheck", "build_latest_venvs"]}},
                {"pyramid_latest": {"requires": ["ccheck", "build_latest_venvs"]}},
            ]
        },
        "test_nightly": {
            "jobs": [
                "pre_check",
                "ccheck",
                "build_base_venvs",
                {"build_docs": {"requires": ["pre_check", "ccheck"]}},
                {
                    "run_test_contrib_job": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {"parameters": {"pattern": ["jinja2", "pynamodb", "falcon", "internal"]}},
                    }
                },
                {
                    "run_test_contrib_job_small": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {
                            "parameters": {
                                "pattern": [
                                    "vendor",
                                    "test_logging",
                                    "pylons",
                                    "mako",
                                    "asgi$",
                                    "pytest$",
                                    "pytest-bdd",
                                ]
                            }
                        },
                    }
                },
                {
                    "run_test_machine_executor": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {
                            "parameters": {
                                "pattern": [
                                    "fastapi",
                                    "django_hosts$",
                                    "graphene",
                                    "graphql",
                                    "pyramid",
                                    "sanic",
                                    "starlette",
                                    "wsgi",
                                ]
                            }
                        },
                    }
                },
                {
                    "run_tox_contrib_job": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {
                            "parameters": {
                                "pattern": [
                                    "^sqlite3_contrib-",
                                    "^algoliasearch_contrib-",
                                    "^requests_gevent_contrib-",
                                    "^molten_contrib-",
                                    "^dogpile_contrib-",
                                    "^bottle_contrib\\(_autopatch\\)\\?-",
                                    "^tornado_contrib-",
                                    "^pyodbc_contrib-",
                                    "^dbapi_contrib-",
                                ]
                            }
                        },
                    }
                },
                {
                    "run_tox_contrib_job_small": {
                        "requires": ["pre_check", "ccheck", "build_base_venvs"],
                        "matrix": {"parameters": {"pattern": ["^futures_contrib-", "^asyncio_contrib-"]}},
                    }
                },
                {"aiobotocore": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aiohttp": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aiomysql": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aiopg": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aioredis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"asyncpg": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"benchmarks": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"boto": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"cassandra": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"celery": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"cherrypy": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"consul": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"ddtracerun": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"django": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"djangorestframework": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"elasticsearch": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"flask": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"gevent": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"grpc": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"httplib": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"httpx": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"integration_agent5": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"integration_agent": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"integration_testagent": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"profile": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"kombu": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mariadb": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mongoengine": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mysqlconnector": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"mysqlpython": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"opentracer": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"psycopg": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pylibmc": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pymemcache": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pymongo": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"pymysql": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"aredis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"yaaredis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"redis": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"rediscluster": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"requests": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"rq": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"snowflake": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"sqlalchemy": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"tracer": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"telemetry": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"debugger": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"appsec": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"urllib3": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"vertica": {"requires": ["pre_check", "ccheck", "build_base_venvs"]}},
                {"profile-windows-35": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-36": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-38": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-39": {"requires": ["pre_check", "ccheck"]}},
                {"profile-windows-310": {"requires": ["pre_check", "ccheck"]}},
                {
                    "coverage_report": {
                        "requires": [
                            "run_test_contrib_job",
                            "run_test_contrib_job_small",
                            "run_test_machine_executor",
                            "run_tox_contrib_job",
                            "run_tox_contrib_job_small",
                            "aiopg",
                            "aioredis",
                            "asyncpg",
                            "benchmarks",
                            "boto",
                            "cassandra",
                            "celery",
                            "cherrypy",
                            "consul",
                            "ddtracerun",
                            "django",
                            "djangorestframework",
                            "elasticsearch",
                            "flask",
                            "gevent",
                            "grpc",
                            "httplib",
                            "httpx",
                            "integration_agent5",
                            "integration_agent",
                            "integration_testagent",
                            "profile",
                            "kombu",
                            "mariadb",
                            "mongoengine",
                            "mysqlconnector",
                            "mysqlpython",
                            "opentracer",
                            "psycopg",
                            "pylibmc",
                            "pymemcache",
                            "pymongo",
                            "pymysql",
                            "aredis",
                            "yaaredis",
                            "redis",
                            "rediscluster",
                            "requests",
                            "rq",
                            "snowflake",
                            "sqlalchemy",
                            "tracer",
                            "telemetry",
                            "debugger",
                            "appsec",
                            "urllib3",
                            "vertica",
                            "profile-windows-35",
                            "profile-windows-36",
                            "profile-windows-38",
                            "profile-windows-39",
                            "profile-windows-310",
                        ]
                    }
                },
            ],
            "triggers": [{"schedule": {"cron": "0 0 * * *", "filters": {"branches": {"only": ["0.x", "1.x"]}}}}],
        },
    },
}



yaml.dump(circleci_config, sys.stdout, default_flow_style = False)
