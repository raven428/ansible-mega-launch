from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

# pylint: disable=import-error
import pytest  # type: ignore[reportMissingImports]
from ansible.module_utils import basic  # type: ignore[reportMissingImports]

from library.mega_status import main

if TYPE_CHECKING:
  from collections.abc import Callable
  from pathlib import Path

JID = '418302112754.4242'
UNIT = 'gaiad'
EPOCH = '20261114T101112'
CURSOR = 's=0123abcd;i=1f;b=4567ef;m=2a;t=5f0;x=89\n'
OLD_TIME_NS = 1_000_000_000_000_000_000
# job file records in the async_wrapper format of ansible-core 2.13-2.18
START = {
  'started': 1,
  'finished': 0,
  'ansible_job_id': JID,
}
FINAL = {
  'changed': True,
  'passed_checks': 2,
  'ports': [9090, 26657],
  'matched_lines': ['committed state'],
}
RETRY_LINE = 'retry [1/2] start [gaiad] service'
EXIT_LINE = 'loop 2 exit'


@dataclass
class Journal:
  rc: int = 0
  out: str = ''
  err: str = ''
  missing: bool = False
  on_call: Callable[[], None] | None = None
  commands: list[list[str]] = field(default_factory=list)
  script: Path | None = None


class StatusRun:
  def __init__(
    self,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    async_dir: Path,
  ) -> None:
    self.monkeypatch = monkeypatch
    self.capsys = capsys
    self.job_path = async_dir / JID
    self.cursor_path = async_dir / f'{JID}.cursor'
    self.journal = Journal()
    self.code: object = None
    self.result: dict[str, Any] = {}
    real_run_command = basic.AnsibleModule.run_command

    def get_bin_path(
      module: basic.AnsibleModule,
      arg: str,
      *,
      required: bool = False,
      **_kwargs: object,
    ) -> str | None:
      if self.journal.script is not None:
        return str(self.journal.script)
      if self.journal.missing:
        if required:
          module.fail_json(msg=f'Failed to find required executable "{arg}"')
        return None
      return f'/usr/bin/{arg}'

    def run_command(
      module: basic.AnsibleModule,
      cmd: list[str],
      *,
      expand_user_and_vars: bool = True,
    ) -> tuple[int, str, str]:
      if self.journal.script is not None:
        return real_run_command(
          module,
          cmd,
          expand_user_and_vars=expand_user_and_vars,
        )
      self.journal.commands.append(cmd)
      if self.journal.on_call is not None:
        self.journal.on_call()
      return self.journal.rc, self.journal.out, self.journal.err

    monkeypatch.setattr(basic.AnsibleModule, 'get_bin_path', get_bin_path)
    monkeypatch.setattr(basic.AnsibleModule, 'run_command', run_command)

  def write_job(self, info: dict[str, Any]) -> bytes:
    raw = json.dumps(info).encode()
    self.job_path.write_bytes(raw)
    os.utime(self.job_path, ns=(OLD_TIME_NS, OLD_TIME_NS))
    return raw

  def call(self, **params: object) -> None:
    # module arguments the same way for ansible-core without patch_module_args
    args = {
      'jid': JID,
      'name': UNIT,
      '_async_dir': str(self.job_path.parent),
      **params,
    }
    self.monkeypatch.setattr(
      basic,
      '_ANSIBLE_ARGS',
      json.dumps({
        'ANSIBLE_MODULE_ARGS': args,
      }).encode(),
    )
    self.monkeypatch.setattr(basic, '_ANSIBLE_PROFILE', 'legacy', raising=False)
    with pytest.raises(SystemExit) as exc_info:
      main()
    self.code = exc_info.value.code
    self.result = json.loads(self.capsys.readouterr().out)


@pytest.fixture(name='status_run')
def fixture_status_run(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  tmp_path: Path,
) -> StatusRun:
  return StatusRun(monkeypatch, capsys, tmp_path)


def jwrite(path: Path, info: dict[str, Any]) -> None:
  # the same steps as jwrite of async_wrapper: temporary file and atomic rename
  tmp_path = path.with_name(f'{path.name}.tmp')
  tmp_path.write_text(json.dumps(info), encoding='utf-8')
  tmp_path.rename(path)


def pick(result: dict[str, Any], *keys: str) -> dict[str, Any]:
  return {
    key: result.get(key)
    for key in keys
  }


def test_race_keeps_final_result(status_run: StatusRun) -> None:
  status_run.write_job(START)
  status_run.journal.on_call = lambda: jwrite(status_run.job_path, FINAL)
  status_run.call(epoch=EPOCH)
  assert status_run.code == 0  # noqa: S101
  assert not status_run.result['finished']  # noqa: S101
  job = json.loads(status_run.job_path.read_text(encoding='utf-8'))
  assert job == FINAL  # noqa: S101
  status_run.journal.on_call = None
  status_run.call(epoch=EPOCH)
  assert status_run.code == 0  # noqa: S101
  assert status_run.result == {  # noqa: S101
    **FINAL,
    'finished': True,
    'ansible_job_id': JID,
  }


