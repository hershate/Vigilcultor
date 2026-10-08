"""Encrypted secret vault: AES-GCM with Argon2id-derived master key.

Master password lifecycle (user decision, 2026-10-08):
- set during the first-run setup wizard;
- the service starts locked; an administrator unlocks it in the panel, which
  caches the derived key to ``unlock.key`` (0600) so other local processes
  (workers, scheduler) can load it; an explicit lock deletes that file;
- ``VIGILCULTOR_MASTER_PASSWORD`` (environment) always takes precedence and
  requires no unlock.
"""
