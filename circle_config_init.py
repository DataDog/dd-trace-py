circleci_config = {
    "version": "2.1",
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
    "commands": {
        "save_coverage": {
            "description": "Save coverage.py results to workspace",
            "steps": [
                {
                    "run": (
                        "set -ex\nmkdir coverage\nif [ -f .coverage ];\nthen\n  cp .coverage"
                        " ./coverage/$CIRCLE_BUILD_NUM-$CIRCLE_JOB-$CIRCLE_NODE_INDEX.coverage\nfi\n"
                    )
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
                        "key": (
                            'tox-cache-{{ .Environment.CIRCLE_JOB }}-{{ checksum "tox.ini" }}-{{ checksum "setup.py" }}'
                        )
                    }
                }
            ],
        },
        "save_tox_cache": {
            "description": "Save .tox directory into cache for faster installs next time",
            "steps": [
                {
                    "save_cache": {
                        "key": (
                            'tox-cache-{{ .Environment.CIRCLE_JOB }}-{{ checksum "tox.ini" }}-{{ checksum "setup.py" }}'
                        ),
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
                        "key": (
                            "pip-cache-{{ .Environment.CIRCLE_JOB }}-{{ .Environment.CIRCLE_NODE_INDEX }}-{{ checksum"
                            ' "riotfile.py" }}-{{ checksum "setup.py" }}'
                        ),
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
                        "key": (
                            "pip-cache-{{ .Environment.CIRCLE_JOB }}-{{ .Environment.CIRCLE_NODE_INDEX }}-{{ checksum"
                            ' "riotfile.py" }}-{{ checksum "setup.py" }}'
                        )
                    }
                }
            ],
        },
        "start_docker_services": {
            "description": "Start Docker services",
            "parameters": {"env": {"type": "string", "default": ""}, "services": {"type": "string", "default": ""}},
            "steps": [
                {
                    "run": (
                        "for i in {1..3}; do docker-compose pull -q << parameters.services >> && break || sleep 3; done"
                    )
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
                "use_latest": {"type": "string", "default": "false"},
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
                                    "environment": {
                                        "DD_TRACE_AGENT_URL": "http://localhost:9126",
                                        "DD_USE_LATEST_VERSIONS": "<< parameters.use_latest >>",
                                    },
                                    "command": (
                                        "mv .riot .ddriot\nriot list -i '<<parameters.pattern>>' | circleci tests split"
                                        " | xargs -I PY ./scripts/ddtest riot -v run --python=PY --exitfirst --pass-env"
                                        " -s '<< parameters.pattern >>'\n"
                                    ),
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
                                                "environment": {
                                                    "DD_USE_LATEST_VERSIONS": "<< parameters.use_latest >>"
                                                },
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
                                    "environment": {"DD_USE_LATEST_VERSIONS": "<< parameters.use_latest >>"},
                                    "command": (
                                        "riot list -i '<<parameters.pattern>>' | circleci tests split | xargs -I PY"
                                        " riot -v run --python=PY --exitfirst --pass-env -s '<< parameters.pattern >>'"
                                    ),
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
                    "run": (
                        "sudo apt-get install --yes clang-format gcc-10 g++-10 python3 python3-setuptools python3-pip"
                        " cppcheck"
                    )
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
            "parallelism": 8,
            "steps": [
                "checkout",
                "setup_riot",
                {"run": {"name": "Run riotfile.py tests", "command": "riot run -s riot-helpers"}},
                {"run": {"name": "Run scripts/*.py tests", "command": "riot run -s scripts"}},
                {
                    "run": {
                        "name": "Generate base virtual environments.",
                        "command": (
                            "riot list -i tracer | circleci tests split | xargs -I PY riot -v generate --python=PY"
                        ),
                    }
                },
                {"persist_to_workspace": {"root": ".", "paths": ["."]}},
            ],
        },
        "opentracer": {
            "executor": "ddtrace_dev",
            "parallelism": 8,
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
        # "profile-windows-38": {
        #     "executor": {"name": "win/default", "shell": "bash.exe"},
        #     "steps": [
        #         {"run": "choco install -y python --version=3.8.10 --side-by-side"},
        #         {"run_tox_scenario": {"store_coverage": False, "pattern": "^py38-profile"}},
        #     ],
        # },
        # "profile-windows-39": {
        #     "executor": {"name": "win/default", "shell": "bash.exe"},
        #     "steps": [
        #         {"run": "choco install -y python --version=3.9.12 --side-by-side"},
        #         {"run_tox_scenario": {"store_coverage": False, "pattern": "^py39-profile"}},
        #     ],
        # },
        "profile-windows-310": {
            "executor": {"name": "win/default", "shell": "bash.exe"},
            "steps": [{"run_tox_scenario": {"store_coverage": False, "pattern": "^py310-profile"}}],
        },
        "profile": {
            "executor": "ddtrace_dev",
            "parallelism": 15,
            "resource_class": "large",
            "steps": [{"run_tox_scenario": {"store_coverage": False, "pattern": "^py.\\+-profile"}}],
        },
        "tornado": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "^tornado_contrib-"}}],
        },
        "bottle": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "^bottle_contrib\\(_autopatch\\)\\?-"}}],
        },
        "consul": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "consul:1.6.0"}],
            "steps": [{"run_tox_scenario": {"pattern": "^consul_contrib-"}}],
        },
        "dogpile_cache": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "^dogpile_contrib-"}}],
        },
        "gevent": {
            "executor": "ddtrace_dev",
            "parallelism": 8,
            "steps": [{"run_tox_scenario": {"pattern": "^gevent_contrib-"}}],
        },
        "molten": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "^molten_contrib-"}}],
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
        "pylibmc": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "memcached:1.5-alpine"}],
            "steps": [{"run_tox_scenario": {"pattern": "^pylibmc_contrib-"}}],
        },
        "pyodbc": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "docker": [{"image": "datadog/dd-trace-py:buster"}],
            "steps": [{"run_tox_scenario": {"pattern": "^pyodbc_contrib-"}}],
        },
        "dbapi": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "^dbapi_contrib-"}}],
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
            "parallelism": 8,
            "docker": [{"image": "datadog/dd-trace-py:buster"}, {"image": "rabbitmq:3.7-alpine"}],
            "steps": [{"run_tox_scenario": {"wait": "rabbitmq", "pattern": "^kombu_contrib-"}}],
        },
        "sqlite3": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "^sqlite3_contrib-"}}],
        },
        "algoliasearch": {
            "executor": "ddtrace_dev",
            "parallelism": 4,
            "steps": [{"run_tox_scenario": {"pattern": "^algoliasearch_contrib-"}}],
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
    "workflows": {
        "version": 2,
    },
}
