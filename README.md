# Lynxo Bot — Discord security bot (Phases 1–3)

All-in-one server protection: anti-raid lockdown, heat-based spam scoring,
word/invite/scam-link filters, strike escalation (warn, timeout, kick, ban),
quarantine, button verification, 18+ photo-check flow, anti-nuke watches
with panic/lockdown modes, server backups with restore, and a phone-friendly
web dashboard. Everything is logged in plain English to #mod-logs.

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

## What Phase 2 does (anti-nuke + panic)

- **Nuke watches:** 3+ channel creates/deletes in 60 seconds, 3+ role
  changes in 60 seconds, webhook creation spam, a role gaining
  Administrator, a member gaining Administrator, a bot being added, or
  5+ member removals (kick/ban/prune) in 60 seconds. The server owner,
  the bot itself, and anyone you exempt are never punished.
- **On trigger:** the offender is stripped of all roles (or banned, your
  choice via `/nuke action`), rogue webhooks are deleted, you get a DM,
  it is logged, and — if a backup exists — the bot restores it
  automatically (toggle with `/nuke auto_restore`). A 5-minute cooldown
  keeps one incident from spamming alerts.
- **What can and can't be reverted:** deleted channels and roles can be
  recreated from a backup (names, positions, permissions, overwrites).
  Messages inside channels are NOT in backups and can't come back.
  Bans done by the attacker need a manual unban. Webhooks the attacker
  made are deleted, not restored.
- **Panic modes:** `/panic` locks all text channels (nobody can talk),
  deletes invites, and pauses verification. `/lockdown` does all that
  plus freezes voice channels. `/unlock` puts every permission back the
  way it was. Use `/nuke status` to see the current settings.
- **Exemptions:** `/nuke exempt_add` / `/nuke exempt_remove` /
  `/nuke exempt_list` for trusted users or roles.

## What Phase 3 does (backups + dashboard)

- **Backups:** `/backup` snapshots every role and channel (names,
  positions, permissions, overwrites) and sends you the file — keep it
  somewhere safe, because files stored on the bot disappear when it
  restarts. `/backups` lists what is on the bot. `/restore` takes an
  uploaded backup file, shows you what it holds, and rebuilds anything
  missing after you press "Yes, restore". Existing things are skipped,
  never duplicated.
- **Dashboard API:** the bot's web server also serves a small JSON API
  under `/api/*` for the dashboard page. It is locked with a
  `DASHBOARD_KEY` you choose — without the key, every call is rejected.
  Endpoints: health, settings (get/put), mod log, strikes
  (lookup/clear), backup, backup list, panic, unlock.
- **Dashboard page:** the `dashboard/` folder is a static site (no build
  step): settings toggles, mod log, user strike lookup, panic buttons,
  and backup controls. Gold-on-black, big buttons, screen-reader
  friendly. Host it on GitHub Pages (repo Settings > Pages > Deploy
  from branch, folder `/dashboard`), then open the page on your phone,
  enter your bot's address (e.g. `https://lynxo-bot.onrender.com`) and
  your dashboard key once — it remembers them.

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
   - `DASHBOARD_KEY` — any long random string you make up. This is the
     password for the dashboard page. Use the same value in the dashboard.
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
- `/panic`, `/lockdown`, `/unlock`
- `/nuke status`, `/nuke exempt_add`, `/nuke exempt_remove`,
  `/nuke exempt_list`, `/nuke action`, `/nuke auto_restore`
- `/backup`, `/backups`, `/restore`
- `/setup` (admin, run once)

## Local testing

1. Copy `.env.example` to `.env` and put your bot token in it.
2. `pip install -r requirements.txt`
3. `python bot.py`

Never put your real token in any file you share or upload.

## Notes and limits

- Settings, strikes, and verification state are kept in a small `data.json`
  file. Render's free disk wipes on restart, so treat it as temporary.
  Backups you download from `/backup` (or the dashboard) are the durable
  copies — keep them.
- The 18+ photo check is human-reviewed in Phase 1. The code has a clean
  `vision.py` stub ready for an AI vision provider in a later phase.
- Slash commands can take up to an hour to appear the very first time;
  after /setup they sync to your server immediately.
- The dashboard API is disabled until you set `DASHBOARD_KEY`. The page
  will tell you plainly if the key is wrong or the bot is asleep.
- Anti-nuke finds the culprit through Discord's audit log. If it can't
  tell who did it, it alerts you loudly but punishes nobody — better safe
  than banning the wrong person.
