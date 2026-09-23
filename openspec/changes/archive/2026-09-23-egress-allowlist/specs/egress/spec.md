## ADDED Requirements

### Requirement: Allowlisted egress
In `allowlist` mode the engine SHALL run agent containers on an internal-only network whose only route out is a proxy that tunnels to allowlisted hosts on port 443.

#### Scenario: Allowed host
- **WHEN** the agent connects to `api.anthropic.com:443`
- **THEN** the connection succeeds and is counted under `egress.allowed`

#### Scenario: Other host
- **WHEN** the agent or a tool it runs connects to any other host, directly or via the proxy
- **THEN** the direct connection is unreachable, the proxied one gets 403, and the proxied attempt is counted under `egress.denied`

### Requirement: Network config is locked
The engine SHALL record the network config in the lock and include it in bout IDs.

#### Scenario: Mode change
- **WHEN** `runtime.network.mode` changes between two locks
- **THEN** the bout IDs differ
