# Daemon Event Controller Spec (P0)

## Scope
- Target: `src/sari/mcp/daemon.py`
- Goal: prevent zombie/incorrect shutdown decisions by serializing state transitions.

## Event Model
- Event type: `DaemonEvent`
- Fields:
  - `event_type: str`
  - `lease_id: str`
  - `conn_id: str`
  - `ts: float`
  - `payload: dict[str, object]`
- Core event types:
  - `LEASE_ISSUE`
  - `LEASE_RENEW`
  - `LEASE_REVOKE`
  - `CONN_CLOSED`
  - `HEARTBEAT_TICK`
  - `SHUTDOWN_REQUEST`

## Serialization Rule
- External actors (session callbacks, main finally, heartbeat thread) only enqueue events.
- Lease mutation and suicide-state transitions are executed only in controller loop.

## Drain Strategy
- Queue is drained with upper bound: `max_events` (default `256`).
- Tick handling rule:
  - Coalesce multiple `HEARTBEAT_TICK` events to one.
  - Place coalesced tick at front of current drained batch.
- Non-tick control events remain ordered in drained batch.

## Suicide State Machine
- States: `idle | grace | stopping`
- Transition rules:
  - `idle -> grace`: no active clients/leasing signal.
  - `grace -> idle`: any client/lease activity resumes before deadline.
  - `grace -> stopping`: `lease==0` and `now >= grace_deadline` and `workers_inflight==0`.
  - `grace -> stopping` (forced): inhibit timeout exceeded.
  - `stopping`: terminal for current lifecycle; no transition back.

## Shutdown Ownership
- Actual shutdown execution is one-shot guarded by `_shutdown_once`.
- `shutdown()` is idempotent.
- `main()` and other callers request shutdown via `SHUTDOWN_REQUEST` event.

## Observability Contract
- Status exports:
  - `active_leases_count`
  - `leases`
  - `reaper_last_run_at`
  - `suicide_state`
  - `shutdown_reason` / `last_shutdown_reason`
  - `workers_alive`
  - `no_client_since`
  - `grace_remaining`
  - `shutdown_once_set`
  - `last_event_ts`
  - `event_queue_depth`
