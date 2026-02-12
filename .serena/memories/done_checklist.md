Before claiming completion:
1) Run relevant pytest scope (at least targeted tests; for read-contract work run `-m read`).
2) Verify daemon/runtime behavior with `sari --cmd status` when touching indexing/daemon code.
3) Report if local source and installed `uv tool` binary versions diverge.
4) Include concrete failure evidence (status snapshot + log lines) for runtime bugs.