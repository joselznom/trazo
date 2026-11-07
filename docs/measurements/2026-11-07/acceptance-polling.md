# Acceptance report

- Command: `run.py --auto push`
- Commit: b6005a5b9c13
- Started: 2026-11-06T23:50:00+00:00
- Result: 12 passed, 0 failed, 6 skipped, 0 not automated

| Scenario | Status | Seconds | Note |
| --- | --- | --- | --- |
| n1-sandbox-chain | passed | 5.5 |  |
| n1-port-busy | passed | 4.2 |  |
| n1-force-quit-build | passed | 4.3 |  |
| n1-old-install-update | passed | 5.1 |  |
| n1-setup-status-ok | passed | 3.6 |  |
| n1-install-newer-than-plugin | passed | 4.8 |  |
| n1-two-sessions-share-preview | passed | 5.2 |  |
| n2-slash-commands | skipped | 3.3 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-mcp-connection-time | skipped | 3.4 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-resume-no-hook-note | skipped | 0.4 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-no-venv-standin | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-claude | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-install-codex | passed | 1.1 |  |
| n2-update-claude | skipped | 0.2 | the Claude login in the keychain has expired or is about to: use `claude` once to renew it |
| n2-update-codex | passed | 1.8 |  |
| n2-codex-hook-retrust | passed | 7.8 |  |
| n2-codex-tools-by-state | passed | 3.9 |  |
| n2-codex-hook-note | passed | 4.0 |  |


Full local report: `t3-preview-status/.build/acceptance/20261106T235000Z/report.md`.
Only the summary is archived. Full-report SHA256: `2a52b81a414b4ab36eee367b2cb0c0e21c2ed755da93ecf34c8e84c0ac2c2b1d`.
