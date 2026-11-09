#!/usr/bin/env bash
# Source this file so Rust build scripts inherit the selected Xcode toolchain.
libclang_toolchain_bin=$(dirname "$(xcrun --find clang)")
export LIBCLANG_PATH="${libclang_toolchain_bin}/../lib"
export SDKROOT="$(xcrun --show-sdk-path)"
