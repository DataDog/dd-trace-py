#!/usr/bin/env bash
set -euo pipefail

mode="${1:?expected seed, control, warm or invalidate}"
case "${mode}" in
  seed|control|warm|invalidate) ;;
  *) echo "Unknown experiment mode: ${mode}" >&2; exit 2 ;;
esac

cd "${CI_PROJECT_DIR:?}"
if [[ -e .ext_cache || -e build ]]; then
  echo "Experiment requires a clean checkout" >&2
  exit 1
fi

candidate_sha=$(git rev-parse HEAD)
base_sha=$(git rev-parse 'HEAD^')
seed="${CI_PROJECT_DIR}/debugwheelhouse/extension-cache-seed"
output="${CI_PROJECT_DIR}/debugwheelhouse/extension-cache-${mode}"
mkdir -p "${output}"
export _DD_DEBUG_EXT=1 _DD_DEBUG_EXT_FILE="${output}/timings.log"

# shellcheck source=build-wheel-helpers.sh
source .gitlab/scripts/build-wheel-helpers.sh
SECONDS=0
setup_env
export DEBUG_WHEEL_DIR="${output}"

if [[ "${mode}" == seed ]]; then
  checkout="${WORK_DIR}/base"
  cleanup_seed() {
    cd "${CI_PROJECT_DIR}"
    if [[ -d "${checkout}" ]]; then
      git worktree remove --force "${checkout}"
    fi
    rm -rf "${WORK_DIR}"
  }
  trap cleanup_seed EXIT
  git worktree add --detach "${checkout}" "${base_sha}"
  cd "${checkout}"
  export PROJECT_DIR="${checkout}"
  printf '%s\n' "${base_sha}" > "${output}/source.sha"
else
  printf '\n# Python-only edit for the cross-commit cache experiment.\n' >> ddtrace/trace/__init__.py
  printf '%s\n' "${candidate_sha}" > "${output}/source.sha"
fi

if [[ "${mode}" == invalidate ]]; then
  printf '\n\ndef _cache_invalidation_probe():\n    return 1\n' >> ddtrace/internal/_tagset.pyx
fi

if ! command -v sccache &> /dev/null; then
  brew install sccache
fi
setup_python
setup_rust
printf 'Mode: %s\nBuild host: %s\nSource revision: %s\n' "${mode}" "$(hostname)" "$(cat "${output}/source.sha")"

{
  uname -m
  sw_vers -buildVersion
  rustc -vV
  cargo --version
  clang --version
  xcrun --sdk macosx --show-sdk-version
  uv --version
  uv run --no-project python -c 'import sys, sysconfig; print(sys.version); print(sysconfig.get_config_var("SOABI"))'
  shasum -a 256 setup.py pyproject.toml
  for flag in ARCHFLAGS CC CXX CFLAGS CPPFLAGS CXXFLAGS LDFLAGS SDKROOT MACOSX_DEPLOYMENT_TARGET \
    CMAKE_ARGS DD_COMPILE_MODE DD_CARGO_ARGS DD_SERVERLESS_BUILD DD_USE_SCCACHE; do
    printf '%s=%s\n' "${flag}" "${!flag-}"
  done
} > "${output}/build-environment"

build_started=${SECONDS}
if [[ "${mode}" == warm || "${mode}" == invalidate ]]; then
  printf '%s\n' "${base_sha}" | cmp - "${seed}/source.sha"
  diff -u "${seed}/build-environment" "${output}/build-environment"

  seed_symbols=("${seed}"/*-debug-symbols.zip)
  if [[ ${#seed_symbols[@]} != 1 || ! -s "${seed_symbols[0]}" ]]; then
    echo "Expected one seed debug-symbols archive" >&2
    exit 1
  fi
  symbols_dir="${WORK_DIR}/seed-symbols"
  unzip -q "${seed_symbols[0]}" -d "${symbols_dir}"
  tar -xzf "${seed}/ext-cache.tar.gz"
  expected=$(cat "${seed}/extension-count")
  if [[ "${mode}" == invalidate ]]; then
    expected=$((expected - 3))
  fi

  restore_log="${output}/restore.log"
  ext_cache_python scripts/restore_macos_cache_experiment.py "${symbols_dir}" "${mode}" 2>&1 | tee "${restore_log}"
  restored=$(grep -cE 'Successfully copied .*\.so to ' "${restore_log}" || true)
  if [[ "${restored}" != "${expected}" || "${restored}" -eq 0 ]]; then
    echo "Expected ${expected} cached extensions, restored ${restored}" >&2
    exit 1
  fi
  echo "Restored ${restored} compiled extensions"
  if [[ "${mode}" == invalidate ]]; then
    grep -F 'No cached files found for _tagset.' "${restore_log}"
    grep -F 'Missing dSYM for ddtrace/internal/_encoding.' "${restore_log}"
    grep -F 'dSYM UUID mismatch for ddtrace/internal/_threads.' "${restore_log}"
  fi
else
  restore_ext_cache
fi

build_wheel
echo "${mode} restore plus build: $((SECONDS - build_started))s"
grep -F 'INFO: sccache server ready' "${BUILD_LOG}"
if [[ "${mode}" == invalidate ]]; then
  for ext in _tagset _encoding _threads; do
    grep -F "building 'ddtrace.internal.${ext}' extension" "${BUILD_LOG}"
  done
fi

if [[ "${mode}" == seed ]]; then
  export EXT_CACHE_POLICY=pull-push
  save_ext_cache
  find .ext_cache -type f -name '*.so' | wc -l | tr -d ' ' > "${output}/extension-count"
  test "$(cat "${output}/extension-count")" -gt 3
  tar -czf "${output}/ext-cache.tar.gz" .ext_cache
fi

package_started=${SECONDS}
if [[ "${mode}" == warm || "${mode}" == invalidate ]]; then
  repair_log="${output}/repair.log"
  repair_wheel --reuse-dsym-from "${symbols_dir}" 2>&1 | tee "${repair_log}"
  reused=$(grep -F 'Reused UUID-matched .dSYM bundle for ' "${repair_log}" | grep -cFv '/libdd_wrapper.' || true)
  if [[ "${reused}" != "${restored}" ]]; then
    echo "Expected ${restored} reused extension dSYM bundles, found ${reused}" >&2
    exit 1
  fi
else
  repair_wheel
fi
finalize
echo "${mode} wheel packaging: $((SECONDS - package_started))s"
test_wheel
if [[ "${mode}" == invalidate ]]; then
  "${VENV_PATH}/bin/python" -c 'from ddtrace.internal._tagset import _cache_invalidation_probe; assert _cache_invalidation_probe() == 1'
fi
sccache --show-stats
echo "${mode} total script runtime: ${SECONDS}s"
