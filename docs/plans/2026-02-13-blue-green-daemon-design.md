# Blue/Green Daemon Design

## Goal
Implement zero-downtime daemon rollout with fixed user-facing HTTP endpoint (`127.0.0.1:47777`) and automatic switch/rollback.

## Decisions (Validated)
- Fixed router owns `47777` permanently.
- Blue/green daemons run on internal ports only.
- Auto deploy trigger: version mismatch detection.
- Rollback trigger: 3 consecutive health failures after switch.
- Deployment metadata tracked in registry with generation-based idempotency.

## Architecture
- Router (fixed ingress): receives all user traffic and proxies to active daemon.
- Active daemon: serves current stable traffic.
- Candidate daemon: started during deploy; health-checked before switch.
- Registry SSOT: `deployment` block + workspace -> boot mapping.

## State Model
`idle -> starting -> ready -> switched -> rolling_back -> idle`

Deployment fields:
- `generation`
- `active_boot_id`
- `candidate_boot_id`
- `old_boot_id`
- `state`
- `switch_ts`
- `health_fail_streak`
- `rollback_reason`

## Invariants
- User-visible endpoint stays fixed at `47777`.
- Exactly one active boot at a time.
- Switch/rollback applies at most once per generation.
- Reuse success is valid only if workspace attach succeeds.

## Failure Handling
- Candidate startup/health failure: abort switch, keep active.
- Post-switch health failure streak >= 3: rollback active to old boot.
- Draining timeout on old daemon: force stop as final safety path.

## Observability
Expose in status/doctor/dashboard:
- deploy generation
- active/candidate/old boot IDs
- state
- health failure streak
- rollback reason/time

## Implementation Phases
### Phase A (Control Plane)
- Registry deployment metadata/API
- lifecycle deploy lock
- idempotent generation operations

### Phase B (Data Plane)
- fixed router forwarding to active daemon
- daemon switch + old draining

### Phase C (Automation + Ops)
- auto deploy trigger integration
- rollback automation
- status/dashboard integration

## Test Strategy
- Unit: registry deploy methods, generation mismatch no-op, rollback restoration.
- Integration: start blue -> candidate green -> switch -> health failures -> rollback.
- Concurrency: parallel lifecycle operations lock contention.
