MACOS_CACHE_PATH=".macos-wheel-cache/cp312-v1.zip"
MACOS_CACHE_SYMBOLS=""

macos_wheel_cache_mode() {
  if [[ "${ARCH_TAG:-}" != "amd64" || "${UV_PYTHON:-}" != "3.12" ||
    "${CI_PIPELINE_SOURCE:-}" != "push" || "${CI_DEFAULT_BRANCH:-}" != "main" ||
    -n "${CI_COMMIT_TAG:-}" || "${UNPIN_DEPENDENCIES:-false}" == "true" ]] ||
    [[ "$(uname -s)" != "Darwin" ]]; then
    return 0
  fi
  if [[ "${CI_COMMIT_BRANCH:-}" == "main" && "${CI_COMMIT_REF_PROTECTED:-}" == "true" ]]; then
    echo publish
  elif [[ "${CI_COMMIT_BRANCH:-}" == macos-cache-pilot/* ]]; then
    echo read
  fi
}

restore_macos_wheel_cache() {
  section_start "restore_macos_wheel_cache" "Restoring macOS wheel cache"
  local started=${SECONDS} download="${WORK_DIR}/macos-cache.zip" status
  if [[ -z "${CI_JOB_TOKEN:-}" || -z "${CI_API_V4_URL:-}" || -z "${CI_PROJECT_ID:-}" || -z "${CI_JOB_NAME:-}" ]]; then
    echo "macOS wheel cache: skipped because artifact API parameters are unavailable"
  # The raw file endpoint streams the artifact. Keep the header on the GitLab host.
  elif status=$(curl --disable --proto '=https' \
    --fail --silent --show-error --retry 5 --retry-delay 2 --retry-connrefused \
    --connect-timeout 5 --max-time 20 --retry-max-time 20 --get \
    --header "JOB-TOKEN: ${CI_JOB_TOKEN}" \
    --data-urlencode "job=${CI_JOB_NAME}" \
    --output "${download}" --write-out '%{http_code}' \
    "${CI_API_V4_URL}/projects/${CI_PROJECT_ID}/jobs/artifacts/main/raw/${MACOS_CACHE_PATH}") &&
    [[ "${status}" == "200" ]]; then
    local staging="${WORK_DIR}/macos-wheel-cache"
    if ext_cache_python scripts/macos_wheel_cache.py restore "${download}" "${staging}"; then
      MACOS_CACHE_SYMBOLS="${staging}/symbols"
    else
      status=$?
      if [[ "${status}" == 3 || -e "${staging}/installing" ]]; then
        echo "macOS wheel cache: restore could not safely roll back" >&2
        return 3
      fi
    fi
  else
    echo "macOS wheel cache: main artifact unavailable (HTTP ${status:-unknown}); building from source"
  fi
  echo "macOS wheel cache lookup: $((SECONDS - started))s"
  section_end "restore_macos_wheel_cache"
}

stage_macos_wheel_cache() {
  local symbols_zip="${DEBUG_WHEEL_DIR}/$(basename "${BUILT_WHEEL_FILE}" .whl)-debug-symbols.zip"
  local archive="${WORK_DIR}/macos-cache.zip"
  if ! ext_cache_python scripts/macos_wheel_cache.py save "${archive}" "${symbols_zip}"; then
    rm -f "${archive}"
    echo "macOS wheel cache: skip publishing this build"
  fi
}

publish_macos_wheel_cache() {
  local archive="${WORK_DIR}/macos-cache.zip" output="${PROJECT_DIR}/${MACOS_CACHE_PATH}"
  if [[ -f "${archive}" ]]; then
    if mkdir -p "$(dirname "${output}")" && mv "${archive}" "${output}"; then
      echo "macOS wheel cache: staged $(wc -c < "${output}") bytes for upload"
    else
      echo "macOS wheel cache: unable to stage the archive for upload"
    fi
  fi
}
