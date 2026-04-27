#!/usr/bin/env bash
set -ueo pipefail
umask 0022
MY_BIN="$(realpath "$0")"
MY_PATH="$(dirname "${MY_BIN}")"
cd "${MY_PATH}/../.."
# shellcheck disable=1091
source "${MY_PATH}/../prepare.sh"
sce='default'
LOG_PATH="/tmp/molecule-$(/usr/bin/env date '+%Y%m%d%H%M%S.%3N')"
printf "\n\n\nmolecule [create] action\n"
# shellcheck disable=2154
ANSIBLE_LOG_PATH="${LOG_PATH}-0create" "${_appimage_bin}" molecule -v create -s "${sce}"
if [[ -n "${MEGA_VAR_REF:-}" ]]; then
  echo "MEGA_VAR_REF=${MEGA_VAR_REF}"
  /usr/bin/env rm -rf "${ANSIBLE_ROLES_PATH}/raven428.mega_var"
  /usr/bin/env git clone --branch "${MEGA_VAR_REF}" \
    'https://github.com/raven428/ansible-mega-var.git' \
    "${ANSIBLE_ROLES_PATH}/raven428.mega_var"
else
  echo 'MEGA_VAR_REF input missed'
fi
if [[ -n "${MEGA_SERVICE_REF:-}" ]]; then
  echo "MEGA_SERVICE_REF=${MEGA_SERVICE_REF}"
  /usr/bin/env rm -rf "${ANSIBLE_ROLES_PATH}/raven428.mega_service"
  /usr/bin/env git clone --branch "${MEGA_SERVICE_REF}" \
    'https://github.com/raven428/ansible-mega-service.git' \
    "${ANSIBLE_ROLES_PATH}/raven428.mega_service"
else
  echo 'MEGA_SERVICE_REF input missed'
fi
for prop_mode in start stop mod_start start_non_fail mod_non_fail start_run_fail \
  mod_run_fail mod_zero_fail start_zero_fail; do
  printf "\n\n\nmolecule [%s] mode [converge] check\n" "${prop_mode}"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-1check" \
    "${_appimage_bin}" molecule -v converge -s "${sce}" -- --check \
    --extra-vars prop_mode="${prop_mode}"

  printf "\n\n\nmolecule [converge] action\n"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-2converge" \
    "${_appimage_bin}" molecule -v converge -s "${sce}" -- \
    --extra-vars prop_mode="${prop_mode}"

  printf "\n\n\nmolecule [idempotence] action\n"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-3idempotence" \
    "${_appimage_bin}" molecule -v idempotence -s "${sce}" -- \
    --extra-vars prop_mode="${prop_mode}"

  printf "\n\n\nmolecule [converge] check\n"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-4converge-check" \
    "${_appimage_bin}" molecule -v converge -s "${sce}" -- --check \
    --extra-vars prop_mode="${prop_mode}"

  [[ "${prop_mode}" == 'start' || "${prop_mode}" == 'stop' ||
    "${prop_mode}" == 'mod_start' ]] && {
    printf "\n\n\nmolecule [idempotence] check\n"
    ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-5idempotence-check" \
      "${_appimage_bin}" molecule -v idempotence -s "${sce}" -- --check \
      --extra-vars prop_mode="${prop_mode}"
  }

  printf "\n\n\nmolecule [verify] action\n"
  ANSIBLE_PROP_MODE="${prop_mode}" \
    ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-6verify" \
    "${_appimage_bin}" molecule -v verify -s "${sce}"
done

for prop_mode in stop_proc stop_port; do
  printf "\n\n\nmolecule [%s] mode [converge]\n" "${prop_mode}"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-1converge" \
    "${_appimage_bin}" molecule -v converge -s "${sce}" -- \
    --extra-vars prop_mode="${prop_mode}"

  printf "\n\n\nmolecule [verify] action\n"
  ANSIBLE_PROP_MODE="${prop_mode}" \
    ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-2verify" \
    "${_appimage_bin}" molecule -v verify -s "${sce}"
done
