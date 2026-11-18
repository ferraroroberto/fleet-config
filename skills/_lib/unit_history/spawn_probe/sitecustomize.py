"""Count every `subprocess.Popen` in a gate run, tagged with the unit that was running.

Loaded by the interpreter at start because `gate_evidence run` puts this folder on `PYTHONPATH`.
Inert unless the gate's spawn-report env var is set. stdlib only and never raises: a counter
must not be able to fail the gate it measures.
"""

import os

_REPORT = os.environ.get("FLEET_GATE_SPAWN_REPORT")
if _REPORT:
    try:
        import json
        import subprocess

        _real_init = subprocess.Popen.__init__

        def _counting_init(self, *args, **kwargs):
            try:
                line = json.dumps({"unit": os.environ.get("FLEET_GATE_UNIT", ""), "pid": os.getpid()}) + "\n"
                fd = os.open(_REPORT, os.O_WRONLY | os.O_APPEND | os.O_CREAT)
                try:
                    os.write(fd, line.encode("utf-8"))
                finally:
                    os.close(fd)
            except Exception:
                pass
            _real_init(self, *args, **kwargs)

        subprocess.Popen.__init__ = _counting_init
    except Exception:
        pass
