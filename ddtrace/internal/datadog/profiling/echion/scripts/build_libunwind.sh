#/bin/bash

LIBUNWIND_VERSION="1.7.2"

# Install libunwind
git clone https://github.com/libunwind/libunwind --branch "v${LIBUNWIND_VERSION}" --depth 1
cd libunwind
autoreconf -i
./configure CFLAGS='-fPIC' CXXFLAGS='-fPIC'
make -j8
make install
cd ..
rm -rf libunwind