@pytest.mark.parametrize('info', [START, FINAL], ids=['running', 'finished'])
def test_status_keeps_job_file(status_run: StatusRun, info: dict[str, Any]) -> None:
  raw = status_run.write_job(info)
  before = status_run.job_path.stat()
  status_run.journal.out = f'{RETRY_LINE}\n'
  status_run.call()
  after = status_run.job_path.stat()
  assert status_run.code == 0  # noqa: S101
  assert status_run.job_path.read_bytes() == raw  # noqa: S101
  assert after.st_ino == before.st_ino  # noqa: S101
  assert after.st_mtime_ns == before.st_mtime_ns  # noqa: S101


@pytest.mark.parametrize(
  ('params', 'tag'),
  [
    ({}, f'mega-launch-{UNIT}'),
    ({
      'epoch': EPOCH,
    }, f'mega-launch-{UNIT}-{EPOCH}'),
  ],
  ids=['no-epoch', 'epoch'],
)
def test_journal_command(
  status_run: StatusRun,
  params: dict[str, Any],
  tag: str,
) -> None:
  status_run.write_job(START)
  status_run.call(**params)
  status_run.call(**params)
  expected = [
    '/usr/bin/journalctl',
    '-t',
    tag,
    f'--cursor-file={status_run.cursor_path}',
    '-o',
    'cat',
  ]
  assert status_run.journal.commands == [expected, expected]  # noqa: S101


def test_cursor_path_reaches_journalctl_unchanged(
  monkeypatch: pytest.MonkeyPatch,
  capsys: pytest.CaptureFixture[str],
  tmp_path: Path,
) -> None:
  # the module expands $HOME of the path argument once, the directory has that name
  monkeypatch.setenv('HOME', '$MEGA_TEST_DIR')
  monkeypatch.setenv('MEGA_TEST_DIR', 'expanded')
  async_dir = tmp_path / "worker's async $MEGA_TEST_DIR"
  async_dir.mkdir()
  args_path = tmp_path / 'journalctl.args'
  script = tmp_path / 'journalctl'
  script.write_text(
    f"#!/bin/sh\nprintf '%s\\000' \"$@\" > '{args_path}'\n",
    encoding='utf-8',
  )
  script.chmod(script.stat().st_mode | stat.S_IXUSR)
  status_run = StatusRun(monkeypatch, capsys, async_dir)
  status_run.journal.script = script
  status_run.write_job(START)
  status_run.call(epoch=EPOCH, _async_dir=f"{tmp_path}/worker's async $HOME")
  assert status_run.code == 0  # noqa: S101
  expected = [
    '-t',
    f'mega-launch-{UNIT}-{EPOCH}',
    f'--cursor-file={async_dir}/{JID}.cursor',
    '-o',
    'cat',
  ]
  actual = args_path.read_bytes().split(b'\0')[:-1]
  assert actual == [arg.encode() for arg in expected]  # noqa: S101


def test_running_job_returns_journal_lines(status_run: StatusRun) -> None:
  status_run.write_job(START)
  status_run.journal.out = f'{RETRY_LINE}\n\n{EXIT_LINE}\n'
  status_run.call()
  assert status_run.code == 0  # noqa: S101
  expected = {
    **START,
    'warning_lines': [RETRY_LINE, EXIT_LINE],
  }
  # 0 == False in Python, JSON keeps finished 0 of async_wrapper apart from false
  actual_json = json.dumps(status_run.result, sort_keys=True)
  assert actual_json == json.dumps(expected, sort_keys=True)  # noqa: S101


def test_job_without_finished_key(status_run: StatusRun) -> None:
  status_run.write_job({
    'started': 1,
    'ansible_job_id': JID,
  })
  status_run.call()
  assert status_run.code == 0  # noqa: S101
  assert status_run.result['finished'] is False  # noqa: S101


def test_finished_job_drops_journal_lines_and_cursor(status_run: StatusRun) -> None:
  status_run.write_job(FINAL)
  status_run.cursor_path.write_text(CURSOR, encoding='utf-8')
  status_run.journal.out = f'{EXIT_LINE}\n'
  status_run.call()
  assert status_run.code == 0  # noqa: S101
  assert status_run.result == {  # noqa: S101
    **FINAL,
    'finished': True,
    'ansible_job_id': JID,
  }
  assert not status_run.cursor_path.exists()  # noqa: S101


def test_running_job_keeps_cursor(status_run: StatusRun) -> None:
  status_run.write_job(START)
  status_run.cursor_path.write_text(CURSOR, encoding='utf-8')
  status_run.call()
  assert status_run.code == 0  # noqa: S101
  expected = {
    **START,
    'warning_lines': [],
  }
  assert status_run.result == expected  # noqa: S101
  assert status_run.cursor_path.read_text(encoding='utf-8') == CURSOR  # noqa: S101


