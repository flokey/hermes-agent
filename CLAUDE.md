@AGENTS.md

# Rules for this fork (flokey/hermes-agent)

This file is ours; `AGENTS.md` above is upstream's development guide and applies unchanged.
Other coding agents: these rules apply to you too.

## This repository is public: nothing personal goes in

Everything that leaves your machine from this repository is public and permanent: code, patches,
tests, fixtures, comments, docs, commit messages, branch names, tags, issues, pull requests and
review comments, here or on upstream NousResearch/hermes-agent.

None of it may contain, or let a reader infer, any of these about the owner or anyone they know:

- names, usernames, handles, email addresses, phone numbers;
- account, user, chat, channel, guild or bot ids on any platform;
- family members, children, home or work locations;
- travel: trips, flights, hotels, bookings, dates and places;
- purchases, orders, wishlists, prices paid, finances;
- calendar entries, messages, emails or chat logs, even shortened;
- servers and deployments: hostnames, IP addresses, cloud provider details, file paths outside
  this repository, container or service names, cron jobs, installed plugins;
- configuration values, tokens, keys, cookies or secrets of any kind;
- names of private repositories or projects, and anything copied out of them.

Examples and test data must be invented and generic ("alice", "example.com", "Flight XY123",
"2030-01-01"). Never base an example on a real log, message, booking or bug report from the
owner's own setup, even with the names changed. Describe a bug by its mechanism, not by the
real event that exposed it.

Commits use the author email `1598611+flokey@users.noreply.github.com`; set it before
committing if it isn't already.

## Before every push or post

1. Read the full diff, every commit message, and any PR, issue or comment text you are about
   to publish.
2. Search it for the categories above (names, emails, ids, IPs, paths, dates, places).
3. If anything matches, rewrite it before publishing. A history that was already pushed with
   personal data must be reported to the owner, not quietly force-pushed over.

These rules list categories only. Do not add the owner's actual details here or anywhere
else in this repository, not even as an example of what to avoid.

## Branch conventions

See `FORK.md`. In short: one commit per fix on `hermes-<version>-flokey`, each naming its
upstream PR and when to drop it.
