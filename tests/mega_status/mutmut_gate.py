# cspell:ignore appimage autojunk
from __future__ import annotations

import difflib
import json
import subprocess  # noqa: S404  # nosec B404
import sys
from dataclasses import dataclass, field
from pathlib import Path

SOURCE = Path('library/mega_status.py')
META = Path('mutants/library/mega_status.py.meta')
ALLOWLIST = Path('tests/mega_status/mutmut-equivalent.txt')
# changed fragment: line ranges from the start anchor line up to the end anchor line
# exclusive, every anchor must match exactly one source line
ANCHORS = (
  ("cursor_path = f'{log_path}.cursor'", 'if not os.path.exists(log_path):'),
  ("if mode == 'cleanup':", '# NOT in cleanup mode'),
  ('try:', '# just write the module output'),
)
# mutmut writes pytest exit code, only 1 means failed tests
KILLED = 1
SURVIVED = 0
PYTEST_ERROR = 3
Signature = tuple[tuple[str, ...], tuple[str, ...]]


@dataclass
class MutantDiff:
  lines: set[int] = field(default_factory=set)
  removed: list[str] = field(default_factory=list)
  added: list[str] = field(default_factory=list)

  @property
  def signature(self) -> Signature:
    return tuple(self.removed), tuple(self.added)

  def text(self) -> str:
    lines = [f'  - {line}' for line in self.removed]
    lines.extend(f'  + {line}' for line in self.added)
    return ''.join(f'{line}\n' for line in lines)


@dataclass
class Allowlist:
  entries: dict[Signature, str] = field(default_factory=dict)
  reason: str = ''
  removed: list[str] = field(default_factory=list)
  added: list[str] = field(default_factory=list)

  def flush(self) -> None:
    if self.reason:
      self.entries[tuple(self.removed), tuple(self.added)] = self.reason
    self.reason = ''
    self.removed = []
    self.added = []

  def load(self, path: Path) -> None:
    for line in path.read_text(encoding='utf-8').splitlines():
      if not line.strip() or line.startswith('#'):
        continue
      if line.startswith('='):
        self.flush()
        self.reason = line[1:].strip()
      elif line.startswith('-') and self.reason:
        self.removed.append(line[1:].strip())
      elif line.startswith('+') and self.reason:
        self.added.append(line[1:].strip())
      else:
        sys.exit(f'bad line [{line}] in [{path}]')
    self.flush()


def anchor_line(source: list[str], anchor: str) -> int:
  found = [num for num, line in enumerate(source) if anchor in line]
  if len(found) != 1:
    sys.exit(f'anchor [{anchor}] found in [{len(found)}] lines, expected [1]')
  return found[0]


def fragment_lines(source: list[str]) -> set[int]:
  lines: set[int] = set()
  for start, end in ANCHORS:
    first = anchor_line(source, start)
    last = anchor_line(source, end)
    if first >= last:
      sys.exit(f'anchor [{start}] is not above anchor [{end}]')
    lines.update(range(first, last))
  return lines


def apply_mutant(appimage: str, name: str, pristine: str) -> MutantDiff:
  subprocess.run(  # noqa: S603  # nosec B603
    [appimage, 'mutmut', 'apply', name],
    check=True,
    capture_output=True,
  )
  mutated = SOURCE.read_text(encoding='utf-8')
  SOURCE.write_text(pristine, encoding='utf-8')
  old = pristine.splitlines()
  new = mutated.splitlines()
  diff = MutantDiff()
  matcher = difflib.SequenceMatcher(a=old, b=new, autojunk=False)
  for tag, old_from, old_to, new_from, new_to in matcher.get_opcodes():
    if tag == 'equal':
      continue
    # pure insertion touches the line before which it is inserted
    diff.lines.update(range(old_from, max(old_to, old_from + 1)))
    diff.removed.extend(line.strip() for line in old[old_from:old_to])
    diff.added.extend(line.strip() for line in new[new_from:new_to])
  return diff


def main() -> int:
  appimage = sys.argv[1]
  pristine = SOURCE.read_text(encoding='utf-8')
  fragment = fragment_lines(pristine.splitlines())
  codes = json.loads(META.read_text(encoding='utf-8'))['exit_code_by_key']
  if not codes:
    sys.exit(f'no mutants in [{META}]')
  allowlist = Allowlist()
  allowlist.load(ALLOWLIST)
  used: set[Signature] = set()
  failed = False
  for name in sorted(codes):
    code = codes[name]
    if code == KILLED:
      continue
    diff = apply_mutant(appimage, name, pristine)
    if not diff.lines & fragment:
      if code != PYTEST_ERROR:
        sys.stdout.write(f'not killed [{name}] code [{code}]\n{diff.text()}')
      continue
    if code == SURVIVED and diff.signature in allowlist.entries:
      used.add(diff.signature)
      reason = allowlist.entries[diff.signature]
      sys.stdout.write(f'equivalent [{name}]: {reason}\n')
      continue
    failed = True
    sys.stdout.write(f'FRAGMENT NOT KILLED [{name}] code [{code}]\n{diff.text()}')
  for signature, reason in allowlist.entries.items():
    if signature not in used:
      failed = True
      sys.stdout.write(f'STALE equivalent entry [{reason}]\n')
  return 1 if failed else 0


if __name__ == '__main__':
  sys.exit(main())
