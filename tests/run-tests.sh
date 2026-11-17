#!/usr/bin/env bash
# cspell:ignore appimage cacheprovider pythondontwritebytecode
set -ueo pipefail
umask 0022
MY_BIN="$(realpath "$0")"
MY_PATH="$(dirname "${MY_BIN}")"
cd "${MY_PATH}/.."
# shellcheck disable=1091
source "${MY_PATH}/../molecule/prepare.sh"
export PYTHONDONTWRITEBYTECODE=1
_tmp_dir="$(mktemp -d /tmp/mega-status-tests.XXXXXX)"
trap 'rm -rf "${_tmp_dir}"' EXIT
# shellcheck disable=2154
"${_appimage_bin}" pytest --color=yes -vv -o cache_dir="${_tmp_dir}/cache" \
  tests/mega_status/main_tests.py
copy_run() {
  local _copy="${_tmp_dir}/${1}"
  mkdir "${_copy}" || return 90
  cp -a library tests "${_copy}/" || return 91
  if [[ -n "${2}" ]]; then
    git -C "${_copy}" apply --unidiff-zero "${2}" || return 92
  fi
  (cd "${_copy}" && "${_appimage_bin}" pytest -q -p no:cacheprovider \
    tests/mega_status/main_tests.py) >"${_copy}.log" 2>&1
}
_rc=0
copy_run baseline '' || _rc=$?
if [[ ${_rc} -ne 0 ]]; then
  cat "${_tmp_dir}/baseline.log"
  echo "baseline copy failed with [${_rc}] code" >&2
  exit 1
fi
# every point mutant must fail the tests with pytest exit code 1
_failed=0
for _patch in "${MY_PATH}"/mega_status/point-mutants/*.patch; do
  _name="$(basename "${_patch}" .patch)"
  _rc=0
  copy_run "${_name}" "${_patch}" || _rc=$?
  case "${_rc}" in
  1)
    echo "mutant [${_name}] killed"
    ;;
  0)
    echo "mutant [${_name}] survived" >&2
    _failed=1
    ;;
  *)
    cat "${_tmp_dir}/${_name}.log"
    echo "mutant [${_name}] error [${_rc}] code" >&2
    _failed=1
    ;;
  esac
done
exit "${_failed}"
