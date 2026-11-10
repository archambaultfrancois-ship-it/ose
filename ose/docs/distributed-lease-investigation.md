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
