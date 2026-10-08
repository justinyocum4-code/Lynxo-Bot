"""Default per-guild settings and filter patterns for Lynxo Bot (Phase 1)."""

DEFAULT_SETTINGS = {
    # Anti-raid
    "raid_join_threshold": 5,      # joins inside the window that trigger lockdown
    "raid_join_window": 10,        # seconds
    "raid_lockdown_minutes": 10,
    # Heat-based spam scoring
    "spam_warn_heat": 8,
    "spam_timeout_heat": 15,
    "spam_timeout_minutes": 5,
    # Auto-mod filters
    "word_filter": True,
    "invite_filter": True,         # block external Discord invite links
    "link_filter": False,          # block ALL links (off by default)
    "massping_filter": True,      # treat @everyone/@here from non-mods as spam
    "ai_moderation": False,        # Groq assist; only used if GROQ_API_KEY is set
    "banned_words": [
        "nigger", "nigga", "faggot", "retard", "kike",
        "chink", "spic", "tranny", "cunt",
    ],
    # Strike escalation: strikes needed for each step
    "strikes_timeout": 2,
    "strikes_kick": 3,
    "strikes_ban": 4,
    "escalation_timeout_minutes": 10,
    # Join security: brand-new accounts
    "new_account_check": True,     # flag/quarantine accounts younger than X days
    "new_account_age_days": 7,     # account age threshold in days
    "new_account_action": "flag",  # "flag" = log it, "quarantine" = isolate them
    # Ghost pings + copypasta
    "ghostping_filter": True,      # punish @everyone then delete
    "copypasta_count": 3,          # identical messages inside the window
    "copypasta_window": 30,        # seconds
    # Audit log
    "audit_log": True,             # log deleted/edited messages to #mod-logs
    # Anti-nuke (Phase 2)
    "nuke_channel_threshold": 3,   # channel creates/deletes inside the window
    "nuke_channel_window": 60,     # seconds
    "nuke_role_threshold": 3,      # role creates/deletes/updates inside the window
    "nuke_role_window": 60,        # seconds
    "nuke_webhook_threshold": 3,   # webhook creations inside the window
    "nuke_webhook_window": 60,     # seconds
    "nuke_remove_threshold": 5,    # member removals (kick/ban/prune) inside the window
    "nuke_remove_window": 60,     # seconds
    "nuke_action": "strip",        # "strip" = remove all roles, "ban" = ban the offender
    "nuke_auto_restore": True,     # restore from latest backup after a nuke trigger
    "nuke_cooldown_minutes": 5,    # quiet period after a trigger so one incident doesn't spam
    # Automatic scheduled backups
    "auto_backup": True,           # take a backup automatically every day
    "auto_backup_channel_id": None,  # where to post it (None = mod-log channel)
    # More security (Phase 4)
    "mass_mention_filter": True,   # block messages pinging many users at once
    "mass_mention_count": 10,      # distinct user mentions that trigger it
    "voice_raid_protection": True,  # watch for mass voice mutes/kicks
    "heat_slowmode": True,         # auto-slow a channel when chat gets heated
    "heat_slowmode_seconds": 10,   # slowmode length to apply
    "heat_slowmode_threshold": None,  # heat score that triggers it (None = automatic)
    "raid_pattern_check": True,    # watch for raid-style usernames joining together
    # Community
    "welcome_enabled": False,
    "welcome_channel_id": None,
    "welcome_message": "Welcome to the server, {user}!",
    "goodbye_enabled": False,
    "goodbye_channel_id": None,
    "goodbye_message": "{name} has left the server.",
    "leveling_enabled": False,     # XP for chatting, level-up announcements
    "levelup_channel_id": None,    # None = announce in the channel they leveled in
    "level_roles": {},             # {"5": role_id} — granted on reaching that level
    "starboard_enabled": False,
    "starboard_channel_id": None,
    "starboard_threshold": 3,      # distinct non-bot star reactions needed
    "suggest_channel_id": None,    # messages here get thumbs up/down reactions
    # Utility
    "custom_commands": {},         # trigger -> response text (!trigger in chat)
    "autoresponders": {},          # phrase -> response text
    "stats_enabled": False,        # member-count voice channel at the top
    "tickets_enabled": False,      # let members open support tickets
}

# Regex fragments (case-insensitive) for common scam / phishing lures.
SCAM_PATTERNS = [
    r"free\s+nitro",
    r"nitro\s+free",
    r"discord\s*gift",
    r"steamcommunity\.com/gift",
    r"verify\s+your\s+wallet",
    r"double\s+your\s+(crypto|eth|btc|usdt)",
    r"airdrop.*claim",
    r"mint\s+now",
    r"send\s+\d.*(eth|btc|usdt).*(get|receive|back)",
]
