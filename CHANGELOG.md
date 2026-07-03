# Changelog

Notable updates to Jarvis, newest first. Edit this file to add an entry —
the website parses and renders it automatically, no template changes needed.

## 2026-07-01 — Credits leaderboard

- Added a public Jarvis Credits leaderboard to the website (`/leaderboard`),
  mirroring the in-Discord `!leaderboard` command.
- `/api/stats` is now cached for a few seconds server-side so the site holds
  up better under real traffic.

## 2026-06-15 — Reliability pass

- Bot now flushes pending state (messages, credits, streaks) to storage on
  shutdown, so a redeploy no longer risks losing the last few seconds of
  activity.
- Added automatic retry with backoff on Discord login rate limits.

## 2026-05-20 — Economy expansion

- Introduced daily check-in bonuses and streak milestones.
- Added the Jarvis Credit shop and mystery boxes.
- Added credit transfers between users.

## 2026-04-10 — Memory & summaries

- Jarvis can now remember facts about you across sessions (`@Jarvis remember that …`).
- Added `!summary` to summarise recent conversation history.
