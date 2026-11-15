# flokey/hermes-agent

A maintenance fork of [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent).

## Before you contribute

This fork is public. Read [CLAUDE.md](CLAUDE.md) first: nothing personal goes into code,
tests, commit messages, issues or pull requests, and every example must be invented.

## Branches

- `hermes-0.22.6-flokey`: the v0.22.6 release plus the fixes below, one commit each. Every
  commit message says which upstream PR it comes from (or which upstream PR fixes the same bug)
  and when to drop it.
- `main` tracks upstream and carries nothing extra.

## Fixes on this branch

| Commit subject | Upstream |
|---|---|
| fix(gateway): title voice-only Discord sessions from the STT transcript | #137984 (same bug, open) |
| fix(web): stop keyless Exa extract from silently capping pages at 3,000 chars | #146585 |
| fix(telegram): skip the read-receipt getUpdates at the end of stop() on network recovery | #131799 |
| fix(telegram): never reuse idle sockets in any adapter pool | #128001 |
| fix(memory): skip the post-turn recall warm-up on one-turn cron runs | #152531 |
| fix(cron): treat a code-formatted [SILENT] sentinel as silence | #123700 |
| fix(cron): a whitespace-padded [SILENT] marker leaks to the chat | #151288 |
| fix(discord): retire an orphaned typing loop instead of typing forever | #145944 |
| fix(discord): skip empty chunks - Discord 400s code 50006 on empty content | #143819 |

All were open and unmerged upstream as of 2026-11-14. The backports keep each PR's code and
tests as the PR's authors wrote them; credit is theirs.

## Upgrading

Start a new branch `hermes-<version>-flokey` from the new release tag and cherry-pick the
commits here whose upstream PR is not in that release. Run each PR's tests again.
