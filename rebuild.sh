export DD_FAST_BUILD=1
export CMAKE_BUILD_PARALLEL_LEVEL=$(nproc)

bear -- python3 setup.py build_ext --inplace -v