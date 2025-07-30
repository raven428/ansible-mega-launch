#!/usr/bin/env bash
set -ueo pipefail
umask 0022
MY_BIN="$(readlink -f "$0")"
MY_PATH="$(dirname "${MY_BIN}")"
cd "${MY_PATH}/../.."
# shellcheck disable=1091
source "${MY_PATH}/../prepare.sh"
sce='default'
LOG_PATH="/tmp/molecule-$(/usr/bin/env date '+%Y%m%d%H%M%S.%3N')"
printf "\n\n\nmolecule [create] action\n"
ANSIBLE_LOG_PATH="${LOG_PATH}-0create" \
  ansible-docker.sh molecule -v create -s "${sce}"

for prop_mode in start stop mod_start start_fail mod_fail start_run_fail mod_run_fail; do
  printf "\n\n\nmolecule [%s] mode [converge] check\n" "${prop_mode}"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-1check" \
    ansible-docker.sh molecule -v converge -s "${sce}" -- --check \
    --extra-vars prop_mode="${prop_mode}"

  printf "\n\n\nmolecule [converge] action\n"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-2converge" \
    ansible-docker.sh molecule -v converge -s "${sce}" -- \
    --extra-vars prop_mode="${prop_mode}"

  printf "\n\n\nmolecule [idempotence] action\n"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-3idempotence" \
    ansible-docker.sh molecule -v idempotence -s "${sce}" -- \
    --extra-vars prop_mode="${prop_mode}"

  printf "\n\n\nmolecule [converge] check\n"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-4converge-check" \
    ansible-docker.sh molecule -v converge -s "${sce}" -- --check \
    --extra-vars prop_mode="${prop_mode}"

  [[ "${prop_mode}" == 'start' || "${prop_mode}" == 'stop' ||
    "${prop_mode}" == 'mod_start' ]] && {
    printf "\n\n\nmolecule [idempotence] check\n"
    ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-5idempotence-check" \
      ansible-docker.sh molecule -v idempotence -s "${sce}" -- --check \
      --extra-vars prop_mode="${prop_mode}"
  }
done

for prop_mode in stop_proc stop_port; do
  printf "\n\n\nmolecule [%s] mode [converge]\n" "${prop_mode}"
  ANSIBLE_LOG_PATH="${LOG_PATH}-${prop_mode}-1converge" \
    ansible-docker.sh molecule -v converge -s "${sce}" -- \
    --extra-vars prop_mode="${prop_mode}"
done
