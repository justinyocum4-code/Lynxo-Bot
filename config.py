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
