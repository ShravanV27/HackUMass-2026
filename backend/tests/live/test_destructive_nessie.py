"""Destructive Nessie sandbox tests — never run without explicit approval.

Requirements (all mandatory):
- pytest ``--run-destructive``
- ``CONFIRM_NESSIE_DESTRUCTIVE=I_UNDERSTAND``
- ``NESSIE_DESTRUCTIVE_API_KEY`` distinct from daily ``NESSIE_API_KEY``

These tests do **not** run during ordinary pytest or ``--run-live``.
They are not executed by the agent while implementing this suite.
"""

from __future__ import annotations

import os

import pytest
import requests

from tests.live.helpers import CONFIRM_DESTRUCTIVE, require_destructive_nessie_key

pytestmark = pytest.mark.destructive


def test_destructive_gates_are_enforced() -> None:
    """Fails closed unless confirmation + dedicated key are present."""
    key = require_destructive_nessie_key()
    assert key
    assert key != (os.getenv("NESSIE_API_KEY") or "").strip()
    assert os.getenv("CONFIRM_NESSIE_DESTRUCTIVE") == CONFIRM_DESTRUCTIVE


def test_sandbox_seed_endpoint_explicit_only() -> None:
    """POST /data/seed against the *dedicated* destructive key only.

    Never uses the shared daily NESSIE_API_KEY. Never auto-wipes without the
    confirmation gate already checked by ``require_destructive_nessie_key``.
    """
    if os.getenv("CONFIRM_NESSIE_SEED", "").strip() != "SEED":
        pytest.skip(
            "Extra confirmation required: set CONFIRM_NESSIE_SEED=SEED to "
            "actually call the Nessie seed endpoint"
        )

    api_key = require_destructive_nessie_key()
    base_url = (
        os.getenv("NESSIE_BASE_URL") or "https://v2-prod-api.nessieisreal.com"
    ).rstrip("/")

    response = requests.post(
        f"{base_url}/data/seed",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    # Do not print response body (may contain sandbox data).
    assert response.status_code < 500
    assert api_key not in (response.text or "")
