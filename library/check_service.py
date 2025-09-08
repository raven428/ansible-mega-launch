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
module: check_service
short_description: check service ports and log
description:
  - check provided ports open
  - check log records for expression
version_added: "0.0.1"
options:
  service_name:
    description: systemd service name
    required: true
    type: str
  port_list:
    description: list of ports
    required: false
    default: []
    type: list
    contains:
      description: port number
      type: int
  log_epoch:
    description: time for --since in journalctl
    required: false
    default: current epoch
    type: int
  log_regexp:
    description: expression for log
    required: false
    default: ""
    type: str

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
    ports_list:
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
matched_lines:
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


def main() -> None:
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
      'main_pid': {
        'type': 'int',
        'default': None,
        'required': False,
        'aliases': ['main-pid'],
      },
      'port_list': {
        'type': 'list',
        'default': None,
        'elements': 'int',
        'required': False,
        'aliases': ['port-list', 'ports', 'port_set', 'port-set'],
      },
      'log_epoch': {
        'type': 'int',
        'default': int(time.time()),
        'required': False,
        'aliases': ['log-epoch'],
      },
      'log_regexp': {
        'type': 'str',
        'default': None,
        'required': False,
        'aliases': ['log-expression'],
      },
    },
    supports_check_mode=True,
  )
  unit = module.params['name']
  for globpattern in (r'*', r'?', r'['):
    if globpattern in unit:
      module.fail_json(
        msg=(
          'This module does not currently support using glob patterns, found '
          f'[{globpattern}] in [{unit}] service'
        ),
      )
  result: dict = {
    'changed': False,
    'passed_checks': 0,
    'ports': set(),
    'matched_lines': [],
  }
  if module.params.get('port_list'):
    result['passed_checks'] = calc_ports(
      main_pid=int(module.params.get('main_pid', 0)),
      result_ports=result['ports'],
      module_ports=set(module.params.get('port_list', [])),
    )
  if module.params.get('log_regexp'):
    journalctl = module.get_bin_path('journalctl', required=True)
    if os.getenv('XDG_RUNTIME_DIR') is None:
      os.environ['XDG_RUNTIME_DIR'] = f'/run/user/{os.geteuid()}'
    command = "{} -t '{}' -S '{}' -o short".format(
      journalctl,
      unit,
      time.strftime(
        '%F %H:%M:%S',
        time.localtime(module.params['log_epoch'] - 1),
      ),
    )
    (rc, out, err) = module.run_command(command)
    if rc != 0:
      module.fail_json(msg=f"Unable journalctl -t '{unit}': {err}")
    log_regexp_matched = False
    if out:
      parser = re.compile(module.params['log_regexp'])
      for line in to_native(out).split('\n'):
        if parser.match(line):
          result['matched_lines'].append(line)
          log_regexp_matched = True
    result['passed_checks'] += int(log_regexp_matched)
  module.exit_json(**result)


if __name__ == '__main__':
  main()
