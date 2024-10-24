#!/bin/sh

mkdir tmp_ddprof
cd tmp_ddprof
curl -Lo ddprof-linux.tar.xz https://github.com/DataDog/ddprof/releases/latest/download/ddprof-amd64-linux.tar.xz
tar xvf ddprof-linux.tar.xz
mv ddprof/lib/*.so ..
cd ..
rm -rf tmp_ddprof
