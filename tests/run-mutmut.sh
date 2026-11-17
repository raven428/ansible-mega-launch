#!/usr/bin/env bash
# cspell:ignore appimage pythondontwritebytecode
set -ueo pipefail
umask 0022
MY_BIN="$(realpath "$0")"
MY_PATH="$(dirname "${MY_BIN}")"
cd "${MY_PATH}/.."
# shellcheck disable=1091
source "${MY_PATH}/../molecule/prepare.sh"
export PYTHONDONTWRITEBYTECODE=1
_work_dir="$(mktemp -d /tmp/mega-status-mutmut.XXXXXX)"
trap 'rm -rf "${_work_dir}"' EXIT
cp -a library tests "${_work_dir}/"
# mutmut reads config and writes mutants/ in the current directory, keep both in copy
cat >"${_work_dir}/pyproject.toml" <<'EOT'
[tool.mutmut]
source_paths = ['library/mega_status.py']
also_copy = ['library/__init__.py']
pytest_add_cli_args_test_selection = ['tests/mega_status/main_tests.py']
use_git_change_detection = false
EOT
cd "${_work_dir}"
# shellcheck disable=2154
"${_appimage_bin}" mutmut run
"${_appimage_bin}" python tests/mega_status/mutmut_gate.py "${_appimage_bin}"
