## Decisions

- **Proxy, not firewall rules.** Rootless podman can't program host nftables per container, but an `--internal` network plus a dual-homed proxy gives the same effect with no privileges. Claude Code honours `HTTPS_PROXY`.
- **One proxy per run.** Logs attribute cleanly to a bout without parsing source IPs, and a crashed bout can't leave shared state behind. Cost is ~1 s startup.
- **CONNECT to 443 only.** Plain HTTP proxying is refused; everything legitimate is TLS.
- **Same image.** The proxy is a 40-line node script in the runner image, so it's pinned by the same digest as the bout.

## Risks

- DNS for arbitrary names still resolves inside the internal network's resolver, but nothing is routable, so lookups can't carry data out beyond the resolver itself. Acceptable for now; a DNS allowlist is a possible follow-up.
