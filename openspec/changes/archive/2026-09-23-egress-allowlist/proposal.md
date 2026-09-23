## Why

Contenders are third-party prompts and plugins. With open networking a skill can tell the agent to fetch or post anything, which is a safety problem and a confound (a skill that pulls in outside help isn't measuring the same thing).

## What Changes

- Bout and judge containers join an internal-only podman network.
- A per-run proxy sidecar (node, from the runner image) tunnels CONNECT to allowlisted hosts on 443 only and logs every decision.
- `record.json` gains `egress: {mode, allowed, denied}`; the lock and bout IDs include the network config.
- `runtime.network.mode: open` keeps the old behaviour for debugging.

## Capabilities

### New Capabilities
- `egress`: allowlisted, logged network egress for agent containers.

### Modified Capabilities
None.

## Impact

Runner image gains `/opt/skillordeal/egress-proxy.mjs`. Bout IDs change for new locks (network config is an input).
