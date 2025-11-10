---
name: bootstrap
description: >
  Set up the dd-trace-py development environment from scratch.
  Use this when first setting up the project, onboarding new developers,
  or rebuilding the environment. Handles Python version installation,
  development dependencies, native extensions build, and Docker services setup.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - TodoWrite
---

# Development Environment Bootstrap Skill

This skill guides you through setting up a complete dd-trace-py development environment from scratch.

## When to Use This Skill

Use this skill when:
- First-time contributor setting up the project
- Onboarding a new developer to the codebase
- Rebuilding development environment after system changes
- Troubleshooting environment-related issues
- Setting up CI/CD runners or containers

## Prerequisites

Before starting, ensure you have:
- **Git** - For cloning the repository
- **Docker** - For running service dependencies (redis, postgres, etc.)
  - Install: https://www.docker.com/products/docker
- **Build tools** for your platform:
  - **Linux**: `gcc`, `g++`, `make`, `cmake`, `pkg-config`
  - **macOS**: Xcode Command Line Tools (`xcode-select --install`)
  - **Windows**: Visual Studio with C++ build tools

## Step 1: Install Python Version Manager

You need a Python version manager to install and manage multiple Python versions (3.8-3.14).

### Option A: Install uv (Recommended - Fast and Modern)

**uv** is a fast Python package and project manager written in Rust.

```bash
# macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Verify installation
uv --version
```

**Documentation**: https://docs.astral.sh/uv/getting-started/installation/

### Option B: Install pyenv (Traditional Approach)

**pyenv** allows you to easily switch between multiple Python versions.

```bash
# macOS (with Homebrew)
brew install pyenv

# Linux (automated installer)
curl https://pyenv.run | bash

# Add to shell configuration (~/.bashrc, ~/.zshrc, etc.)
echo 'export PYENV_ROOT="$HOME/.pyenv"' >> ~/.bashrc
echo 'command -v pyenv >/dev/null || export PATH="$PYENV_ROOT/bin:$PATH"' >> ~/.bashrc
echo 'eval "$(pyenv init -)"' >> ~/.bashrc

# Restart shell or source config
source ~/.bashrc

# Verify installation
pyenv --version
```

**Documentation**: https://github.com/pyenv/pyenv#installation

## Step 2: Install Required Python Versions

dd-trace-py supports Python 3.8 through 3.14. Install the versions you need for development:

### With uv:

```bash
# Install all supported Python versions
uv python install 3.8
uv python install 3.9
uv python install 3.10
uv python install 3.11
uv python install 3.12
uv python install 3.13
uv python install 3.14

# Verify installations
uv python list
```

### With pyenv:

```bash
# Install all supported Python versions
pyenv install 3.8.19      # Latest 3.8.x
pyenv install 3.9.24      # Latest 3.9.x
pyenv install 3.10.19     # Latest 3.10.x
pyenv install 3.11.14     # Latest 3.11.x
pyenv install 3.12.12     # Latest 3.12.x
pyenv install 3.13.9      # Latest 3.13.x
pyenv install 3.14.0      # Latest 3.14.x (if available)

# Set local Python versions for the project
cd /path/to/dd-trace-py
pyenv local 3.13.9 3.12.12 3.11.14 3.10.19 3.9.24 3.8.19

# Verify installations
pyenv versions
```

**Minimum requirement**: At least Python 3.10+ for development (used by hatch lint environment).

**Recommended**: Install 3.10, 3.12, and 3.13 as minimum for effective testing.

## Step 3: Install Build Dependencies

The project requires several build-time dependencies for compiling native extensions.

### Read Requirements from Project Files

The project defines build requirements in multiple locations:

**pyproject.toml** (Build system requirements):
```toml
[build-system]
requires = [
    "setuptools_scm[toml]>=4",
    "cython",
    "cmake>=3.24.2,<3.28",
    "setuptools-rust<2",
    "patchelf>=0.17.0.0; sys_platform == 'linux'",
]
```

