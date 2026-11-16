Can a paused background worker overwrite the result of its replacement?

I tested this failure mode in a small local lab for background-agent infrastructure: two Python worker processes, one SQLite resource, eight synthetic payload pairs and three controlled schedules. There were 72 trials across three write policies.

In the handoff cases, worker A's lease expired, worker B took over and committed a newer result, then A resumed.

Stale writes accepted after B's commit:
• Lease acquisition only: 16/16.
• Separate ownership check before writing: 8/16.
• Storage-side fencing: 0/16.

The separate check blocked A when the handoff happened before the check. It failed in all 8 cases where A passed the check, paused, and then wrote after B.

All three policies accepted the replacement's write in 16/16 handoff cases and completed 8/8 clean controls.

The fenced path compared the incoming token with the last accepted token and updated the result in one transaction. That rejects an older token after a newer token has been committed. It does not cancel the old worker or enforce lease expiry by itself.

I used INVARIANT to implement the store, run eight fixed acceptance checks and review the implementation. A separate script audited every saved command, database transition and final result.

These are deterministic counterexamples with logical time and controlled waits. No LLM decisions, production agents, distributed token allocation or external APIs were tested. The eight payloads are coverage cases, not a reliability estimate.

Code, protocol, raw evidence and chart:
https://github.com/Xenox-developer/stale-worker-fencing-lab

INVARIANT:
https://github.com/Xenox-developer/invariant

Background:
https://claude.com/resources/articles/building-always-on-agents
https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/
