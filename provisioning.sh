#! /usr/bin/env bash

# install python versions and requirements
apt-get update
apt-get install -y python python-pip python3 python3-pip git build-essential autoconf libtool unzip

# dependencies for our benchmarks
# TODO[manu]: they can be reduced with better benchmarks
pip2 install nose mock msgpack-python
pip3 install nose mock msgpack-python

# build protobuff CPP accelerated implementation
git clone https://github.com/google/protobuf.git .protobuf
cd .protobuf
./autogen.sh
./configure
make && make install

# build python extension
cd python
python2 setup.py install --cpp_implementation
python3 setup.py install --cpp_implementation
ldconfig
