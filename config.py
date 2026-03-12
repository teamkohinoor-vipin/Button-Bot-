import os

# --- ⚙️ CONFIGURATION (can be overridden by environment variables) ---

# Bot token – set in environment for security
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8434464254:AAEJl6T3HYmvJYVd4g5opoaD5cEjC7s1L5M")

# Owner IDs (hardcoded owners, cannot be removed)
ADMIN_IDS = [int(x.strip()) for x in os.environ.get("ADMIN_IDS", "8262107211").split(",") if x.strip()]

SUPPORT_USERNAME = "KHRsupportBot"
REFERRAL_NOTIFICATION_GROUP = "https://t.me/+tIwH7ctrekc1YThl"
LOG_CHANNEL_ID = int(os.environ.get("LOG_CHANNEL_ID", -1003472844347))

# --- GROUP UNLIMITED SEARCH CONFIGURATION ---
OFFICIAL_GROUP_ID = int(os.environ.get("OFFICIAL_GROUP_ID", -1003490016636))
OFFICIAL_GROUP_LINK = "https://t.me/+OdNjwHMDXZtiNzA1"

# --- CHANNEL JOIN CONFIGURATION ---
CHANNEL_1_INVITE_LINK = "https://t.me/osnitInfo"
REQUIRED_CHANNEL_1_ID = int(os.environ.get("REQUIRED_CHANNEL_1_ID", -1003411597042))
CHANNEL_2_INVITE_LINK = "https://t.me/+EnHwtMwircJkNzk1"
REQUIRED_CHANNEL_2_ID = int(os.environ.get("REQUIRED_CHANNEL_2_ID", -1003227457437))

# --- 📱 PHONE NUMBER API (NEW) ---
# Base URL with {num} placeholder – change this if API changes
PHONE_API_NEW = os.environ.get("PHONE_API_NEW", "https://cyber-apis.vercel.app/search?key=ZEXX_@TRY&number={num}")

# --- CREDITS SETTINGS ---
INITIAL_CREDITS = 3
REFERRAL_CREDITS = 5
NEW_USER_REFERRAL_CREDITS = 2
SEARCH_COST = 1
REDEEM_COOLDOWN_SECONDS = 3600
REFERRAL_PREMIUM_DAYS = 1
REFERRAL_TIER_1_COUNT = 15
REFERRAL_TIER_2_COUNT = 70

# --- DEFAULT DAILY FREE LIMIT ---
DEFAULT_DAILY_LIMIT = 3

# --- DEFAULT AUTO-DELETE TIME (seconds) ---
DEFAULT_AUTO_DELETE_TIME = 60
