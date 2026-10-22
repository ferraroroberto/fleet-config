"""Synthetic credential-shaped strings for the secret_scan_guard cases.

One per family in `_lib.SECRET_PATTERNS`. Each is assembled from fragments at runtime
so no contiguous token literal ever sits in this source file — that would trip
GitHub's push protection (and the very guard under test).
"""
from __future__ import annotations

FAKE_XOXB = "-".join(("xo" + "xb", "2444556677", "8899001122", "AbCdEfGhIjKlMnOpQrStUvWx"))
FAKE_GHP = "gh" + "p_" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
FAKE_SK = "sk" + "-" + "AbCdEfGhIjKlMnOpQrStUvWxYz012345"
FAKE_AKIA = "AK" + "IA" + "IOSFODNN7EXAMPLE"
