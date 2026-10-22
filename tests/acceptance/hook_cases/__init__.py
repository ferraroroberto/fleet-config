"""Case data for the hook-payload acceptance matrix (`acceptance.hook_matrix`).

One module per hook family, each a list of `(label, hook, payload, expected exit)` records.
"""
from __future__ import annotations

from typing import Any, Dict, Tuple

Case = Tuple[str, str, Dict[str, Any], int]
