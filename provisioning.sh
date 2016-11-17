#! /usr/bin/env bash

apt-get update
apt-get install -y python python-pip python3 python3-pip
pip2 install nose mock msgpack-python
pip3 install nose mock msgpack-python
