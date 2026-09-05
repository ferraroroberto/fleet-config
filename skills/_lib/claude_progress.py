"""Compatible Claude entrypoint; lifecycle implementation is shared.

Import aliases retain the historical Python helper surface, including injected
test clocks and retry constants. Existing scheduled batch files need no change.
"""
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scheduled_runner

def __getattr__(name: str) -> Any:
    """Also support import-by-path callers retaining their original module."""
    return getattr(scheduled_runner, name)


if __name__ == "__main__":
    raise SystemExit(scheduled_runner.main())
sys.modules[__name__] = scheduled_runner
