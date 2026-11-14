"""Opt-in live integration tests.

Never collected during ordinary ``pytest`` unless ``--run-live`` is passed.
Live tests are non-destructive: they never purge Nessie or wipe DATABASE_URL.
Demo resets belong only in ``python scripts/reset_demo.py --reset``.
See ``docs/live_testing.md``.
"""
