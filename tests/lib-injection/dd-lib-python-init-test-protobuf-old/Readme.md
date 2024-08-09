# Purpose

This is a regression test to detect situations where:
1. autoinject erroneously brings in a new version of protobuf, causing issues processing pb.py files generated with an old version of protoc
2. when the profiler is loaded, it tries to load its own pb.py files with the customer's old version of protobuf, which cannot process its too-new pb.py files


# How to reproduce the test itself

This test uses assets which were generated outside of the test itself.
At the time of writing, the following works to generate the required assets

```
wget -c https://github.com/protocolbuffers/protobuf/releases/download/v2.6.1/protobuf-2.6.1.tar.bz2
tar -xvf protobuf-2.6.1.tar.bz2
pushd protobuf-2.6.1
./configure && make -j
popd
mv ./protobuf-2.6.1/examples/addressbook.proto .
./protobuf-2.6.1/src/protoc --python_out=. addressbook.proto
```
