from __future__ import annotations

# Copyright © 2022 Dmitrii Sukhodoev <raven428@gmail.com>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

ANSIBLE_METADATA = {
  'metadata_version': '1.1',
  'status': ['preview'],
  'supported_by': 'community',
}

DOCUMENTATION = r'''
---
module: mega_launch
short_description: start service with checks
description:
  - start desired service
  - wait timeout provided ports open
  - wait timeout log records for expression
  - try to restart service in case of failure
version_added: "0.0.1"
options:
  service_name:
    description: systemd service name
    required: true
    type: str
  wait_timeout:
    description: wait checks timeout after start
    required: true
    default: "77"
    type: str
  max_rescues:
    description: max restart retries
    required: false
    default: 5
    type: int
  rescue_delay:
    description: delay between restarts
    required: false
    default: 3
    type: int
  retry_delay:
    description: delay between checks
    required: false
    default: 1
    type: int
  port_list:
    description: list of ports
    required: false
    default: []
    type: list
    contains:
      description: port number
      type: int
  log_regexp:
    description: regular expression for waiting in log
    required: false
    default: ""
    type: str
  required_checks:
    description: number of required checks to success
    required: false
    default: 2
    type: int

author: "Dmitrii Sukhodoev <raven428@gmail.com>"
attributes:
  check_mode:
    support: full
  platform:
    platforms: debian
'''

EXAMPLES = r'''
- name: start xray service with restries
  check_service:
    service_name: "xray"
    port_list:
      - 1443
      - 10101
    log_regexp: 'regexp-pattern'
'''

RETURN = r'''
passed_checks:
  description: number of passed checks after service start
  type: int
  returned: always
  sample: 0
port_list:
  description: list of found listen ports
  type: list
  default: []
  returned: success, when need
  contains:
    description: port number
    type: int
match_lines:
  description: matched log lines
  type: list
  default: []
  returned: success, when need
  contains:
    description: log line
    type: str
'''

import os
import re
import syslog
import time

# pylint: disable=import-error
from ansible.module_utils._text import (  # type: ignore[reportMissingImports]
  to_native,  # noqa: PLC2701
)
from ansible.module_utils.basic import (  # type: ignore[reportMissingImports]
  AnsibleModule,
)
from ansible.module_utils.mega_launch import (  # type: ignore[reportMissingImports]
  calc_ports,
)
from ansible.module_utils.service import (  # type: ignore[reportMissingImports]
  fail_if_missing,
  sysv_exists,
)


class ServiceStatus:
  def __init__(
    self,
    unit: str,
    module: AnsibleModule,
    systemctl: str | None = None,
  ) -> None:
    self.unit = unit
    self.module = module
    self.systemctl = systemctl or module.get_bin_path(arg='systemctl', required=True)
    if module.params.get('scope') != 'system':
      self.systemctl += f' --{module.params["scope"]}'
    self.status: dict[str, str] | None = {}
    self.rc, out, _ = self.module.run_command(f"{self.systemctl} show '{self.unit}'")
    if self.rc != 0:
      self.status = None
    else:
      self.status = parse_systemctl_show(to_native(out).split('\n'))

  def get(self, key: str, default: str | None = None) -> str | None:
    if not self.status:
      return default
    return self.status.get(key, default)

  def __getitem__(self, key: str) -> str:
    if not self.status:
      msg = f'Service [{self.unit}] status not available'
      raise KeyError(msg)
    return self.status[key]

  def __contains__(self, key: str) -> bool:
    if not self.status:
      return False
    return key in self.status

  def __bool__(self) -> bool:
    if not self.status or not isinstance(self.status, dict):
      return False
    return self.status.get('SubState') == 'running' \
      and self.status.get('ActiveState') == 'active' \
      and int(self.status.get('MainPID') or '0') > 0


def request_was_ignored(out: str) -> bool:
  return '=' not in out and ('ignoring request' in out or 'ignoring command' in out)


def parse_systemctl_show(lines: list[str]) -> dict:
  multival: list[str] = []
  parsed = {}
  key = ''
  for line in lines:
    if key:
      continue
    if '=' in line:
      key, value = line.split('=', 1)
      if key.startswith('Exec') and value.lstrip(
      ).startswith('{') and not value.rstrip().endswith('}'):
        multival.append(value)
        continue
      parsed[key] = value.strip()
      key = ''
    else:
      multival.append(line)
      if line.rstrip().endswith('}'):
        parsed[key] = '\n'.join(multival).strip()
        multival = []
        key = ''
  return parsed