**Runtime dependencies** (from pyproject.toml):
- `bytecode` (version-specific)
- `envier~=0.6.1`
- `opentelemetry-api>=1,<2`
- `wrapt>=1,<3`

### Install Development Dependencies

```bash
# Navigate to project directory
cd /path/to/dd-trace-py

# Install uv if not already installed (required by scripts/run-tests)
pip install uv

# Install build dependencies
pip install -U \
    setuptools_scm \
    cython \
    cmake \
    setuptools-rust \
    patchelf  # Linux only

# Install test/dev dependencies
pip install -U \
    pytest \
    pytest-mock \
    pytest-cov \
    pytest-asyncio \
    hypothesis \
    mock \
    opentracing \
    requests \
    pycryptodome \
    Werkzeug \
    flask \
    fastapi \
    gunicorn \
    gevent \
    riot \
    envier \
    wrapt \
    bytecode \
    hatch

# Or use hatch to manage environments automatically
pip install hatch
```

## Step 4: Build Native Extensions

dd-trace-py includes several native extensions (Cython, C++, Rust) that need compilation.

```bash
# Build all native extensions in-place
python setup.py build_ext --inplace

# Or use faster build mode for development (skips some optimizations)
DD_FAST_BUILD=1 python setup.py build_ext --inplace

# Verify build succeeded
python -c "import ddtrace; print(ddtrace.__version__)"
```

**Build Components:**
- **Cython extensions**: `_encoding.pyx`, `_rand.pyx`, `_tagset.pyx`, etc.
- **C/C++ extensions**: IAST taint tracking, profiling collectors
- **Rust extensions**: Native performance-critical code in `src/native/`

**Build modes** (via `DD_COMPILE_MODE`):
- `Release` - Optimized, no debug symbols (Windows default)
- `RelWithDebInfo` - Optimized with debug symbols (Linux/macOS default)
- `Debug` - No optimization, full debug symbols
- `MinSizeRel` - Size-optimized

## Step 5: Set Up Docker Services

Some test suites require external services (Redis, PostgreSQL, Testagent, etc.).

```bash
# Start all services in background
docker-compose up -d

# Verify services are running
docker-compose ps

# View service logs
docker-compose logs testagent
docker-compose logs redis

# Stop services when done
docker-compose down
```

**Available services** (from docker-compose.yml):
- `testagent` - Datadog test agent for trace validation
- `redis` - For Redis integration tests
- `postgres` - For PostgreSQL integration tests
- `mongodb` - For MongoDB integration tests
- And more...

## Step 6: Verify Installation

Run quick validation checks:

```bash
# 1. Check Python environment
python --version
python -c "import ddtrace; print(f'ddtrace {ddtrace.__version__}')"

# 2. Check hatch is working
hatch --version
hatch env show

# 3. Check riot is working
riot --version
riot list

# 4. Run a simple test suite
scripts/run-tests --list ddtrace/settings/config.py

# 5. Run lint checks
hatch run lint:style --help
```

## Step 7: Run Your First Tests

Validate everything works by running a small test suite:

```bash
# List available test suites for a file
scripts/run-tests --list ddtrace/_trace/tracer.py

# Run a quick test suite (use the run-tests skill for this)
scripts/run-tests ddtrace/settings/config.py

# Or run specific tests directly
hatch run tests:test tests/internal/test_settings.py -v
```

## Common Setup Issues

### Issue: CMake version too old

**Solution**: Install CMake >= 3.24.2
```bash
# macOS
brew install cmake

# Linux (via pip)
pip install cmake>=3.24.2

# Or download from: https://cmake.org/download/
```

### Issue: Cython compilation fails

**Solution**: Ensure you have C compiler installed
```bash
# Ubuntu/Debian
sudo apt-get install build-essential

# macOS
xcode-select --install

# Then reinstall Cython
pip install --force-reinstall cython
```

