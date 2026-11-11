// foreman-shim healthcheck: the shim listens on TCP 3013.
// Do NOT use the gateway image's docker-healthcheck (it probes the gateway's
// /healthz on localhost, which does not exist inside the shim container).
require('net').connect(3013, '127.0.0.1')
  .on('connect', () => process.exit(0))
  .on('error', () => process.exit(1));