def main() -> None:  # noqa: C901,PLR0912,PLR0914,PLR0915
  module = AnsibleModule(
    argument_spec={
      'name': {
        'type': 'str',
        'default': None,
        'required': True,
        'aliases': [
          'unit',
          'service',
          'service_name',
          'service-name',
        ],
      },
      'wait_timeout': {
        'type': 'int',
        'default': 77,
        'required': False,
        'aliases': ['wait-timeout'],
      },
      'max_rescues': {
        'type': 'int',
        'default': 3,
        'required': False,
        'aliases': ['max-rescues'],
      },
      'rescue_delay': {
        'type': 'int',
        'default': 3,
        'required': False,
        'aliases': ['rescue-delay'],
      },
      'retry_delay': {
        'type': 'int',
        'default': 1,
        'required': False,
        'aliases': ['retry-delay'],
      },
      'port_list': {
        'type': 'list',
        'default': None,
        'elements': 'int',
        'required': False,
        'aliases': ['port-list', 'ports', 'port_set', 'port-set'],
      },
      'log_regexp': {
        'type': 'str',
        'default': None,
        'required': False,
        'aliases': ['log-regexp'],
      },
      'required_checks': {
        'type': 'int',
        'default': 2,
        'required': False,
        'aliases': ['required-checks'],
      },
      'epoch': {
        'type': 'str',
        'default': None,
        'required': False,
      },
      'scope': {
        'type': 'str',
        'default': 'system',
        'choices': [
          'user',
          'global',
          'system',
        ],
      },
    },
    supports_check_mode=True,
  )
  unit = module.params['name']
  wait_timeout = module.params['wait_timeout']
  if unit is not None:
    for globpattern in (r'*', r'?', r'['):
      if globpattern in unit:
        module.fail_json(
          msg='This module does not currently support using glob patterns, found '
          f'[{globpattern}] in [{unit}] service',
        )
  systemctl = module.get_bin_path(arg='systemctl', required=True)
  journalctl = module.get_bin_path(arg='journalctl', required=True)
  if os.getenv('XDG_RUNTIME_DIR') is None:
    os.environ['XDG_RUNTIME_DIR'] = f'/run/user/{os.geteuid()}'
  if module.params['scope'] != 'system':
    systemctl += f' --{module.params["scope"]}'
    journalctl += f' --{module.params["scope"]}'
  rc = 0
  out = err = ''
  result: dict = {
    'changed': False,
    'passed_checks': 0,
    'ports': set(),
    'matched_lines': [],
  }

  # systemd_service from Ansible part begin
  found = False
  if unit:
    is_initd = sysv_exists(unit)
    is_systemd = False
    (rc, out, err) = module.run_command(f"{systemctl} show '{unit}'")
    if rc == 0 and not (request_was_ignored(out) or request_was_ignored(err)):
      if out:
        result['status'] = parse_systemctl_show(to_native(out).split('\n'))
        is_systemd = (
          'LoadState' in result['status'] and result['status']['LoadState'] != 'not-found'
        )
        is_masked = (
          'LoadState' in result['status'] and result['status']['LoadState'] == 'masked'
        )
        if is_systemd and not is_masked and 'LoadError' in result['status']:
          module.fail_json(
            msg=f"Error loading unit file '{unit}': {result['status']['LoadError']}",
          )
    elif err and rc == 1 and 'Failed to parse bus message' in err:
      result['status'] = parse_systemctl_show(to_native(out).split('\n'))
      unit_base, sep, _suffix = unit.partition('@')
      unit_search = f'{unit_base}{sep}'
      (rc, out, err) = module.run_command(f"{systemctl} list-unit-files '{unit_search}*'")
      is_systemd = unit_search in out
      (rc, out, err) = module.run_command(f"{systemctl} is-active '{unit}'")
      result['status']['ActiveState'] = out.rstrip('\n')
    else:
      valid_enabled_states = [
        'enabled',
        'enabled-runtime',
        'linked',
        'linked-runtime',
        'masked',
        'masked-runtime',
        'static',
        'indirect',
        'disabled',
        'generated',
        'transient',
      ]
      (rc, out, err) = module.run_command(f"{systemctl} is-enabled '{unit}'")
      if out.strip() in valid_enabled_states:
        is_systemd = True
      else:
        (rc, out, err) = module.run_command(f"{systemctl} list-unit-files '{unit}'")
        if rc == 0:
          is_systemd = True
        else:
          module.run_command(systemctl, check_rc=True)
    found = is_systemd or is_initd
    if is_initd and not is_systemd:
      module.warn(
        f'The service ({unit}) is actually an init script but the system is managed '
        'by systemd',
      )
  fail_if_missing(module, found, unit, msg='host')
  if 'ActiveState' not in result['status']:
    module.fail_json(msg='Unknown service state', status=result['status'])
  # systemd_service from Ansible part end

  current_retry = 0
  log_regexp = module.params.get('log_regexp')
  parser = re.compile(log_regexp) if log_regexp else None
  epoch = module.params['epoch']
  syslog.openlog(
    f'mega-launch-{unit}{"" if epoch is None else f"-{epoch}"}',
    0,
    getattr(syslog, 'LOG_USER', syslog.LOG_USER),
  )
  running_before = ServiceStatus(unit, module)
  while (
    result['passed_checks'] < module.params['required_checks']
    and current_retry < module.params['max_rescues']
  ):
    current_retry += 1
    result['passed_checks'] = 0
    service_start_time = time.time() - 1
    if not module.check_mode:
      (rc, out, err) = module.run_command(f"{systemctl} start '{unit}'")
      if rc != 0:
        module.fail_json(msg=f'Unable to start service {unit}: {err}')
    running_service = ServiceStatus(unit, module)
    if not running_service:
      if module.check_mode:
        result['changed'] = True
        module.exit_json(**result)
      else:
        module.fail_json(msg=f'Service {unit} unable to start')
    syslog.syslog(
      syslog.LOG_INFO,
      f'retry [{current_retry}/{module.params["max_rescues"]}] '
      f'{"check_mode" if module.check_mode else "start"}'
      f' [{unit}] service',
    )
    check_epoch = time.time()
    while (
      result['passed_checks'] < module.params['required_checks']
      and time.time() - check_epoch < wait_timeout and running_service
    ):
      running_service = ServiceStatus(unit, module)
      result['passed_checks'] = calc_ports(
        main_pid=int(running_service.get('MainPID', '0') or '0'),
        result_ports=result['ports'],
        module_ports=set(module.params.get('port_list', [])),
      )
      (rc, out, err) = module.run_command(
        f"{journalctl} -t '{unit}' -S '@{service_start_time:0.3f}' -o short-iso",
      )
      if rc != 0:
        module.fail_json(msg=f"Unable journalctl -t '{unit}': {err}")
      log_exp_matched = False
      if out and parser:
        for line in to_native(out).split('\n'):
          if parser.match(line):
            result['matched_lines'].append(line)
            log_exp_matched = True
            break
      result['passed_checks'] += int(log_exp_matched)
      syslog.syslog(
        syslog.LOG_INFO,
        f'remain [{wait_timeout - time.time() + check_epoch:.2f}] seconds ['
        f'{result["passed_checks"]}/{module.params["required_checks"]}] checks',
      )
      time.sleep(module.params['retry_delay'])
    syslog.syslog(syslog.LOG_INFO, 'loop 2 exit')
    if result['passed_checks'] < module.params['required_checks']:
      if not module.check_mode and not running_before:
        (rc, out, err) = module.run_command(f"{systemctl} stop '{unit}'")
        if rc != 0:
          module.fail_json(msg=f'Unable to stop service {unit}: {err}')
      syslog.syslog(
        syslog.LOG_INFO,
        f'not enough [{result["passed_checks"]}/{module.params["required_checks"]}]'
        f' checks, [{unit}] '
        f'{"check_mode" if module.check_mode else "stopped"}',
      )
      time.sleep(module.params['rescue_delay'])
  if result['passed_checks'] < module.params['required_checks']:
    result['msg'] = (
      f'Passed checks [{result["passed_checks"]}] less '
      f'than [{module.params["required_checks"]}] required checks'
    )
    result['changed'] = False
    module.fail_json(**result)
  elif not running_before:
    result['changed'] = True
  syslog.closelog()
  module.exit_json(**result)


if __name__ == '__main__':
  main()