### Issue: Rust compilation fails

**Solution**: Install Rust toolchain
```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source $HOME/.cargo/env
rustc --version
```

### Issue: Docker services won't start

**Solution**: Check Docker daemon is running
```bash
# Start Docker daemon (varies by OS)
# macOS: Open Docker Desktop
# Linux: sudo systemctl start docker

# Clean up old containers
docker-compose down
docker-compose up -d
```

### Issue: pyenv can't find Python versions

**Solution**: May need build dependencies
```bash
# Ubuntu/Debian
sudo apt-get install -y \
    build-essential \
    libssl-dev \
    zlib1g-dev \
    libbz2-dev \
    libreadline-dev \
    libsqlite3-dev \
    wget \
    curl \
    llvm \
    libncurses5-dev \
    libncursesw5-dev \
    xz-utils \
    tk-dev \
    libffi-dev \
    liblzma-dev \
    python3-openssl \
    git

# macOS
brew install openssl readline sqlite3 xz zlib
```

### Issue: Permission denied on scripts

**Solution**: Make scripts executable
```bash
chmod +x scripts/run-tests
chmod +x scripts/ddtest
```

## Environment Variables

Key environment variables for development:

```bash
# Fast build mode (skips optimizations, faster iteration)
export DD_FAST_BUILD=1

# Build mode (Debug, Release, RelWithDebInfo, MinSizeRel)
export DD_COMPILE_MODE=RelWithDebInfo

# Use sccache for faster rebuilds
export DD_USE_SCCACHE=1

# Enable profiling native tests
export DD_PROFILING_NATIVE_TESTS=1

# IAST development
export DD_IAST_ENABLED=true

# Python unbuffered output (useful for debugging)
export PYTHONUNBUFFERED=1
```

## Next Steps

Once your environment is set up:

1. **Read contributing docs**: [docs/contributing.rst](../../docs/contributing.rst)
2. **Understand the architecture**: [docs/contributing-design.rst](../../docs/contributing-design.rst)
3. **Run tests with the run-tests skill**: Use the `run-tests` skill for validation
4. **Format code with lint skill**: Use the `lint` skill before committing
5. **Make your first change**: Pick an issue and submit a PR!

## Quick Reference

```bash
# Common development commands
scripts/run-tests                          # Run tests for changed files
scripts/run-tests --list <file>            # Discover test suites
hatch run lint:fmt -- <file>               # Format code
hatch run lint:checks                      # Run all lint checks
docker-compose up -d                       # Start services
docker-compose down                        # Stop services
python setup.py build_ext --inplace        # Rebuild extensions

# Environment managers
uv python install 3.13                     # Install Python (uv)
pyenv install 3.13.9                       # Install Python (pyenv)
pyenv local 3.13.9                         # Set project Python (pyenv)
```

## Platform-Specific Notes

### macOS (Apple Silicon)

Some native extensions may need architecture-specific builds:
```bash
# Ensure you're building for arm64
python -c "import platform; print(platform.machine())"  # Should show 'arm64'

# If having issues, try installing Rosetta 2
softwareupdate --install-rosetta
```

### Windows

Development on Windows has some limitations:
- Use WSL2 (Windows Subsystem for Linux) for best compatibility
- Some native tests may not run on Windows
- Docker Desktop for Windows required for services

### Linux

Most straightforward platform for development:
- Ensure you have `build-essential` installed
- `patchelf` is required for Linux builds
- Some tests may require root privileges (use with caution)

## Additional Resources

- **[docs/contributing.rst](../../docs/contributing.rst)** - Main contributing guide
- **[docs/contributing-testing.rst](../../docs/contributing-testing.rst)** - Testing setup details
- **pyenv documentation**: https://github.com/pyenv/pyenv
- **uv documentation**: https://docs.astral.sh/uv/
- **Docker documentation**: https://docs.docker.com/
- **CMake documentation**: https://cmake.org/documentation/
