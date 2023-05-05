{
# use the environment channel
pkgs ? import <nixpkgs> { },

# use a pinned package state
pinned ? import
  (fetchTarball ("https://github.com/NixOS/nixpkgs/archive/14d9b465c71.tar.gz"))
  { }, }:
let
  # get these python packages from nix
  python_packages = python-packages: [
    python-packages.pip
    python-packages.virtualenv
    python-packages.setuptools
    python-packages.cython
  ];

  # use this python version, and include the abvoe packages
  python = pinned.python310.withPackages python_packages;

  click_py27 = python27.pkgs.buildPythonPackage rec {
    pname = "click";
    version = "7.1.2";
    src = python27.pkgs.fetchPypi {
      inherit pname version;
      sha256 = "sha256-0rUlXHxjSbwb0eWeCM0SrLvWPOZJ8liHVXg6qU37axo=";
    };
    doCheck = false;
    buildInputs = [ ];
  };
  python27_packages = python-packages: [ click_py27 pinned.python27.pkgs.pip ];
  python27 = pinned.python27.withPackages python27_packages;

  # control llvm/clang version (e.g for packages built form source)
  llvm = pinned.llvmPackages_12;

in llvm.stdenv.mkDerivation {
  # unique project name for this environment derivation
  name = "dd-trace-py.devshell";

  buildInputs = [
    # version to use + default packages are declared above
    python
    python27

    # for c++ dependencies such as grpcio-tools
    llvm.libcxx.dev
    pinned.zlib
    pinned.openssl
  ];

  shellHook = ''
    export PYTHON_VERSION="$(python -c 'import platform; import re; print(re.sub(r"\.\d+$", "", platform.python_version()))')"

    unset SOURCE_DATE_EPOCH

    # for c++ stuff like grpcio-tools, which is building from source but doesn't pick up the proper include
    export CFLAGS="-I${llvm.libcxx.dev}/include/c++/v1"

    virtualenv .venv
    source .venv/bin/activate
    .venv/bin/pip install -e .
    .venv/bin/pip install -e ../riot
    .venv/bin/pip install reno Cython
    .venv/bin/python setup.py develop
  '';
}
