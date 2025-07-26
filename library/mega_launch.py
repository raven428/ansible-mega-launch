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

from __future__ import absolute_import

__metaclass__ = type

ANSIBLE_METADATA = {
  'metadata_version': '1.1',
  'status': ['preview'],
  'supported_by': 'community'
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
  log_expression:
    description: expression for waiting in log
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

import os, re, json, time, psutil, datetime, syslog
from ansible.module_utils._text import to_native
from ansible.module_utils.basic import AnsibleModule
from ansible.module_utils.facts.system.chroot import is_chroot
from ansible.module_utils.service import sysv_exists, sysv_is_enabled, fail_if_missing


def is_running_service(service_status):
  return service_status['ActiveState'] in set(['active', 'activating'])


def is_deactivating_service(service_status):
  return service_status['ActiveState'] in set(['deactivating'])


def request_was_ignored(out):
  return '=' not in out and ('ignoring request' in out or 'ignoring command' in out)


def parse_systemctl_show(lines):
  parsed = {}
  multival = []
  k = None
  for line in lines:
    if k is None:
      if '=' in line:
        k, v = line.split('=', 1)
        if k.startswith('Exec') and v.lstrip().startswith('{'):
          if not v.rstrip().endswith('}'):
            multival.append(v)
            continue
        parsed[k] = v.strip()
        k = None
      else:
        multival.append(line)
        if line.rstrip().endswith('}'):
          parsed[k] = '\n'.join(multival).strip()
          multival = []
          k = None
  return parsed


def main():
  module = AnsibleModule(
    argument_spec=dict(
      name=dict(
        type='str',
        default=None,
        required=True,
        aliases=[
          'unit',
          'service',
          'service_name',
          'service-name',
        ],
      ),
      wait_timeout=dict(
        type='int',
        default=77,
        required=False,
        aliases=['wait-timeout'],
      ),
      max_rescues=dict(
        type='int',
        default=3,
        required=False,
        aliases=['max-retries'],
      ),
      rescue_delay=dict(
        type='int',
        default=3,
        required=False,
        aliases=['wait-delay'],
      ),
      retry_delay=dict(
        type='int',
        default=1,
        required=False,
        aliases=['wait-delay'],
      ),
      port_list=dict(
        type='list',
        default=[],
        required=False,
        aliases=['port-list'],
      ),
      log_expression=dict(
        type='str',
        default='',
        required=False,
        aliases=['log-expression'],
      ),
      required_checks=dict(
        type='int',
        default=2,
        required=False,
        aliases=['required-checks'],
      ),
      epoch=dict(
        type='str',
        default=None,
        required=False,
      ),
      scope=dict(
        type='str',
        default='system',
        choices=[
          'user',
          'global',
          'system',
        ],
      ),
    ),
    supports_check_mode=True,
  )
  unit = module.params['name']
  wait_timeout = module.params['wait_timeout']
  if unit is not None:
    for globpattern in (r"*", r"?", r"["):
      if globpattern in unit:
        module.fail_json(
          msg=
          "This module does not currently support using glob patterns, found '%s' in service name: %s"
          % (globpattern, unit)
        )
  systemctl = module.get_bin_path('systemctl', True)
  journalctl = module.get_bin_path('journalctl', True)
  if os.getenv('XDG_RUNTIME_DIR') is None:
    os.environ['XDG_RUNTIME_DIR'] = '/run/user/%s' % os.geteuid()
  if module.params['scope'] != 'system':
    systemctl += " --%s" % module.params['scope']
  rc = 0
  out = err = ''
  result = dict(
    changed=False,
    passed_checks=0,
    port_list=[],
    matched_lines=[],
  )
  if unit:
    found = False
    is_initd = sysv_exists(unit)
    is_systemd = False
    (rc, out, err) = module.run_command("%s show '%s'" % (systemctl, unit))
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
            msg="Error loading unit file '%s': %s" %
            (unit, result['status']['LoadError'])
          )
    elif err and rc == 1 and 'Failed to parse bus message' in err:
      result['status'] = parse_systemctl_show(to_native(out).split('\n'))
      unit_base, sep, suffix = unit.partition('@')
      unit_search = "%s%s" % (unit_base, sep)
      (rc, out,
       err) = module.run_command("%s list-unit-files '%s*'" % (systemctl, unit_search))
      is_systemd = unit_search in out
      (rc, out, err) = module.run_command("%s is-active '%s'" % (systemctl, unit))
      result['status']['ActiveState'] = out.rstrip('\n')
    else:
      valid_enabled_states = [
        "enabled", "enabled-runtime", "linked", "linked-runtime", "masked",
        "masked-runtime", "static", "indirect", "disabled", "generated", "transient"
      ]
      (rc, out, err) = module.run_command("%s is-enabled '%s'" % (systemctl, unit))
      if out.strip() in valid_enabled_states:
        is_systemd = True
      else:
        (rc, out, err) = module.run_command("%s list-unit-files '%s'" % (systemctl, unit))
        if rc == 0:
          is_systemd = True
        else:
          module.run_command(systemctl, check_rc=True)
    found = is_systemd or is_initd
    if is_initd and not is_systemd:
      module.warn(
        'The service (%s) is actually an init script but the system is managed by systemd'
        % unit
      )
  fail_if_missing(module, found, unit, msg="host")
  if 'ActiveState' in result['status']:
    if result['status']['ActiveState'] not in set(['active', 'activating']):
      result['changed'] = True
      if not module.check_mode:
        current_retry = 0
        parser = re.compile(module.params['log_expression'])
        epoch = module.params['epoch']
        syslog.openlog(
          f'mega-launch-{unit}{"" if epoch is None else f"-{epoch}"}', 0,
          getattr(syslog, 'LOG_USER', syslog.LOG_USER)
        )
        while ((result['passed_checks'] < module.params['required_checks'])
               and (current_retry < module.params['max_rescues'])):
          current_retry += 1
          result['passed_checks'] = 0
          syslog.syslog(
            syslog.LOG_INFO, 'retry [%d/%d] start [%s] service' % (
              current_retry,
              module.params['max_rescues'],
              unit,
            )
          )
          service_start_time = time.time() - 1
          (rc, out, err) = module.run_command("%s start '%s'" % (systemctl, unit))
          if rc != 0:
            module.fail_json(msg="Unable to start service %s: %s" % (unit, err))
          check_epoch = time.time()
          while ((result['passed_checks'] < module.params['required_checks'])
                 and (time.time() - check_epoch < wait_timeout)):
            (rc, out, err) = module.run_command("%s show '%s'" % (systemctl, unit))
            if rc != 0:
              module.fail_json(msg="Unable to check service %s: %s" % (unit, err))
            result['status'] = parse_systemctl_show(to_native(out).split('\n'))
            result['passed_checks'] = 0
            result['port_list'] = set(
              laddr.port for laddr in [
                conn.laddr
                for conn in psutil.Process(int(result['status']['MainPID'])).connections()
                if conn.status == psutil.CONN_LISTEN
              ]
            )
            result['passed_checks'] += int(
              set(module.params['port_list']).issubset(result['port_list'])
            )
            (rc, out, err) = module.run_command(
              "%s -t '%s' -S '@%0.3f' -o short-iso" % (
                journalctl,
                unit,
                service_start_time,
              )
            )
            if rc != 0:
              module.fail_json(msg="Unable journalctl -t '%s': %s" % (unit, err))
            log_exp_matched = False
            if out:
              for line in to_native(out).split('\n'):
                if parser.match(line):
                  result['matched_lines'].append(line)
                  log_exp_matched = True
                  break
            result['passed_checks'] += int(log_exp_matched)
            syslog.syslog(
              syslog.LOG_INFO,
              f'remain [{wait_timeout - time.time() + check_epoch:.2f}] seconds ['
              f'{result["passed_checks"]}/{module.params["required_checks"]}] checks'
            )
            time.sleep(module.params['retry_delay'])
          if result['passed_checks'] < module.params['required_checks']:
            (rc, out, err) = module.run_command("%s stop '%s'" % (systemctl, unit))
            if rc != 0:
              module.fail_json(msg="Unable to stop service %s: %s" % (unit, err))
          syslog.syslog(
            syslog.LOG_INFO,
            f'not enough [{result["passed_checks"]}/{module.params["required_checks"]}]'
            f' checks, [{unit}] stopped'
          )
          time.sleep(module.params['rescue_delay'])
        if result['passed_checks'] < module.params['required_checks']:
          result['msg'] = (
            f'Passed checks [{result["passed_checks"]}] less '
            f'than [{module.params["required_checks"]}] required checks'
          )
          module.fail_json(**result)
  elif is_chroot(module) or os.environ.get('SYSTEMD_OFFLINE') == '1':
    module.warn(
      "Target is a chroot or systemd is offline. This can lead to false positives or prevent the init system tools from working."
    )
  else:
    module.fail_json(msg="Service is in unknown state", status=result['status'])

  module.exit_json(**result)


if __name__ == '__main__':
  main()
