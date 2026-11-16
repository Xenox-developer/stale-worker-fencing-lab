# Stale worker fencing: preregistered fault matrix

Frozen on 2026-11-16 before primary measurements. This is a deterministic local engineering experiment, not a vendor benchmark, paper reproduction or measurement of LLM decisions.

## Question and novelty

Can an old background worker overwrite a newer result after its lease expires and a replacement takes over? Hypothesis: a storage-side fencing check blocks an older token after a newer token has been committed; acquiring a lease alone does not, and a separate ownership check has a check/write race. Unlike our earlier retry/idempotency experiment, this study concerns two overlapping workers with different generations, not repeated delivery of one operation or duplicate side effects.

## Fixed interface and conditions

Implement only `store.py`, Python standard library. `Store(path)` initializes SQLite with `journal_mode=DELETE`, `synchronous=FULL`, busy timeout 5 seconds. Connections must be short-lived and no transaction may span an IPC barrier. Schema: `lease(id INTEGER PRIMARY KEY, generation INTEGER, owner TEXT, expires INTEGER)` with singleton id=1 initially (0,'',0); `resource(id INTEGER PRIMARY KEY, token INTEGER, payload TEXT)` with singleton id=1 initially (0,NULL); `writes(seq INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, token INTEGER, payload TEXT)`. Payload is canonical JSON (`ensure_ascii=False`, sorted keys, separators comma/colon). Resource token records the last accepted writer, and is not independently clamped in unsafe baselines.

`acquire(owner, now, ttl)` atomically refuses with ValueError if the lease expires strictly after `now`; otherwise increments the generation, records owner and `expires=now+ttl`, and returns the positive integer token. Accept nonempty string owner, nonnegative exact integer now and positive exact integer ttl; bool is not an integer for this contract. No clock reading: the harness supplies logical time to control expiry without timing races.

`prepare(mode, owner, token, now)` returns a boolean. Modes: `lease_only`, `precheck`, `fenced`. Lease-only and fenced return True (fenced enforces at the storage write). Precheck reads the lease and returns True only when owner and generation match and `now < expires`. It closes its connection before returning. `commit(mode, owner, token, payload, allowed)` returns a boolean; false allowed means no mutation. Lease-only and precheck accept an allowed write unconditionally. Fenced accepts only if the incoming token is at least the resource's stored token, checking and updating within one `BEGIN IMMEDIATE` transaction. On acceptance, atomically update resource and append exactly one writes row. On rejection, change nothing. Reject unknown modes and nonpositive/bool tokens with ValueError. This is a monotonic-resource-token mechanism, NOT immediate revocation on lease expiry: an expired token may still write until a higher token has been committed. Same-token writes are allowed; this is not idempotency or owner authentication.

## Fixed matrix and barriers

Eight authored payload pairs × three conditions × three schedules = 72 trials. Fresh database and two actual owned Python child processes per trial, each process with a distinct PID and its own database connections. All workers are simulated executors; no measured model calls. IPC command boundaries hold worker A while B runs; this is a controlled wait, not a measured network delay, process crash, SIGSTOP, random scheduler race or machine-clock change. Parent and children do not hold transactions while waiting for commands.

For every trial A acquires at now=0, ttl=10, receiving token 1. Payload A is `old`, payload B is `new` from the fixture.

1. `clean`: A prepares at now=1 and commits, no handoff. B exists but receives no database command. This is a negative control against rejecting all writes.
2. `handoff_before_check`: B acquires at now=11, ttl=10 (token 2), prepares at now=12, commits its new result, then A prepares at now=12 and tries to commit its old result.
3. `handoff_after_check`: A prepares at now=1, then waits at the IPC barrier. B acquires/prepares/commits at 11/12, then A resumes its previously prepared commit. This isolates the precheck/write gap.

Exactly one primary run; shuffle all 72 cells with seed 20261116 and preserve schedule.json. Payload variations are coverage cases, not independent probability estimates. No throughput, latency, failure-rate confidence interval or vendor ranking. No silent retry, dropped trial or retrospective dataset tuning.

## Outcomes and independent verification

Primary: stale A commits after B's higher token commit, count/16 per condition and count/8 in each handoff schedule. Secondary: final stale overwrite, replacement B success count/16, clean A success count/8. Keep all per-command requests/responses/PIDs, worker exit codes/stderr, post-command database snapshots, and final SQLite databases. Each response must match the persisted state and write log. Report any missing cell, timeout, invalid response or unexpected exit; these block a publication-ready result.

Protected tests cover acquisition exclusion and generation, expiry boundary and reopening, baseline writes, precheck late rejection and prechecked stale acceptance, fenced stale rejection, same-token accepted writes, future fresh token accepted writes, invalid inputs, no-op rejection, and independence of logical resources. The harness scorer must be checked using positive, negative and deliberately malformed controls before primary measurement. A separate audit imports neither store nor primary scorer; it verifies frozen hashes, exact matrix/order, event chronology, PID separation, all row snapshots and final databases, recomputes outcomes and compares aggregates. Do not replace measured outcomes with expected architecture behavior.

## Tool and resources

INVARIANT on this project's actual protected acceptance suite implements the store through the current Codex subscription, validates it and performs semantic review/guarded application. Preserve version, IDs, fixed evaluation receipts, operational execution evidence, application verification and complete available usage. The host harness measures the owned local processes. No new models, paid APIs, rented compute, Redis installation, external targets or changes to the real publishing automation. SQLite lease and resource share one database here; this does not implement or validate Redlock, distributed token allocation, remote API fencing, failover durability or exactly-once effects. No claim that an existing agent product has this bug.
