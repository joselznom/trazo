# Acceptance report

- Command: `run.py --auto push`
- Commit: 6edd9451ec25
- Started: 2026-11-06T23:41:07+00:00
- Result: 12 passed, 0 failed, 6 skipped, 0 not automated

| Scenario | Status | Seconds | Note |
| --- | --- | --- | --- |
| n1-sandbox-chain | passed | 5.2 |  |
| n1-port-busy | passed | 3.8 |  |
| n1-force-quit-build | passed | 4.0 |  |
| n1-old-install-update | passed | 4.9 |  |
| n1-setup-status-ok | passed | 3.4 |  |
| n1-install-newer-than-plugin | passed | 5.4 |  |
| n1-two-sessions-share-preview | passed | 4.9 |  |
| n2-slash-commands | skipped | 3.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-mcp-connection-time | skipped | 3.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-resume-no-hook-note | skipped | 0.4 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-no-venv-standin | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-claude | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-codex | passed | 1.1 |  |
| n2-update-claude | skipped | 0.3 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-update-codex | passed | 1.7 |  |
| n2-codex-hook-retrust | passed | 7.3 |  |
| n2-codex-tools-by-state | passed | 3.6 |  |
| n2-codex-hook-note | passed | 5.0 |  |


Full local report: `t3-mcp-deadline/.build/acceptance/20261106T234107Z/report.md`.
Only the summary is archived. Full-report SHA256: `a143e30a254e249bf07656c91441785c642a7819714a4e251d296bbc6ecf63ce`.
