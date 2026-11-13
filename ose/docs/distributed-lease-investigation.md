# Distributed authorization expiry investigation

The failing case was
`tests.integration.test_distributed_inputs.DistributedInputTests.test_broker_feedback_preserves_max_id_and_passive_pull`.
Its fixture forced an 800 ms worker lease, compared with the six-second production
default. Heartbeats ran every 200 ms. The recording/message-ID fixture similarly
shortened the lease to two seconds.

## Reproduction and measurements

- The original case passes alone and in eight instrumented repetitions. The
  measured maximum heartbeat round trip in those repetitions was about 21 ms.
- Eight more repetitions with four additional CPU workers also pass. CPU load
  alone therefore did not reproduce the failure in this experiment.
- An injected 950 ms pause of the runtime event loop after the first heartbeat
  reproduces `RuntimeError: coordinator authorization expired`. The probe
  records a 950 ms gap on both CLOCK_BOOTTIME and the monotonic clock, followed
  by the worker watchdog's expiration check. The coordinator can also retire
  the session and close its control connection.
- The same pause with a six-second lease passes the complete feedback exchange.
- The earlier qualification reproduced expiration using committed pre-microstep
  distributed/model-validation code as well.

The demonstrated mechanism is a scheduling pause longer than the shortened
fixture lease, not a lost renewal while the loop progresses normally. The exact
source of the pauses during the earlier qualification was not captured; this
investigation does not establish that CPU contention, Android suspend or a
particular synchronous operation caused them. Heartbeats and the watchdog share
the runtime event loop. Concurrent RPC processing cannot renew a lease while that
loop itself is stopped. Suspend is deliberately included in lease accounting.

## Changes and verification

The callback/routing and recording/message-ID fixtures now use the production
lease default. A regression injects the 950 ms pause during initialization and
then checks the real feedback exchange, IDs and consumption. The existing
`test_lost_heartbeat_expires_live_control_connection` still verifies that losing
heartbeats expires a live connection. Runtime lease/fencing behavior and the
six-second production default are unchanged. The inactivity test now selects
timeouts at/after the post-feedback deadline: with the normal heartbeat period,
a receiver can legitimately time out while the sender is still learning initial
activation. Earlier startup timeouts do not invalidate subsequent inactivity
semantics.

Probe scripts and measurements are in ignored `ose/build/lease_probe.py`,
`lease-probe-events*.json` and `lease-investigation-*.log`. The recorded probes
were diagnostic experiments; the initialization-pause case is retained in the
test suite.

Final qualification: all 19 distributed-input and recording/message-ID cases
pass together (`ose/build/lease-investigation-final.log`), including the six
cases that failed in the earlier batch. The lost-heartbeat expiry check and
initialization-pause regression also pass together
(`ose/build/lease-investigation-regression.log`).

## Shared-engine qualification

Some other functional fixtures still used one- or two-second leases for routing,
recording and TLS checks. They now use the six-second production default. The
lost-heartbeat test explicitly retains its one-second lease and checks actual
retirement; unit tests also check the watchdog at the exact expiry boundary.
The runtime's default, suspend-aware clock and generation fencing are unchanged.

Worker renewal now uses the heartbeat request's departure as its conservative
local authorization origin. A reply taking a full lease is refused before
enabling execution. The next renewal subtracts the preceding round-trip time
from its quarter-lease delay. Controlled-clock tests cover delayed replies,
normal latency and expiry without waiting for a network reply.

Timing assumptions were also removed from three functional checks:

- Component disable acknowledgement stops future callbacks; it does not mean
  all previously committed publications have reached the consumer. The test
  waits until every committed source sequence has been observed before checking
  that no new publications appear while disabled.
- The C feedback handler counts activations and fires exactly three times.
  Its test now arms each one-shot timer after the previous feedback and delivery
  acknowledgements, rather than assuming that the network completes within a
  200 ms periodic interval.
- A capture window is defined by deposit timestamps, not callback indices.
  Timer expirations can coalesce, so sequence 1 can legitimately arrive after
  50 ms. The integration test measures the actual recording origin and checks
  every saved timestamp against the exact half-open window, retaining the
  first-sequence check for a capture without a window.

On Android, keep the test host awake during distributed qualification, for
example with `termux-wake-lock`, releasing it with `termux-wake-unlock` afterwards.
An actual suspend longer than a lease must still fence the affected generation;
extending or ignoring authorization on resume would violate that contract.

Qualification of these corrections: 387 unit tests, 112 recording/replay tests,
86 network tests (one Ada test skipped because GNAT is unavailable), and three
repetitions of each of the five formerly timing-sensitive cases all pass.
Vibration also runs with six ready components, a two-second lease and 36 observer
events, without replacing any worker generation.
