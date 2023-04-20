{
  # use the environment channel
  pkgs ? import <nixpkgs> {},

  # use a pinned package state
  pinned ? import(fetchTarball("https://github.com/NixOS/nixpkgs/archive/14d9b465c71.tar.gz")) {},
}:
let
  # get these python packages from nix
  python_packages = python-packages: [
    python-packages.pip
    python-packages.setuptools
    python-packages.cython
  ];

  # use this python version, and include the abvoe packages
  python = pinned.python39.withPackages python_packages;

  # control llvm/clang version (e.g for packages built form source)
  llvm = pinned.llvmPackages_12;
in llvm.stdenv.mkDerivation {
  # unique project name for this environment derivation
  name = "dd-trace-py.devshell";

  buildInputs = [
    # version to use + default packages are declared above
    python

    # for c++ dependencies such as grpcio-tools
    llvm.libcxx.dev
  ];

  shellHook = ''
    export PYTHON_VERSION="$(python -c 'import platform; import re; print(re.sub(r"\.\d+$", "", platform.python_version()))')"

    # replicate virtualenv behaviour
    export PIP_PREFIX="$PWD/vendor/python/$PYTHON_VERSION/packages"
    export PYTHONPATH="$PIP_PREFIX/lib/python$PYTHON_VERSION/site-packages:$PYTHONPATH"
    unset SOURCE_DATE_EPOCH
    export PATH="$PIP_PREFIX/bin:$PATH"

    # for c++ stuff like grpcio-tools, which is building from source but doesn't pick up the proper include
    export CFLAGS="-I${llvm.libcxx.dev}/include/c++/v1"
  '';
}
