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
  piptools_py27 = python27.pkgs.buildPythonPackage rec {
    pname = "pip-tools";
    version = "5.5.0";
    src = python27.pkgs.fetchPypi {
      inherit pname version;
      sha256 = "sha256-ywEIORNms+8zYYUJezwsDz+hFbFQmNr72l54rvcOoRQ=";
    };
    doCheck = false;
    buildInputs = [
      click_py27
      pinned.python27.pkgs.setuptools
      pinned.python27.pkgs.setuptools_scm
    ];
  };
  python27_packages = python-packages: [
    click_py27
    piptools_py27
    pinned.python27.pkgs.pip
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
    pinned.zlib
    pinned.openssl

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
    export CPPFLAGS="-I${pinned.zlib.dev}/include -I${pinned.openssl.dev}/include/openssl"
    export LDFLAGS="-L${pinned.zlib}/lib -L${pinned.openssl.out}/lib"
    echo $CPPFLAGS
    echo $LDFLAGS
    ls -al ${pinned.openssl.dev}/include/openssl
    ls -al ${pinned.openssl.out}/lib

    rm -rf ~/.pyenv
    git clone https://github.com/pyenv/pyenv.git ~/.pyenv
    export PYENV_ROOT=~/.pyenv
    export PATH=~/.pyenv/bin:$PATH
    eval "$(pyenv init --path)"
    pyenv install 2.7

    .venv/bin/pip install -e .
    .venv/bin/pip install reno riot Cython
    .venv/bin/python setup.py develop
  '';
}
