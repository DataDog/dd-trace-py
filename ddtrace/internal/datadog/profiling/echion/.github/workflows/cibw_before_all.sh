#!/bin/bash

set -ex

# musllinux doesn't have libtool by default
if command -v apk &> /dev/null; then
  apk add libtool
fi

. scripts/build_libunwind.sh

if command -v yum &> /dev/null; then
    sed -i 's/mirrorlist/#mirrorlist/g' /etc/yum.repos.d/CentOS-*
    sed -i 's|#baseurl=http://mirror.centos.org|baseurl=http://vault.centos.org|g' /etc/yum.repos.d/CentOS-*
fi

# Install xz
if command -v yum &> /dev/null; then
    yum makecache
    yum install -y gettext-devel
elif command -v apk &> /dev/null; then
    apk add gettext-dev
fi

git clone https://git.tukaani.org/xz.git
cd xz
./autogen.sh --no-po4a
./configure CFLAGS='-fPIC' CXXFLAGS='-fPIC'
make
make install
cd ..
rm -rf xz
