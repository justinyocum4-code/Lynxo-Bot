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
