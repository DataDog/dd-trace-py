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
    python-packages.pip-tools
  ];

  # use this python version, and include the abvoe packages
  python = pinned.python310.withPackages python_packages;

  python27_packages = python-packages:
    [
      (python.pkgs.buildPythonPackage rec {
        pname = "pip-tools";
        version = "5.5.0";
        src = python.pkgs.fetchPypi {
          inherit pname version;
          sha256 =
            "sha256-cb0108391366b3ef336185097b3c2c0f3fa115b15098dafbda5e78aef70ea114=";
        };
        doCheck = false;
        propagatedBuildInputs = [ ];
      })
    ];
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

    #pinned.python35Packages.pip-tools
    #pinned.python36Packages.pip-tools
    #pinned.python37Packages.pip-tools
    #pinned.python38Packages.pip-tools
    pinned.python39Packages.pip-tools
    pinned.python310Packages.pip-tools
    #pinned.python311Packages.pip-tools
  ];

  shellHook = ''
    export PYTHON_VERSION="$(python -c 'import platform; import re; print(re.sub(r"\.\d+$", "", platform.python_version()))')"

    unset SOURCE_DATE_EPOCH

    # for c++ stuff like grpcio-tools, which is building from source but doesn't pick up the proper include
    export CFLAGS="-I${llvm.libcxx.dev}/include/c++/v1"

    virtualenv .venv
    source .venv/bin/activate
    .venv/bin/pip install -e .
    .venv/bin/pip install reno riot Cython
    .venv/bin/python setup.py develop
  '';
}
