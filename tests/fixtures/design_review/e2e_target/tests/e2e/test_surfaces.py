"""The fixture target's e2e suite: two tests capture a surface each, one never does (deselected in capture mode)."""
from __future__ import annotations

from tests.e2e._surface import surface


def test_item_sheet(desktop_page):
    desktop_page.click("#openSheet")
    desktop_page.locator("#itemSheet").wait_for(state="visible")
    surface(desktop_page, "items.sheet")


def test_home_on_phone(phone_page):
    phone_page.locator("#home").wait_for(state="visible")
    surface(phone_page, "home.root")


def test_title_only(desktop_page):
    assert desktop_page.title() == "design_review e2e fixture"
