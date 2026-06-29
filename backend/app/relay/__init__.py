"""Cloud<->bridge relay: an in-process registry that routes push jobs to a connected bridge.

Single-process scope (API + inline taskiq). For multi-process prod (separate Redis workers + several
API pods), this needs a Redis pub/sub layer so the pod holding the bridge socket receives the job —
see plan §10.8. The dispatch protocol mirrors the Go bridge's ``internal/protocol`` frames.
"""
