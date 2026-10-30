"""Suite-wide isolation."""

from __future__ import annotations

from dataclasses import replace

import pytest

from jobhunter.outreach import inboxes


@pytest.fixture(autouse=True)
def _gmail_only_inbox_pool(monkeypatch):
    """No test sees the sending inboxes configured on this machine (2026-10-30: once movin@'s
    password was in .env, the send tests started sending through it). The pool is the Gmail
    alone; a test that wants more inboxes sets them itself (test_inboxes.py)."""
    # the Gmail as the older tests know it: it may take a guessed address (the live config
    # may say gmail_verified_only)
    gmail_box = replace(inboxes._gmail(), verified_only=False)
    monkeypatch.setattr(inboxes, "all_inboxes", lambda: [gmail_box])
    monkeypatch.setattr(inboxes, "ready", lambda: [gmail_box])
