# Acceptance report

- Command: `run.py --auto push`
- Commit: 6ee0bae9a8a6
- Started: 2026-11-07T00:00:19+00:00
- Result: 12 passed, 0 failed, 6 skipped, 0 not automated

| Scenario | Status | Seconds | Note |
| --- | --- | --- | --- |
| n1-sandbox-chain | passed | 5.7 |  |
| n1-port-busy | passed | 4.2 |  |
| n1-force-quit-build | passed | 4.2 |  |
| n1-old-install-update | passed | 5.1 |  |
| n1-setup-status-ok | passed | 3.6 |  |
| n1-install-newer-than-plugin | passed | 5.6 |  |
| n1-two-sessions-share-preview | passed | 5.0 |  |
| n2-slash-commands | skipped | 3.3 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-mcp-connection-time | skipped | 3.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-resume-no-hook-note | skipped | 0.4 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-no-venv-standin | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-claude | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-codex | passed | 1.2 |  |
| n2-update-claude | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-update-codex | passed | 1.8 |  |
| n2-codex-hook-retrust | passed | 7.8 |  |
| n2-codex-tools-by-state | passed | 3.7 |  |
| n2-codex-hook-note | passed | 5.2 |  |


Full local report: `t3-measure-integrated/.build/acceptance/20261107T000019Z/report.md`.
Only the summary is archived. Full-report SHA256: `3c0e9ce68d2a9cd684fdfee9487164476b3c75c8abdabf812a751cef741d73ce`.
