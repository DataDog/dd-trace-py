#!/usr/bin/env python
#import docker
from python_on_whales import docker

from github import Auth
from github import Github

import os
import sys
import yaml

def get_versions(contrib):
    auth = Auth.Token(os.environ["GH_TOKEN"])

    # Public Web Github
    g = Github(auth=auth)

    # Then play with your Github objects:
    repository = g.get_repo(contrib["repository"])
    tags = repository.get_tags()

    results =  [tag.name for tag in tags]
    g.close()

    return results

def get_runtimes():
    yaml_file = "supported.yaml"

    data_loaded = None
    with open(yaml_file, 'r') as stream:
        data_loaded = yaml.safe_load(stream)

    if data_loaded is None:
        print("No data in yaml file: %s" % yaml_file)
        sys.exit(0)

    python_versions = data_loaded["python-runtimes"]
    contribs = data_loaded["contribs"]

    return python_versions, contribs


def build_images(runtimes, contrib_versions):
    client = docker.from_env()
    client = docker.DockerClient(base_url='unix://var/run/docker.sock', credstore_env={"credHelpers": ""})
    login_response = client.login("teague.bick", os.environ.get("GH_TOKEN"), "teague.bick@datadoghq.com", "ghcr.io")


    for version in runtimes:
        for contrib, c_versions in contrib_versions.items():
            for c_version in c_versions:
                build_args = {"PYTHON_VERSION": version, "CONTRIB_NAME": contrib, "CONTRIB_VERSION": c_version}
                print("Building Image for : %s", build_args)
                result = client.images.build(path=".", tag=f"{version}-{contrib}-{c_version}", buildargs=build_args)
                print(result)
                breakpoint()


def build_and_run(runtime, contrib, contrib_version, application_dir, ddtrace_version):
    """
    client = docker.from_env()
    client = docker.DockerClient(base_url='unix://var/run/docker.sock', credstore_env={"credHelpers": ""})
    login_response = client.login("teague.bick", os.environ.get("GH_TOKEN"), "teague.bick@datadoghq.com", "ghcr.io")
    build_args = {"PYTHON_VERSION": runtime, "CONTRIB_NAME": contrib, "CONTRIB_VERSION": contrib_version, "DD_VERSION": ddtrace_version}
    print("Building Image for: %", build_args)
    result = client.images.build(path=application_dir, tag=f"{runtime}-{contrib}-{contrib_version}", buildargs=build_args)
    image = result[0]
    print(result)
    print("Running Container for: %s", image)
    result = client.containers.run(image, detach=True)
    print(result)
    """
    build_args = {"PYTHON_VERSION": runtime, "CONTRIB_NAME": contrib, "CONTRIB_VERSION": contrib_version, "DD_VERSION": ddtrace_version}
    os.environ.update(build_args)
    docker.compose.up(build=True, detach=True, recreate=True)


def down(runtime, contrib, contrib_version, application_dir, ddtrace_version):
    build_args = {"PYTHON_VERSION": runtime, "CONTRIB_NAME": contrib, "CONTRIB_VERSION": contrib_version, "DD_VERSION": ddtrace_version}
    os.environ.update(build_args)
    docker.compose.down()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Build and run the teague scenarios")
    parser.add_argument("--runtime", help="Python runtime to use", default="3.8")
    parser.add_argument("--contrib", help="Contrib to use", default="pyramid")
    parser.add_argument("--contrib-version", help="Contrib version to use", default="2.0.2")
    parser.add_argument("--ddtrace-version", help="DDTrace version to use", default="2.17.0")
    parser.add_argument("--down", help="Down the containers", action="store_true")
    runtimes, contribs = get_runtimes()

    args = parser.parse_args()
    runtime = args.runtime
    contrib = args.contrib
    contrib_version = args.contrib_version
    application_dir = args.contrib
    ddtrace_version = args.ddtrace_version

    print("Runtimes: %s" % runtime)
    print("Contribs: %s" % contrib)
    print("Contrib Versions: %s" % contrib_version)
    print("Application: %s" % application_dir)

    #contrib_versions = dict()
    #for contrib in contribs:
    #    contrib_versions[contrib["name"]] = get_versions(contrib)
    #build_images(runtimes, contrib_versions)

    if not args.down:
        build_and_run(runtime, contrib, contrib_version, application_dir, ddtrace_version)
    else:
        down(runtime, contrib, contrib_version, application_dir, ddtrace_version)


if __name__ == "__main__":
    main()