@pytest.mark.parametrize('cursor', ['present', 'absent'])
def test_cleanup_removes_job_and_cursor(status_run: StatusRun, cursor: str) -> None:
  status_run.write_job(START)
  if cursor == 'present':
    status_run.cursor_path.write_text(CURSOR, encoding='utf-8')
  status_run.call(mode='cleanup')
  assert status_run.code == 0  # noqa: S101
  expected = {
    'ansible_job_id': JID,
    'erased': str(status_run.job_path),
  }
  actual = pick(status_run.result, 'ansible_job_id', 'erased')
  assert actual == expected  # noqa: S101
  assert not status_run.job_path.exists()  # noqa: S101
  assert not status_run.cursor_path.exists()  # noqa: S101
  assert status_run.journal.commands == []  # noqa: S101


@pytest.mark.parametrize('mode', ['status', 'cleanup'])
def test_missing_job_file(status_run: StatusRun, mode: str) -> None:
  status_run.call(mode=mode)
  assert status_run.code == 1  # noqa: S101
  keys = ('msg', 'failed', 'ansible_job_id', 'started', 'finished')
  assert pick(status_run.result, *keys) == {  # noqa: S101
    'msg': 'could not find job',
    'failed': True,
    'ansible_job_id': JID,
    'started': True,
    'finished': True,
  }
  assert status_run.journal.commands == []  # noqa: S101


def test_journalctl_failure(status_run: StatusRun) -> None:
  raw = status_run.write_job(START)
  status_run.journal.rc = 1
  status_run.journal.err = 'journal is broken'
  status_run.call()
  assert status_run.code == 1  # noqa: S101
  keys = ('msg', 'failed', 'started', 'finished')
  assert pick(status_run.result, *keys) == {  # noqa: S101
    'msg': f"Unable journalctl -t '{UNIT}': journal is broken",
    'failed': True,
    'started': None,
    'finished': None,
  }
  assert status_run.job_path.read_bytes() == raw  # noqa: S101


def test_journalctl_no_records_running_job(status_run: StatusRun) -> None:
  raw = status_run.write_job(START)
  status_run.cursor_path.write_text(CURSOR, encoding='utf-8')
  status_run.journal.rc = 1
  status_run.call()
  assert status_run.code == 0  # noqa: S101
  expected = {
    **START,
    'warning_lines': [],
  }
  actual_json = json.dumps(status_run.result, sort_keys=True)
  assert actual_json == json.dumps(expected, sort_keys=True)  # noqa: S101
  assert status_run.job_path.read_bytes() == raw  # noqa: S101
  assert status_run.cursor_path.read_text(encoding='utf-8') == CURSOR  # noqa: S101


def test_journalctl_no_records_finished_job(status_run: StatusRun) -> None:
  status_run.write_job(FINAL)
  status_run.cursor_path.write_text(CURSOR, encoding='utf-8')
  status_run.journal.rc = 1
  status_run.call()
  assert status_run.code == 0  # noqa: S101
  assert status_run.result == {  # noqa: S101
    **FINAL,
    'finished': True,
    'ansible_job_id': JID,
  }
  assert not status_run.cursor_path.exists()  # noqa: S101


@pytest.mark.parametrize(
  ('rc', 'out', 'err'),
  [
    (1, f'{RETRY_LINE}\n', ''),
    (2, '', ''),
    (1, '\n', ''),
    (1, '', ' '),
  ],
  ids=['rc1-stdout', 'rc2-empty', 'rc1-blank-stdout', 'rc1-blank-stderr'],
)
def test_journalctl_failure_not_empty(
  status_run: StatusRun,
  rc: int,
  out: str,
  err: str,
) -> None:
  raw = status_run.write_job(START)
  status_run.journal.rc = rc
  status_run.journal.out = out
  status_run.journal.err = err
  status_run.call()
  assert status_run.code == 1  # noqa: S101
  expected = {
    'msg': f"Unable journalctl -t '{UNIT}': {err}",
    'failed': True,
  }
  actual = pick(status_run.result, 'msg', 'failed')
  assert actual == expected  # noqa: S101
  assert status_run.job_path.read_bytes() == raw  # noqa: S101


def test_journalctl_missing(status_run: StatusRun) -> None:
  status_run.write_job(START)
  status_run.journal.missing = True
  status_run.call()
  assert status_run.code == 1  # noqa: S101
  keys = ('msg', 'failed')
  assert pick(status_run.result, *keys) == {  # noqa: S101
    'msg': 'Failed to find required executable "journalctl"',
    'failed': True,
  }
  assert status_run.journal.commands == []  # noqa: S101


@pytest.mark.parametrize(
  'content',
  [b'', b'{"started": 1, "fini', None],
  ids=['empty', 'truncated', 'unreadable'],
)
def test_unreadable_job_file(status_run: StatusRun, content: bytes | None) -> None:
  if content is None:
    status_run.job_path.mkdir()
  else:
    status_run.job_path.write_bytes(content)
  status_run.call()
  assert status_run.code == 0  # noqa: S101
  keys = ('results_file', 'ansible_job_id', 'started', 'finished')
  assert pick(status_run.result, *keys) == {  # noqa: S101
    'results_file': str(status_run.job_path),
    'ansible_job_id': JID,
    'started': True,
    'finished': False,
  }
  assert status_run.journal.commands == []  # noqa: S101
