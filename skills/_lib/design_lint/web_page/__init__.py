"""The web-page lenses: a public static site checked against `design.web.md`.

A web page shares no tokens and no components with the web-app spec, so it
gets its own `tokens` lens (the contract's token names declared, values left
to the brand) and its own contract set (header, phone menu, sections, card,
carousel, footer, image roles, plus the keyboard and motion floor). The
`adoption` lens runs as it is; `vendored` and `siblings` are narrowed to what
a site can act on (`scope.py`).
"""
from __future__ import annotations

from .contracts import web_contracts
from .scope import adopted_only, without_build_copies
from .tokens import contract_token_names, web_tokens

__all__ = ["adopted_only", "contract_token_names", "web_contracts", "web_tokens", "without_build_copies"]
