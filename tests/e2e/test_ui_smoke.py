"""Browser smoke tests for the Audit Trail review UI.

These drive the real page and lock in the shell + the features added recently
(brand, environment banner/switch, connectors panel, audit-history viewer, theme).
They run without live QuickBooks (see conftest), so they're safe and deterministic.
"""

import re

from playwright.sync_api import Page, expect


def test_page_titled_audit_trail(page: Page, ui_base_url: str):
    page.goto(ui_base_url)
    expect(page).to_have_title(re.compile("Audit Trail"))
    expect(page.locator("header h1")).to_have_text("Audit Trail")


def test_environment_banner_shows_sandbox(page: Page, ui_base_url: str):
    page.goto(ui_base_url)
    expect(page.locator("#envBanner")).to_contain_text("SANDBOX")


def test_welcome_empty_state_visible(page: Page, ui_base_url: str):
    page.goto(ui_base_url)
    expect(page.locator(".empty")).to_be_visible()
    expect(page.locator(".empty")).to_contain_text("Review")


def test_theme_toggle_switches_light_and_back(page: Page, ui_base_url: str):
    page.goto(ui_base_url)
    html = page.locator("html")
    expect(html).to_have_attribute("data-theme", "dark")   # default
    page.click("#themeBtn")
    expect(html).to_have_attribute("data-theme", "light")
    page.click("#themeBtn")
    expect(html).to_have_attribute("data-theme", "dark")


def test_connectors_modal_opens_and_closes(page: Page, ui_base_url: str):
    page.goto(ui_base_url)
    page.click("#connectorsBtn")
    overlay = page.locator("#connOverlay")
    expect(overlay).to_be_visible()
    expect(overlay).to_contain_text("Connectors")
    page.click("#connClose")
    expect(overlay).not_to_be_visible()


def test_environment_switch_modal_shows_current_env(page: Page, ui_base_url: str):
    page.goto(ui_base_url)
    page.click("#envBanner")
    overlay = page.locator("#envOverlay")
    expect(overlay).to_be_visible()
    expect(overlay).to_contain_text("Currently targeting")
    expect(overlay).to_contain_text("SANDBOX")


def test_audit_history_modal_opens_on_empty_dir(page: Page, ui_base_url: str):
    page.goto(ui_base_url)
    page.click("#auditBtn")
    overlay = page.locator("#auditOverlay")
    expect(overlay).to_be_visible()
    expect(overlay).to_contain_text("Audit history")
    # empty AUDIT_DIR -> the empty-state message
    expect(page.locator("#auditBody")).to_contain_text("No applied splits")
