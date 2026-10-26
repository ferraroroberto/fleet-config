import inspect
import json
import os

from _suite import CALL_GOLDEN, SURFACE_GOLDEN, check, v
import e2e_value as _e2e_value


# ---- characterization pin: the calls above, and the module's public surface (fleet-config#1247) ---------


def _surface(module) -> dict:
    """Every public name (and `_wall_s`, which this file calls): a function's signature, a constant's value."""
    out = {}
    for name in sorted(vars(module)):
        attr = getattr(module, name)
        if name.startswith("_") and name != "_wall_s" or inspect.ismodule(attr):
            continue
        if inspect.isfunction(attr):
            out[name] = f"def {name}{inspect.signature(attr)}"
        elif isinstance(attr, (bool, int, float, str, tuple, frozenset)):
            out[name] = f"{type(attr).__name__} {attr!r}"
    return out


_surface_now = _surface(_e2e_value)
if os.environ.get("E2E_VALUE_WRITE_CALL_GOLDEN") == "1":
    CALL_GOLDEN.write_text(json.dumps(v.calls, indent=0, ensure_ascii=False) + "\n", encoding="utf-8")
    SURFACE_GOLDEN.write_text(json.dumps(_surface_now, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
_calls_want = json.loads(CALL_GOLDEN.read_text(encoding="utf-8"))
_calls_got = json.loads(json.dumps(v.calls))
_at = next((i for i, (g, w) in enumerate(zip(_calls_got, _calls_want)) if g != w),
           min(len(_calls_got), len(_calls_want)))
check(_calls_got == _calls_want,
      f"every e2e_value call's result matches the golden ({len(_calls_got)} calls, golden {len(_calls_want)}; "
      f"first difference at #{_at}: got {str(_calls_got[_at:_at + 1])[:600]} want {str(_calls_want[_at:_at + 1])[:600]})")
_surface_want = json.loads(SURFACE_GOLDEN.read_text(encoding="utf-8"))
check(_surface_now == _surface_want,
      "e2e_value's public names, signatures and constants match the golden -- "
      f"missing {sorted(set(_surface_want) - set(_surface_now))}, extra {sorted(set(_surface_now) - set(_surface_want))}, "
      f"changed {sorted(k for k in set(_surface_now) & set(_surface_want) if _surface_now[k] != _surface_want[k])}")
