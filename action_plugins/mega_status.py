# Copyright: (c) 2018, Ansible Project
# GNU General Public License v3.0+
# see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt

from __future__ import annotations

# pylint: disable=import-error
from ansible.plugins.action import (  # type: ignore[reportMissingImports]
  ActionBase,
)
from ansible.utils.vars import (  # type: ignore[reportMissingImports]
  merge_hash,
)


class ActionModule(ActionBase):
  def _get_async_dir(self) -> str:

    # async directory based on the shell option
    async_dir = self.get_shell_option(
      'async_dir',
      default='~/.ansible_async',
    )

    return self._remote_expand_user(async_dir)

  def run(self, tmp=None, task_vars=None):  # noqa: ANN001,ANN201

    results = super().run(tmp, task_vars)

    _, new_module_args = self.validate_argument_spec(
      argument_spec={
        'jid': {
          'type': 'str',
          'required': True,
        },
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
        'mode': {
          'type': 'str',
          'choices': ['status', 'cleanup'],
          'default': 'status',
        },
        'epoch': {
          'type': 'str',
          'default': None,
        },
      },
    )

    # initialize response
    results['started'] = results['finished'] = False
    results['stdout'] = results['stderr'] = ''
    results['stdout_lines'] = results['stderr_lines'] = []

    self._display.warning('start warning from status action plugin')

    jid = new_module_args['jid']
    mode = new_module_args['mode']

    results['ansible_job_id'] = jid
    async_dir = self._get_async_dir()
    log_path = self._connection._shell.join_path(async_dir, jid)  # noqa: SLF001

    if mode == 'cleanup':
      results['erased'] = log_path
    else:
      results['results_file'] = log_path
      results['started'] = True

    new_module_args['_async_dir'] = async_dir
    results = merge_hash(
      results,
      self._execute_module(
        module_name='mega_status',
        task_vars=task_vars,
        module_args=new_module_args,
      ),
    )

    for line in results.get('warning_lines', []):
      self._display.warning(f'{line}')

    # Backwards compat shim for when started/finished were ints,
    # mostly to work with ansible.windows.async_status
    for convert in ('started', 'finished'):
      results[convert] = bool(results[convert])

    return results
