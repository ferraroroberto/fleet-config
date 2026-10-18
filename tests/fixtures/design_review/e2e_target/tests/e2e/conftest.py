"""Conftest of the `measure --e2e` fixture target's suite (fleet-config#1498).

`_surface.py` is not checked in here: tests/test_design_review_browser.py copies project-scaffolding's vendored
hook in beside this file when it builds the throwaway copy, as an adopting app vendors it.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from tests.e2e._surface import pytest_collection_modifyitems  # noqa: F401 — capture-mode deselect

PAGE = (Path(__file__).resolve().parents[2] / "page.html").as_uri()


@pytest.fixture(scope="session")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture
def desktop_page(pw):
    browser = pw.chromium.launch()
    page = browser.new_context(viewport={"width": 1280, "height": 800}).new_page()
    page.goto(PAGE)
    yield page
    browser.close()


@pytest.fixture
def phone_page(pw):
    browser = pw.chromium.launch()
    page = browser.new_context(**pw.devices["Pixel 7"]).new_page()
    page.goto(PAGE)
    yield page
    browser.close()
