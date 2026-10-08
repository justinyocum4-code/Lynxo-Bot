# Lynxo Bot — Discord security bot (Phase 1: security core)

All-in-one server protection: anti-raid lockdown, heat-based spam scoring,
word/invite/scam-link filters, strike escalation (warn, timeout, kick, ban),
quarantine, button verification, and an 18+ photo-check flow with a mod
review queue. Everything is logged in plain English to #mod-logs.

No paid services are used. The optional AI moderation uses Groq's free tier.

## What Phase 1 does

- **Anti-raid:** 5 or more joins in 10 seconds triggers lockdown — invites are
  deleted, verification buttons pause for 10 minutes, the server owner gets a
  DM, and it is logged.
- **Spam scoring:** every message earns "heat" for fast repeats, duplicates,
  mass mentions, caps floods, and links. Enough heat warns, then times out.
- **Filters:** banned words, external Discord invite links, and common
  scam/phishing patterns are deleted on sight. An all-links filter is
  available but off by default. Manage with the /filter commands.
- **Strikes:** 2 strikes = timeout, 3 = kick, 4 = ban (defaults).
- **Verification:** /setup builds #verify-here, #get-adult-access, #mod-logs,
  #verify-review plus Member, Adult 18+, and Quarantined roles, with
  persistent buttons. Regular users press a button to get Member. The 18+
  button starts a DM photo challenge (random number 1-5, selfie holding up
  that many fingers); photos land in #verify-review for mods to approve
  with /approve18 or deny with /deny18. Three denials flag manual review.
- **Logging:** every auto action is posted to #mod-logs in plain words with
  who, what, why, and when.

## Setup

### 1. Create your Discord server

In the Discord app: tap the plus button, choose "Create My Own", give it a
name. Only server owners can invite bots, so do this on your own account.

### 2. Invite the bot with Administrator

1. Open the Discord developer portal and select your application.
2. Go to OAuth2, then URL Generator.
3. Check the `bot` scope.
4. Under bot permissions, check `Administrator`.
5. Copy the generated URL at the bottom, open it, pick your new server,
   and authorize.

### 3. Turn on the two privileged intents

In the developer portal, open your application, go to the Bot page, and turn
ON both of these:

- Server Members Intent
- Message Content Intent

Then save. The bot cannot see joins or scan messages without them.

### 4. Deploy on Render (free)

1. Push this folder to a GitHub repo so these files are at the repo root.
2. In the Render dashboard: New, then Blueprint, and point it at the repo.
3. When it asks for environment variables, add:
   - `DISCORD_TOKEN` — your bot token (paste it; it stays secret).
   - `GROQ_API_KEY` — only if you want the AI moderation assist; leave it
     out otherwise and the built-in filters do the job alone.
4. Deploy. The bot starts itself and opens a `/health` page so Render can
   see it is alive.

### 5. Run /setup in your server

In any channel, type `/setup` and send it (you must be the server owner or
an admin). The bot creates the channels, roles, and buttons, then confirms.

## Keeping it awake on the free tier

Render's free tier puts web services to sleep after about 15 minutes with no
traffic. The bot has a `/health` page for this. Use a free uptime monitor
(such as UptimeRobot's free plan — no card needed) to visit your Render URL
plus `/health` every 5 minutes. That keeps the bot awake around the clock.

## Commands

- `/warn`, `/timeout`, `/kick`, `/ban`, `/purge`, `/strikes`, `/clearstrikes`
- `/quarantine`, `/unquarantine`
- `/approve18`, `/deny18`
- `/filter add`, `/filter remove`, `/filter list`
- `/filter toggle_words`, `/filter toggle_invites`, `/filter toggle_links`,
  `/filter toggle_ai`
- `/setup` (admin, run once)

## Local testing

1. Copy `.env.example` to `.env` and put your bot token in it.
2. `pip install -r requirements.txt`
3. `python bot.py`

Never put your real token in any file you share or upload.

## Notes and limits

- Settings, strikes, and verification state are kept in a small `data.json`
  file. Render's free disk wipes on restart, so long-term history moves to
  real storage in Phase 3 (backups + dashboard).
- The 18+ photo check is human-reviewed in Phase 1. The code has a clean
  `vision.py` stub ready for an AI vision provider in a later phase.
- Slash commands can take up to an hour to appear the very first time;
  after /setup they sync to your server immediately.

## Coming later

- Phase 2: anti-nuke (channel/role/webhook/prune/bot-add watches + rollback)
  and panic/lockdown modes.
- Phase 3: server backups + restore, and the web dashboard.
