from __future__ import annotations

import contextlib

# pylint: disable=import-error
import psutil  # type: ignore[reportMissingImports]


def calc_ports(
  main_pid: int,
  result_ports: set[int],
  module_ports: set[int],
) -> int:
  def_res = 0
  result_ports.clear()
  with contextlib.suppress(psutil.NoSuchProcess):
    result_ports.update({
      laddr.port
      for laddr in [
        conn.laddr for conn in psutil.Process(main_pid).connections()
        if conn.status == psutil.CONN_LISTEN
      ]
    } if main_pid > 0 else {
      sc.laddr.port  # type: ignore[reportAttributeAccessIssue]
      for sc in psutil.net_connections() if sc.status == 'LISTEN'
    })
  if main_pid == 0:
    insect = module_ports.intersection(result_ports)
    if insect:
      result_ports.clear()
      result_ports.update(insect)
    else:
      def_res += 1
  else:
    def_res += int(module_ports.issubset(result_ports))
  return def_res
