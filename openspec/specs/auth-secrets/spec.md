# auth-secrets Specification

## Purpose
TBD - created by archiving change bootstrap-engine. Update Purpose after archive.
## Requirements
### Requirement: Configurable auth modes
The engine SHALL support `api_key`, `oauth` and `credentials_file` auth modes, reading the token from a configured host env var name, optionally loaded from a configured env file.

#### Scenario: Named OAuth token
- **WHEN** runtime config sets `auth.mode: oauth` and `auth.token_env: TOC_CLAUDE_CODE_OAUTH_TOKEN`
- **THEN** the container receives it as `CLAUDE_CODE_OAUTH_TOKEN`

### Requirement: Secrets never leak
The engine SHALL pass secrets to podman by variable name only, and SHALL scrub secret values and `sk-ant-` patterns from every artifact before writing it.

#### Scenario: Token in transcript
- **WHEN** a transcript line contains the token value
- **THEN** the stored transcript contains `[REDACTED]` instead

