import logging
import json
import asyncio
import secrets
import time
import html
import os
import re
from datetime import datetime, timedelta
from collections import defaultdict
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler
from telegram.constants import ParseMode
from io import BytesIO

# --- Import config and API module ---
import config
from api import fetch_from_new_api

# --- 💾 DATA FILES (same as original) ---
USER_DATA_FILE = "users.json"
REDEEM_CODES_FILE = "redeem_codes.json"
BANNED_USERS_FILE = "banned_users.json"
PREMIUM_USERS_FILE = "premium_users.json"
FREE_MODE_FILE = "free_mode.json"
USER_HISTORY_FILE = "user_history.json"
PROTECTED_NUMBERS_FILE = "protected_numbers.json"
ADMINS_FILE = "admins.json"
GLOBAL_FREE_MODE_FILE = "global_free_mode.json"
DAILY_LIMIT_FILE = "daily_free_limit.json"
AUTO_DELETE_TIME_FILE = "auto_delete_time.json"
MAINTENANCE_MODE_FILE = "maintenance_mode.json"

# --- Use values from config ---
BOT_TOKEN = config.BOT_TOKEN
ADMIN_IDS = config.ADMIN_IDS
SUPPORT_USERNAME = config.SUPPORT_USERNAME
REFERRAL_NOTIFICATION_GROUP = config.REFERRAL_NOTIFICATION_GROUP
LOG_CHANNEL_ID = config.LOG_CHANNEL_ID
OFFICIAL_GROUP_ID = config.OFFICIAL_GROUP_ID
OFFICIAL_GROUP_LINK = config.OFFICIAL_GROUP_LINK
CHANNEL_1_INVITE_LINK = config.CHANNEL_1_INVITE_LINK
REQUIRED_CHANNEL_1_ID = config.REQUIRED_CHANNEL_1_ID
CHANNEL_2_INVITE_LINK = config.CHANNEL_2_INVITE_LINK
REQUIRED_CHANNEL_2_ID = config.REQUIRED_CHANNEL_2_ID
PHONE_API_NEW = config.PHONE_API_NEW
INITIAL_CREDITS = config.INITIAL_CREDITS
REFERRAL_CREDITS = config.REFERRAL_CREDITS
NEW_USER_REFERRAL_CREDITS = config.NEW_USER_REFERRAL_CREDITS
SEARCH_COST = config.SEARCH_COST
REDEEM_COOLDOWN_SECONDS = config.REDEEM_COOLDOWN_SECONDS
REFERRAL_PREMIUM_DAYS = config.REFERRAL_PREMIUM_DAYS
REFERRAL_TIER_1_COUNT = config.REFERRAL_TIER_1_COUNT
REFERRAL_TIER_2_COUNT = config.REFERRAL_TIER_2_COUNT
DEFAULT_DAILY_LIMIT = config.DEFAULT_DAILY_LIMIT
DEFAULT_AUTO_DELETE_TIME = config.DEFAULT_AUTO_DELETE_TIME

# --- END OF CONFIGURATION ---

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- 💾 Data Management ---
def load_data(filename):
    try:
        with open(filename, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        if 'banned' in filename or 'premium' in filename or 'admins' in filename:
            return []
        if 'free_mode' in filename:
            return {"active": False}
        if 'global_free_mode' in filename:
            return {"active": False}
        if 'protected_numbers' in filename:
            return {}
        if 'daily_free_limit' in filename:
            return {"limit": DEFAULT_DAILY_LIMIT}
        if 'auto_delete_time' in filename:
            return {"seconds": DEFAULT_AUTO_DELETE_TIME}
        if 'maintenance_mode' in filename:
            return {"active": False}
        return {}

def save_data(data, filename):
    try:
        with open(filename, 'w') as f:
            json.dump(data, f, indent=4)
    except Exception as e:
        logger.error(f"Error saving data to {filename}: {e}")

# --- MAINTENANCE MODE FUNCTIONS ---
def is_maintenance_mode_active():
    data = load_data(MAINTENANCE_MODE_FILE)
    return data.get("active", False)

def set_maintenance_mode(status: bool):
    save_data({"active": status}, MAINTENANCE_MODE_FILE)

# --- AUTO-DELETE TIME FUNCTIONS ---
def get_auto_delete_time() -> int:
    """Get current auto-delete delay in seconds (0 = disabled)."""
    data = load_data(AUTO_DELETE_TIME_FILE)
    return data.get("seconds", DEFAULT_AUTO_DELETE_TIME)

def set_auto_delete_time(seconds: int) -> None:
    """Set new auto-delete delay."""
    save_data({"seconds": seconds}, AUTO_DELETE_TIME_FILE)

# --- DAILY FREE LIMIT FUNCTIONS ---
def get_daily_free_limit() -> int:
    """Get current daily free search limit from file."""
    data = load_data(DAILY_LIMIT_FILE)
    return data.get("limit", DEFAULT_DAILY_LIMIT)

def set_daily_free_limit(limit: int) -> None:
    """Set new daily free search limit."""
    save_data({"limit": limit}, DAILY_LIMIT_FILE)

# --- USER DAILY SEARCH TRACKING ---
def get_user_daily_data(user_id: int):
    """Return (daily_searches, last_date) for user, resetting if needed."""
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    if user_id_str not in user_data:
        user_data[user_id_str] = {
            "credits": 0,
            "referred_by": None,
            "redeemed_codes": [],
            "last_redeem_timestamp": 0,
            "referral_count": 0,
            "daily_searches": 0,
            "last_search_date": today
        }
        save_data(user_data, USER_DATA_FILE)
        return 0, today
    
    # Ensure fields exist
    if "daily_searches" not in user_data[user_id_str]:
        user_data[user_id_str]["daily_searches"] = 0
    if "last_search_date" not in user_data[user_id_str]:
        user_data[user_id_str]["last_search_date"] = today
    
    last_date = user_data[user_id_str]["last_search_date"]
    if last_date != today:
        # Reset daily count
        user_data[user_id_str]["daily_searches"] = 0
        user_data[user_id_str]["last_search_date"] = today
        save_data(user_data, USER_DATA_FILE)
    
    return user_data[user_id_str]["daily_searches"], today

async def can_use_daily_free(user_id: int) -> bool:
    """Check if user has free searches left today."""
    daily_count, _ = get_user_daily_data(user_id)
    limit = get_daily_free_limit()
    return daily_count < limit

async def increment_daily_searches(user_id: int) -> None:
    """Increment user's daily search count (resets if new day)."""
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    today = datetime.now().strftime("%Y-%m-%d")
    
    if user_id_str not in user_data:
        user_data[user_id_str] = {
            "credits": 0,
            "referred_by": None,
            "redeemed_codes": [],
            "last_redeem_timestamp": 0,
            "referral_count": 0,
            "daily_searches": 1,
            "last_search_date": today
        }
    else:
        # Reset if new day
        if user_data[user_id_str].get("last_search_date") != today:
            user_data[user_id_str]["daily_searches"] = 1
            user_data[user_id_str]["last_search_date"] = today
        else:
            user_data[user_id_str]["daily_searches"] = user_data[user_id_str].get("daily_searches", 0) + 1
    
    save_data(user_data, USER_DATA_FILE)

# --- GLOBAL FREE MODE FUNCTIONS ---
def is_global_free_mode_active():
    data = load_data(GLOBAL_FREE_MODE_FILE)
    return data.get("active", False)

def set_global_free_mode(status: bool):
    save_data({"active": status}, GLOBAL_FREE_MODE_FILE)

async def notify_global_free_mode_change_async(context: CallbackContext, status: bool, admin_name: str):
    """Notify all users about global free mode change asynchronously"""
    user_data = load_data(USER_DATA_FILE)
    
    if status:
        message = (
            "🎉 <b>GLOBAL FREE MODE ACTIVATED!</b>\n\n"
            f"👑 <b>Activated by:</b> {admin_name}\n\n"
            "✨ <b>All users can now search without using credits!</b>\n"
            "🔍 Unlimited searches for everyone\n"
            "💰 No credits will be deducted\n"
            "⏰ This mode will remain active until an admin disables it\n\n"
            "Enjoy unlimited searches! 🚀"
        )
    else:
        message = (
            "⚠️ <b>GLOBAL FREE MODE DEACTIVATED</b>\n\n"
            f"👑 <b>Deactivated by:</b> {admin_name}\n\n"
            "💡 <b>Credit system is now active again</b>\n"
            "💰 Each search will deduct 1 credit\n"
            "🔗 Use referrals to earn more credits\n"
            "⭐ Consider buying premium for unlimited access\n\n"
            "Thank you for using our bot! ❤️"
        )
    
    # Start notification in background without waiting
    asyncio.create_task(send_notifications_to_users(context, user_data.keys(), message))
    
    # Return immediately with estimated counts
    return len(user_data), 0

async def send_notifications_to_users(context: CallbackContext, user_ids, message):
    """Send notifications to users in background"""
    success_count = 0
    fail_count = 0
    
    for user_id_str in user_ids:
        try:
            await context.bot.send_message(
                chat_id=int(user_id_str), 
                text=message, 
                parse_mode=ParseMode.HTML
            )
            success_count += 1
            await asyncio.sleep(0.05)  # Reduced delay for faster processing
        except Exception as e:
            if "Chat not found" in str(e) or "bot was blocked" in str(e):
                logger.info(f"User {user_id_str} blocked the bot or chat not found")
                fail_count += 1
            else:
                logger.warning(f"Failed to notify user {user_id_str}: {e}")
                fail_count += 1
    
    logger.info(f"Background notification completed: Success={success_count}, Failed={fail_count}")

async def add_credits_to_all_users_async(context: CallbackContext, credits: int, admin_name: str):
    """Add credits to all users asynchronously"""
    user_data = load_data(USER_DATA_FILE)
    
    success_count = 0
    fail_count = 0
    
    for user_id_str, data in user_data.items():
        try:
            if "credits" not in data:
                data["credits"] = 0
            data["credits"] += credits
            success_count += 1
            
            # Notify individual user in background
            try:
                user_id = int(user_id_str)
                message = (
                    f"🎁 <b>Credits Gift from Admin!</b>\n\n"
                    f"👑 <b>From:</b> {admin_name}\n"
                    f"💰 <b>Credits Received:</b> {credits}\n"
                    f"💳 <b>Your New Balance:</b> {data['credits']}\n\n"
                    f"Thank you for using our bot! ❤️"
                )
                asyncio.create_task(
                    safe_send_message(context, user_id, message)
                )
            except Exception as e:
                logger.warning(f"Failed to schedule notification for user {user_id_str}: {e}")
                
        except Exception as e:
            logger.error(f"Failed to add credits to user {user_id_str}: {e}")
            fail_count += 1
    
    save_data(user_data, USER_DATA_FILE)
    
    # Log to admin
    summary_message = (
        f"📊 <b>Bulk Credit Distribution Complete</b>\n\n"
        f"👑 <b>Admin:</b> {admin_name}\n"
        f"💰 <b>Credits per user:</b> {credits}\n"
        f"✅ <b>Success:</b> {success_count} users\n"
        f"❌ <b>Failed:</b> {fail_count} users\n"
        f"👥 <b>Total users:</b> {len(user_data)}\n\n"
        f"Notifications are being sent to users in the background."
    )
    
    return summary_message

async def safe_send_message(context: CallbackContext, user_id: int, message: str):
    """Safely send message with error handling"""
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        if "Chat not found" in str(e) or "bot was blocked" in str(e):
            logger.info(f"User {user_id} blocked the bot")
        else:
            logger.warning(f"Failed to send message to user {user_id}: {e}")

# --- DOWNLOAD FILE FUNCTIONS ---
def create_safe_filename(query: str, search_type: str, bot_username: str) -> str:
    """Create a safe filename from query"""
    safe_query = re.sub(r'[<>:"/\\|?*]', '_', str(query))
    safe_query = safe_query[:50]
    filename = f"{search_type}_{safe_query} @{bot_username}.txt"
    return filename

def create_search_result_file(result_text: str, query: str, search_type: str, bot_username: str) -> BytesIO:
    """Create a text file with search results"""
    clean_text = re.sub(r'<[^>]+>', '', result_text)
    clean_text = html.unescape(clean_text)
    
    file_content = f"Search Query: {query}\n"
    file_content += f"Search Type: {search_type}\n"
    file_content += f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    file_content += f"Bot: @{bot_username}\n"
    file_content += "=" * 50 + "\n\n"
    file_content += clean_text
    
    file_bytes = BytesIO(file_content.encode('utf-8'))
    file_bytes.name = create_safe_filename(query, search_type, bot_username)
    return file_bytes

# --- ADDRESS FORMATTING FUNCTION ---
def format_address(address: str) -> str:
    """Format address to remove excessive spaces and newlines"""
    if not address or address == 'N/A':
        return 'N/A'
    
    # Replace multiple spaces and newlines with single space
    address = re.sub(r'\s+', ' ', address.strip())
    
    # Remove duplicate words or phrases
    words = address.split()
    unique_words = []
    for word in words:
        if word not in unique_words:
            unique_words.append(word)
    
    formatted_address = ' '.join(unique_words)
    return formatted_address

# --- PHONE NUMBER NORMALIZATION ---
def normalize_phone_number(text: str) -> str | None:
    """
    Extract a valid 10-digit Indian mobile number from various input formats.
    Returns cleaned 10-digit number or None if invalid.
    """
    # Remove all non-digit characters
    digits = re.sub(r'\D', '', text)
    
    # Check if it's a valid Indian number
    if len(digits) == 10:
        return digits
    elif len(digits) == 12 and digits.startswith('91'):
        return digits[2:]
    elif len(digits) == 11 and digits.startswith('0'):
        # Some people add a leading zero
        return digits[1:] if len(digits[1:]) == 10 else None
    elif len(digits) > 10:
        # Try to take the last 10 digits
        last10 = digits[-10:]
        return last10
    else:
        return None

# --- ADMIN MANAGEMENT FUNCTIONS ---
def is_owner(user_id: int) -> bool:
    """Check if user is an owner (hardcoded admin)"""
    return user_id in ADMIN_IDS

def is_admin(user_id: int) -> bool:
    """Check if user is an admin (owner or sub-admin)"""
    if is_owner(user_id):
        return True
    
    admins = load_data(ADMINS_FILE)
    return user_id in admins

def add_admin(user_id: int, added_by: int) -> bool:
    """Add a new admin"""
    if is_admin(user_id):
        return False
    
    admins = load_data(ADMINS_FILE)
    admins.append(user_id)
    save_data(admins, ADMINS_FILE)
    
    log_user_action(added_by, "Added Admin", f"New admin: {user_id}")
    return True

def remove_admin(user_id: int, removed_by: int) -> bool:
    """Remove an admin (cannot remove owners)"""
    if is_owner(user_id):
        return False
    
    admins = load_data(ADMINS_FILE)
    if user_id in admins:
        admins.remove(user_id)
        save_data(admins, ADMINS_FILE)
        
        log_user_action(removed_by, "Removed Admin", f"Removed admin: {user_id}")
        return True
    
    return False

def get_all_admins() -> list:
    """Get all admins (owners + sub-admins)"""
    owners = ADMIN_IDS
    sub_admins = load_data(ADMINS_FILE)
    return owners + sub_admins

def get_admin_list_text() -> str:
    """Get formatted text of all admins"""
    owners = ADMIN_IDS
    sub_admins = load_data(ADMINS_FILE)
    
    text = "👑 <b>Admin List</b>\n\n"
    
    text += "🏆 <b>Owners (Cannot be removed):</b>\n"
    for i, owner_id in enumerate(owners, 1):
        text += f"{i}. <code>{owner_id}</code>\n"
    
    text += "\n👨‍💼 <b>Sub-Admins:</b>\n"
    if sub_admins:
        for i, admin_id in enumerate(sub_admins, 1):
            text += f"{i}. <code>{admin_id}</code>\n"
    else:
        text += "No sub-admins added.\n"
    
    return text

async def notify_new_admin(context: CallbackContext, user_id: int, added_by: int):
    """Notify user they've been made admin"""
    try:
        message = (
            "🎉 <b>You've been promoted to Admin!</b>\n\n"
            "You now have full access to the bot's admin panel.\n\n"
            "Use the Admin Panel button to manage the bot."
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not notify new admin {user_id}: {e}")

async def notify_removed_admin(context: CallbackContext, user_id: int, removed_by: int):
    """Notify user they've been removed as admin"""
    try:
        message = (
            "⚠️ <b>Admin Access Removed</b>\n\n"
            "Your admin privileges have been removed from the bot."
        )
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not notify removed admin {user_id}: {e}")

# --- PHONE NUMBER PROTECTION FUNCTIONS ---
def is_number_protected(number: str) -> bool:
    """Check if a number is protected"""
    protected_numbers = load_data(PROTECTED_NUMBERS_FILE)
    return number in protected_numbers

def get_protection_message(number: str) -> str:
    """Get the custom message for a protected number"""
    protected_numbers = load_data(PROTECTED_NUMBERS_FILE)
    if number in protected_numbers:
        return protected_numbers[number].get("message", "❌ No data found for this number.")
    return "❌ No data found for this number."

def protect_number(number: str, admin_id: int, custom_message: str = None) -> bool:
    """Protect a number with optional custom message"""
    protected_numbers = load_data(PROTECTED_NUMBERS_FILE)
    
    if number in protected_numbers:
        return False
    
    protected_numbers[number] = {
        "protected_by": admin_id,
        "protected_at": datetime.now().isoformat(),
        "message": custom_message or "❌ No data found for this number."
    }
    
    save_data(protected_numbers, PROTECTED_NUMBERS_FILE)
    return True

def unprotect_number(number: str) -> bool:
    """Remove protection from a number"""
    protected_numbers = load_data(PROTECTED_NUMBERS_FILE)
    
    if number in protected_numbers:
        del protected_numbers[number]
        save_data(protected_numbers, PROTECTED_NUMBERS_FILE)
        return True
    
    return False

def get_all_protected_numbers() -> dict:
    """Get all protected numbers"""
    return load_data(PROTECTED_NUMBERS_FILE)

# --- COMMON FUNCTIONS ---
def is_free_mode_active():
    return load_data(FREE_MODE_FILE).get("active", False)

def set_free_mode(status: bool):
    save_data({"active": status}, FREE_MODE_FILE)

def log_user_action(user_id, action, details=""):
    history = load_data(USER_HISTORY_FILE)
    user_id_str = str(user_id)
    if user_id_str not in history:
        history[user_id_str] = []
    
    log_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "action": action,
        "details": details
    }
    history[user_id_str].insert(0, log_entry)
    history[user_id_str] = history[user_id_str][:50]
    save_data(history, USER_HISTORY_FILE)

async def notify_admin_new_user(context: CallbackContext, user, total_users: int):
    user_id = user.id
    user_name = user.first_name or "Unknown"
    user_username = f"@{user.username}" if user.username else "No username"
    
    profile_link = f"tg://user?id={user_id}"
    
    message = (
        "🆕 <b>New User Started the Bot!</b>\n\n"
        f"👤 <b>User:</b> <a href='{profile_link}'>{user_name}</a>\n"
        f"🆔 <b>ID:</b> <code>{user_id}</code>\n"
        f"📛 <b>Username:</b> {user_username}\n"
        f"👥 <b>Total Users:</b> {total_users}\n"
        f"⏰ <b>Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    )
    
    message += f"\n<a href='{profile_link}'>💬 Send Message to User</a>"
    
    for admin_id in get_all_admins():
        try:
            await context.bot.send_message(
                chat_id=admin_id,
                text=message,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True
            )
        except Exception as e:
            logger.error(f"Failed to send new user notification to admin {admin_id}: {e}")

async def log_search_to_channel(context: CallbackContext, user, search_type: str, query: str, result: str = "", success: bool = True, chat_id: int = None):
    """Log search activity to channel with proper formatting"""
    try:
        user_id = user.id
        user_name = user.first_name or "Unknown"
        user_username = f"@{user.username}" if user.username else "No username"
        
        profile_link = f"tg://user?id={user_id}"
        
        status_emoji = "✅" if success else "❌"
        
        # Create log message header
        message = (
            f"{status_emoji} <b>Search Activity Log</b>\n\n"
            f"<b>👤 User:</b> {user_name}\n"
            f"<b>🆔 ID:</b> <code>{user_id}</code>\n"
            f"<b>📛 Username:</b> {user_username}\n"
            f"<b>🔍 Search Type:</b> {search_type}\n"
            f"<b>📝 Query:</b> <code>{query}</code>\n"
            f"<b>⏰ Time:</b> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        )
        
        # Add group info if search was from official group
        if chat_id and is_official_group(chat_id):
            message += f"<b>🌐 Location:</b> Official Group\n\n"
        else:
            message += f"<b>🌐 Location:</b> Private Chat\n\n"
        
        if result:
            # Truncate result to avoid timeout
            truncated_result = result[:500] + "..." if len(result) > 500 else result
            message += f"<b>📄 Result Preview:</b>\n{truncated_result}\n\n"
        
        message += f"💞<b>Developer: @ll_VIPIN_ll</b>"
        
        # Send to log channel
        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=message,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )
        
    except Exception as e:
        logger.error(f"Failed to log search to channel: {e}")

async def notify_user(context: CallbackContext, user_id: int, message: str):
    try:
        await context.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.HTML
        )
        return True
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")
        return False

async def notify_premium_added(context: CallbackContext, user_id: int, days: int = None):
    if days:
        message = f"🎉 <b>Premium Activated!</b>\n\n⭐ You have been granted <b>{days} days</b> of premium access!\n\n✨ Enjoy unlimited searches and premium features!"
    else:
        message = f"🎉 <b>Premium Activated!</b>\n\n⭐ You have been granted <b>permanent premium</b> access!\n\n✨ Enjoy unlimited searches and premium features forever!"
    
    await notify_user(context, user_id, message)

async def notify_premium_removed(context: CallbackContext, user_id: int):
    message = "⚠️ <b>Premium Access Removed</b>\n\n⭐ Your premium access has been removed.\n\n💡 You can still use the bot with credits or purchase premium again."
    await notify_user(context, user_id, message)

async def notify_premium_expired(context: CallbackContext, user_id: int):
    message = "⏰ <b>Premium Expired</b>\n\n⭐ Your premium access has expired.\n\n💡 You can still use the bot with credits or purchase premium again to continue enjoying unlimited searches."
    await notify_user(context, user_id, message)

async def notify_credits_added(context: CallbackContext, user_id: int, credits: int, new_balance: int):
    message = f"💰 <b>Credits Added!</b>\n\n➕ <b>{credits} credits</b> have been added to your account.\n\n💳 Your new balance: <b>{new_balance} credits</b>"
    await notify_user(context, user_id, message)

async def notify_credits_removed(context: CallbackContext, user_id: int, credits: int, new_balance: int):
    message = f"💰 <b>Credits Removed</b>\n\n➖ <b>{credits} credits</b> have been removed from your account.\n\n💳 Your new balance: <b>{new_balance} credits</b>"
    await notify_user(context, user_id, message)

async def is_banned(user_id: int) -> bool:
    banned_users = load_data(BANNED_USERS_FILE)
    return user_id in banned_users

async def is_premium(user_id: int) -> bool:
    premium_users = load_data(PREMIUM_USERS_FILE)
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_id in premium_users:
        return True
    
    if user_id_str in user_data:
        user_info = user_data[user_id_str]
        if "premium_until" in user_info:
            premium_until = datetime.fromisoformat(user_info["premium_until"])
            if datetime.now() < premium_until:
                return True
            else:
                asyncio.create_task(notify_premium_expired(None, user_id))
                del user_data[user_id_str]["premium_until"]
                save_data(user_data, USER_DATA_FILE)
    
    return False

def add_premium_days(user_id: int, days: int):
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_id_str not in user_data:
        user_data[user_id_str] = {"credits": INITIAL_CREDITS, "referred_by": None, "redeemed_codes": [], "last_redeem_timestamp": 0, "referral_count": 0}
    
    premium_until = datetime.now() + timedelta(days=days)
    user_data[user_id_str]["premium_until"] = premium_until.isoformat()
    save_data(user_data, USER_DATA_FILE)

def add_referral_credit(user_id: int, credits: int):
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_id_str in user_data:
        user_data[user_id_str]["credits"] += credits
        save_data(user_data, USER_DATA_FILE)

def increment_referral_count(user_id: int):
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_id_str in user_data:
        if "referral_count" not in user_data[user_id_str]:
            user_data[user_id_str]["referral_count"] = 0
        user_data[user_id_str]["referral_count"] += 1
        save_data(user_data, USER_DATA_FILE)
        return user_data[user_id_str]["referral_count"]
    return 0

def get_referral_count(user_id: int) -> int:
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_id_str in user_data:
        return user_data[user_id_str].get("referral_count", 0)
    return 0

async def check_membership(user_id: int, channel_id: int, context: CallbackContext) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=channel_id, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception as e:
        logger.error(f"Error checking membership for user {user_id} in channel {channel_id}: {e}")
        return False

async def is_subscribed(user_id: int, context: CallbackContext) -> bool:
    subscribed_to_1 = await check_membership(user_id, REQUIRED_CHANNEL_1_ID, context)
    subscribed_to_2 = await check_membership(user_id, REQUIRED_CHANNEL_2_ID, context)
    return subscribed_to_1 and subscribed_to_2

async def send_join_message(update: Update, context: CallbackContext):
    """Send message asking user to join channels"""
    if update.callback_query:
        message = update.callback_query.message
    else:
        message = update.message
    
    keyboard = [
        [InlineKeyboardButton("➡️ Join Channel 1", url=CHANNEL_1_INVITE_LINK)],
        [InlineKeyboardButton("➡️ Join Channel 2", url=CHANNEL_2_INVITE_LINK)],
        [InlineKeyboardButton("✅ Verify", callback_data='verify_join')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    message_text = (
        "❌ <b>You must join both channels to use this bot!</b>\n\n"
        "Please join both channels below and then click Verify."
    )
    
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(message_text, reply_markup=reply_markup, parse_mode=ParseMode.HTML)

async def check_and_require_subscription(update: Update, context: CallbackContext, user_id: int) -> bool:
    """Check if user is subscribed to channels, if not, send join message"""
    if not await is_subscribed(user_id, context):
        await send_join_message(update, context)
        return False
    return True

async def deduct_credits(user_id: int, chat_id: int = None, cost: int = SEARCH_COST) -> bool:
    if chat_id == OFFICIAL_GROUP_ID:
        return True
        
    # Check global free mode
    if is_global_free_mode_active():
        return True
        
    if is_free_mode_active(): 
        return True
    if is_admin(user_id) or await is_premium(user_id): 
        return True
    
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_data.get(user_id_str, {}).get("referral_count", 0) >= REFERRAL_TIER_2_COUNT:
        return True
        
    if user_data.get(user_id_str, {}).get("credits", 0) >= cost:
        user_data[user_id_str]["credits"] -= cost
        save_data(user_data, USER_DATA_FILE)
        return True
    return False
    
def get_info_footer(user_id: int, chat_id: int = None) -> str:
    if chat_id == OFFICIAL_GROUP_ID:
        return "\n\n🚀 <b>Official Group Mode:</b> No credits were used for this search!"
    
    # Check global free mode
    if is_global_free_mode_active():
        return "\n\n🌍 <b>Global Free Mode ACTIVE!</b> No credits were used for this search!"
    
    if is_free_mode_active():
        return "\n\n✨ <b>Free Mode is ACTIVE!</b> No credits were used for this search."
    
    user_data = load_data(USER_DATA_FILE)
    credits = user_data.get(str(user_id), {}).get("credits", 0)
    
    if is_admin(user_id):
        return f"\n\n💰 Credits Remaining: <b>{credits}</b> | 👑 Admin User"
    
    premium_users = load_data(PREMIUM_USERS_FILE)
    if user_id in premium_users:
        return f"\n\n💰 Credits Remaining: <b>{credits}</b> | ⭐ Premium User"
    else:
        user_info = user_data.get(str(user_id), {})
        if "premium_until" in user_info:
            premium_until = datetime.fromisoformat(user_info["premium_until"])
            if datetime.now() < premium_until:
                time_left = premium_until - datetime.now()
                hours_left = int(time_left.total_seconds() / 3600)
                return f"\n\n💰 Credits Remaining: <b>{credits}</b> | ⭐ Premium ({hours_left}h left)"
    
    return f"\n\n💰 Credits Remaining: <b>{credits}</b>"

async def notify_referral_success(context: CallbackContext, referrer_id: int, new_user_name: str, referral_count: int, new_user_credits: int, referrer_credits: int):
    try:
        message = f"🎉 <b>New Referral Success!</b>\n\n👤 {new_user_name} joined using your link!\n\n"
        message += f"✅ You've received <b>{REFERRAL_CREDITS} credits</b>\n"
        message += f"👤 New user received <b>{NEW_USER_REFERRAL_CREDITS} credits</b>\n"
        message += f"💰 Your new balance: <b>{referrer_credits} credits</b>\n"
        message += f"📊 Total referrals: <b>{referral_count}</b>\n\n"
        
        if referral_count == REFERRAL_TIER_1_COUNT:
            message += f"⭐ <b>BONUS UNLOCKED!</b> You've reached {REFERRAL_TIER_1_COUNT} referrals and earned <b>1 day premium access</b>! 🚀\n\nYou now have unlimited searches for 24 hours!"
        elif referral_count == REFERRAL_TIER_2_COUNT:
            message += f"♾️ <b>MEGA BONUS UNLOCKED!</b> You've reached {REFERRAL_TIER_2_COUNT} referrals and earned <b>UNLIMITED CREDITS FOREVER</b>! 🎊\n\nYou now have unlimited searches permanently!"
        
        await context.bot.send_message(
            chat_id=referrer_id,
            text=message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not notify referrer {referrer_id}: {e}")

async def notify_new_user_referral(context: CallbackContext, new_user_id: int, credits_received: int, total_credits: int):
    try:
        message = f"🎉 <b>Welcome Bonus!</b>\n\n"
        message += f"💰 You received <b>{credits_received} credits</b> for joining via referral!\n"
        message += f"💳 Your total credits: <b>{total_credits}</b>\n\n"
        message += f"🔍 Start searching now with the India Number button!"
        
        await context.bot.send_message(
            chat_id=new_user_id,
            text=message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not notify new user {new_user_id}: {e}")

async def notify_admin_group(context: CallbackContext, referrer_name: str, new_user_name: str, referral_count: int, new_user_credits: int, referrer_credits: int):
    try:
        message = f"📈 <b>New Referral Activity</b>\n\n"
        message += f"👤 <b>Referrer:</b> {referrer_name}\n"
        message += f"🆕 <b>New User:</b> {new_user_name}\n"
        message += f"💰 <b>Credits to Referrer:</b> {REFERRAL_CREDITS} (Total: {referrer_credits})\n"
        message += f"💰 <b>Credits to New User:</b> {NEW_USER_REFERRAL_CREDITS} (Total: {new_user_credits})\n"
        message += f"📊 <b>Total Referrals:</b> {referral_count}\n"
        
        if referral_count >= REFERRAL_TIER_2_COUNT:
            message += f"\n🎉 <b>MILESTONE REACHED!</b> User now has UNLIMITED CREDITS! 🚀"
        elif referral_count >= REFERRAL_TIER_1_COUNT:
            message += f"\n⭐ <b>Premium Unlocked!</b> User now has 1-day premium access!"
        
        await context.bot.send_message(
            chat_id=REFERRAL_NOTIFICATION_GROUP,
            text=message,
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.warning(f"Could not notify admin group: {e}")

def is_official_group(chat_id: int) -> bool:
    return chat_id == OFFICIAL_GROUP_ID

async def send_restricted_message(update: Update):
    message_text = (
        "❌ <b>This bot only works in the official group and private chat!</b>\n\n"
        f"🚀 <b>Official Group:</b> {OFFICIAL_GROUP_LINK}\n\n"
        "💡 <b>Note:</b> Join our official group for unlimited free searches!"
    )
    await update.message.reply_text(message_text, parse_mode=ParseMode.HTML)

async def add_credits_to_user(user_id: int, credits: int, context: CallbackContext = None):
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_id_str not in user_data:
        user_data[user_id_str] = {"credits": 0, "referred_by": None, "redeemed_codes": [], "last_redeem_timestamp": 0, "referral_count": 0}
    
    user_data[user_id_str]["credits"] += credits
    save_data(user_data, USER_DATA_FILE)
    
    if context:
        new_balance = user_data[user_id_str]["credits"]
        await notify_credits_added(context, user_id, credits, new_balance)
    
    return user_data[user_id_str]["credits"]

async def remove_credits_from_user(user_id: int, credits: int, context: CallbackContext = None):
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    if user_id_str in user_data:
        user_data[user_id_str]["credits"] = max(0, user_data[user_id_str]["credits"] - credits)
        save_data(user_data, USER_DATA_FILE)
        
        if context:
            new_balance = user_data[user_id_str]["credits"]
            await notify_credits_removed(context, user_id, credits, new_balance)
        
        return user_data[user_id_str]["credits"]
    return 0

async def add_user_to_premium(user_id: int, context: CallbackContext = None, days: int = None):
    premium_users = load_data(PREMIUM_USERS_FILE)
    if user_id not in premium_users:
        premium_users.append(user_id)
        save_data(premium_users, PREMIUM_USERS_FILE)
        
        if days:
            add_premium_days(user_id, days)
        
        if context:
            await notify_premium_added(context, user_id, days)
        
        return True
    return False

async def remove_user_from_premium(user_id: int, context: CallbackContext = None):
    premium_users = load_data(PREMIUM_USERS_FILE)
    if user_id in premium_users:
        premium_users.remove(user_id)
        save_data(premium_users, PREMIUM_USERS_FILE)
        
        user_data = load_data(USER_DATA_FILE)
        user_id_str = str(user_id)
        if user_id_str in user_data and "premium_until" in user_data[user_id_str]:
            del user_data[user_id_str]["premium_until"]
            save_data(user_data, USER_DATA_FILE)
        
        if context:
            await notify_premium_removed(context, user_id)
        
        return True
    return False

def ban_user(user_id: int):
    banned_users = load_data(BANNED_USERS_FILE)
    if user_id not in banned_users:
        banned_users.append(user_id)
        save_data(banned_users, BANNED_USERS_FILE)
        return True
    return False

def unban_user(user_id: int):
    banned_users = load_data(BANNED_USERS_FILE)
    if user_id in banned_users:
        banned_users.remove(user_id)
        save_data(banned_users, BANNED_USERS_FILE)
        return True
    return False

async def broadcast_message(context: CallbackContext, message: str):
    user_data = load_data(USER_DATA_FILE)
    success_count = 0
    fail_count = 0
    
    for user_id_str in user_data.keys():
        try:
            await context.bot.send_message(chat_id=int(user_id_str), text=message, parse_mode=ParseMode.HTML)
            success_count += 1
        except Exception as e:
            logger.error(f"Failed to send broadcast to {user_id_str}: {e}")
            fail_count += 1
    
    return success_count, fail_count

# --- AUTO-DELETE CALLBACK ---
async def delete_message(context: CallbackContext):
    """Job callback to delete a message after delay."""
    job = context.job
    chat_id = job.data['chat_id']
    message_id = job.data['message_id']
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.warning(f"Failed to auto-delete message {message_id}: {e}")

# ==================== MAINTENANCE MODE CHECK ====================
async def check_maintenance(update: Update, context: CallbackContext) -> bool:
    """If maintenance mode is on and user is not admin, send message and return False."""
    if is_maintenance_mode_active() and not is_admin(update.effective_user.id):
        msg = (
            "⚠️ <b>Maintenance Mode is Active</b>\n\n"
            "The bot is currently under maintenance. Please try again later.\n\n"
            "Thank you for your patience! 🙏"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.HTML)
        return False
    return True

# ==================== NEW USER PROCESSING FUNCTION (FIXED REFERRAL) ====================

async def process_new_user(context: CallbackContext, user_id: int, chat_id: int, referrer_id: int = None) -> None:
    """Create a new user account and process referral if any."""
    user_data = load_data(USER_DATA_FILE)
    user_id_str = str(user_id)
    
    # If user already exists, do nothing
    if user_id_str in user_data:
        return
    
    # Determine initial credits
    initial_credits = NEW_USER_REFERRAL_CREDITS if referrer_id else INITIAL_CREDITS
    
    # Create new user entry
    user_data[user_id_str] = {
        "credits": initial_credits,
        "referred_by": referrer_id,
        "redeemed_codes": [],
        "last_redeem_timestamp": 0,
        "referral_count": 0,
        "daily_searches": 0,
        "last_search_date": datetime.now().strftime("%Y-%m-%d")
    }
    
    # If there is a referrer, update referrer's data
    if referrer_id:
        referrer_str = str(referrer_id)
        if referrer_str in user_data:
            # Add credits to referrer
            user_data[referrer_str]["credits"] += REFERRAL_CREDITS
            # Increment referral count
            if "referral_count" not in user_data[referrer_str]:
                user_data[referrer_str]["referral_count"] = 0
            user_data[referrer_str]["referral_count"] += 1
            new_referral_count = user_data[referrer_str]["referral_count"]
            # Check for tier rewards
            if new_referral_count == REFERRAL_TIER_1_COUNT:
                premium_until = datetime.now() + timedelta(days=REFERRAL_PREMIUM_DAYS)
                user_data[referrer_str]["premium_until"] = premium_until.isoformat()
            # No need to handle tier 2 here (unlimited credits) because that's just a status based on count, not a separate field.
    
    # Save all changes
    save_data(user_data, USER_DATA_FILE)
    
    # Get user object for notifications
    try:
        user_obj = await context.bot.get_chat(user_id)
    except Exception as e:
        logger.warning(f"Could not fetch user chat for {user_id}: {e}")
        user_obj = type('User', (), {'id': user_id, 'first_name': 'User', 'username': None})()
    
    # Send notifications
    if referrer_id and referrer_str in user_data:
        # Notify new user about referral bonus
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎉 You joined using a referral link! You received <b>{NEW_USER_REFERRAL_CREDITS} credits</b> and your referrer has been rewarded with {REFERRAL_CREDITS} credits.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.warning(f"Could not notify new user about referral: {e}")
        
        # Notify referrer
        try:
            referrer_credits = user_data[referrer_str]["credits"]
            referrer_chat = await context.bot.get_chat(referrer_id)
            referrer_name = referrer_chat.first_name or f"User {referrer_id}"
            await notify_referral_success(context, referrer_id, user_obj.first_name, new_referral_count, NEW_USER_REFERRAL_CREDITS, referrer_credits)
            await notify_admin_group(context, referrer_name, user_obj.first_name, new_referral_count, NEW_USER_REFERRAL_CREDITS, referrer_credits)
        except Exception as e:
            logger.warning(f"Could not notify referrer: {e}")
    else:
        # Normal new user welcome
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"🎉 Welcome! You have received {initial_credits} free credits to get started.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            logger.warning(f"Could not notify new user: {e}")
    
    # Log user action
    log_user_action(user_id, "Joined", f"Referred by: {referrer_id}, Initial credits: {initial_credits}")
    
    # Notify admins about new user
    total_users = len(user_data)
    await notify_admin_new_user(context, user_obj, total_users)

# ==================== KEYBOARD DEFINITIONS ====================

def get_main_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    """Main menu keyboard for regular users"""
    keyboard = [
        ["India Number 🇮🇳"],
        ["Check Credit 💰", "Get Referral Link 🔗"],
        ["Redeem Code 🎁", "Buy Premium & Credits 💎"],
        ["Support 👨‍💻", "Official Group 🚀"],
        ["Privacy Policy 🔒"]
    ]
    if is_admin(user_id):
        keyboard.append(["Admin Panel 👑"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_keyboard() -> ReplyKeyboardMarkup:
    """Admin main menu keyboard"""
    keyboard = [
        ["Add Credits ➕", "Remove Credits ➖"],
        ["Add Premium ⭐", "Remove Premium ⭐➖"],
        ["Add Credits to All 💰👥", "User History 📝"],
        ["Broadcast 📢", "Premium List 📋"],
        ["Block User 🚫", "Unblock User ✅"],
        ["Blocked List 📋🚫", "Bot Stats 📊"],
        ["Generate Code 🎁", "Toggle Group Free 🎯"],
        ["Toggle Global Free 🌍", "Set Daily Limit 🔢"],
        ["Referral Stats 📈", "Number Protection 🛡️"],
        ["Admin Management 👨‍💼"],
        ["Auto-Delete Time ⏱️", "Maintenance Mode ⚠️"],
        ["Back to Main 🔙"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_number_protection_keyboard() -> ReplyKeyboardMarkup:
    """Number protection submenu keyboard"""
    keyboard = [
        ["Protect Number ➕🛡️", "Unprotect Number ➖🛡️"],
        ["Protected List 📋🛡️"],
        ["Back to Admin 🔙"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_admin_management_keyboard() -> ReplyKeyboardMarkup:
    """Admin management submenu (owners only)"""
    keyboard = [
        ["Add Admin ➕👨‍💼", "Remove Admin ➖👨‍💼"],
        ["Admin List 📋👨‍💼"],
        ["Back to Admin 🔙"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

def get_buy_keyboard() -> ReplyKeyboardMarkup:
    """Buy menu keyboard"""
    keyboard = [
        ["Premium Plans ⭐", "Credit Packages 💰"],
        ["Back to Main 🔙"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# ==================== COMMAND HANDLERS ====================

async def start(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    chat = update.effective_chat
    
    if not await check_maintenance(update, context):
        return

    if await is_banned(user.id): 
        return

    if chat.type != 'private' and not is_official_group(chat.id):
        await send_restricted_message(update)
        return

    if chat.type != 'private' and is_official_group(chat.id):
        base_caption = (
            "🚀 <b>Welcome to Phone Info Bot - Official Group Mode</b>\n\n"
            "🔍 <b>Available Commands:</b>\n"
            "• <code>/phone 9876543210</code> - Indian Phone Number 🇮🇳\n"
            "• <code>/help</code> - Show this help message\n\n"
            "💡 <b>Note:</b> In this group, you have <b>UNLIMITED FREE SEARCHES</b>! No credits required.\n\n"
            "⚠️ <b>Important:</b> For personal use with credit system, use the bot in private chat."
        )
        
        keyboard = [
            [
                InlineKeyboardButton("India Number 🇮🇳", callback_data='search_phone')
            ],
            [
                InlineKeyboardButton("Private Bot 🤖", url=f"https://t.me/{(await context.bot.get_me()).username}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await update.message.reply_text(base_caption, reply_markup=reply_markup, parse_mode=ParseMode.HTML)
        return

    if chat.type == 'private':
        daily_limit = get_daily_free_limit()
        user_id_str = str(user.id)
        user_data = load_data(USER_DATA_FILE)
        
        if user_id_str in user_data:
            base_caption = (
                f"I am your advanced Phone Information bot. Here's what you can do:\n\n"
                f"🔍 <b>Lookups:</b> Indian Phone Number information\n\n"
                f"💰 <b>Credit System:</b> You start with free credits. Each search costs one credit.\n\n"
                f"🎁 <b>Daily Free Searches:</b> You can search <b>{daily_limit} numbers</b> for free every day!\n\n"
                f"🔗 <b>Referrals:</b> Share your link to earn more credits and get 1 day premium access!\n\n"
                f"👥 <b>Group Unlimited:</b> Join our group for unlimited searches!\n\n"
                f"🔍 <b>Available Commands:</b>\n"
                f"• <code>/phone 9876543210</code>\n"
                f"• <code>/help</code> - Show this help message\n\n"
            )
            final_caption = f"<b>👋 Welcome back, {user.first_name}!</b>\n\n{base_caption}"
            context.user_data['menu_level'] = 'main'
            await update.message.reply_text(
                text=final_caption,
                reply_markup=get_main_keyboard(user.id),
                parse_mode=ParseMode.HTML
            )
            return
        
        referrer_id = None
        if context.args and context.args[0].isdigit():
            potential_referrer_id = int(context.args[0])
            if potential_referrer_id != user.id and str(potential_referrer_id) in user_data:
                referrer_id = potential_referrer_id
                context.user_data['pending_referral'] = referrer_id
        
        if not await is_subscribed(user.id, context):
            await send_join_message(update, context)
            return
        
        await process_new_user(context, user.id, chat.id, referrer_id)
        context.user_data.pop('pending_referral', None)
        
        base_caption = (
            f"I am your advanced Phone Information bot. Here's what you can do:\n\n"
            f"🔍 <b>Lookups:</b> Indian Phone Number information\n\n"
            f"💰 <b>Credit System:</b> You start with free credits. Each search costs one credit.\n\n"
            f"🎁 <b>Daily Free Searches:</b> You can search <b>{daily_limit} numbers</b> for free every day!\n\n"
            f"🔗 <b>Referrals:</b> Share your link to earn more credits and get 1 day premium access!\n\n"
            f"👥 <b>Group Unlimited:</b> Join our group for unlimited searches!\n\n"
            f"🔍 <b>Available Commands:</b>\n"
            f"• <code>/phone 9876543210</code>\n"
            f"• <code>/help</code> - Show this help message\n\n"
        )
        final_caption = f"<b>🎉 Welcome, {user.first_name}!</b>\n\n{base_caption}"
        context.user_data['menu_level'] = 'main'
        await update.message.reply_text(
            text=final_caption,
            reply_markup=get_main_keyboard(user.id),
            parse_mode=ParseMode.HTML
        )

async def help_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    chat = update.effective_chat
    
    if not await check_maintenance(update, context):
        return
    
    if chat.type != 'private' and not is_official_group(chat.id):
        await send_restricted_message(update)
        return
    
    if await is_banned(user.id):
        return
    
    if chat.type == 'private':
        if not await check_and_require_subscription(update, context, user.id):
            return
    
    daily_limit = get_daily_free_limit()
    
    if chat.type != 'private' and is_official_group(chat.id):
        help_text = (
            "🚀 <b>Phone Info Bot - Official Group Mode</b>\n\n"
            "🔍 <b>Available Commands:</b>\n"
            "• <code>/phone 9876543210</code> - Indian Phone Number 🇮🇳\n"
            "• <code>/help</code> - Show this help message\n\n"
            "💡 <b>Note:</b> In this group, you have <b>UNLIMITED FREE SEARCHES</b>! No credits required.\n\n"
            "⚠️ <b>Important:</b> For personal use with credit system, use the bot in private chat."
        )
    else:
        help_text = (
            "🤖 <b>Phone Info Bot - Private Mode</b>\n\n"
            "🔍 <b>Available Commands:</b>\n"
            "• <code>/phone 9876543210</code> - Indian Phone Number 🇮🇳\n"
            "• <code>/help</code> - Show this help message\n\n"
            f"🎁 <b>Daily Free Searches:</b> You can search <b>{daily_limit} numbers</b> for free every day!\n\n"
            "💰 <b>Credit System:</b>\n"
            "• After using daily free searches, each search costs 1 credit\n"
            "• Normal user gets 3 credits initially\n"
            "• Referral user gets 2 credits\n"
            "• Referrer gets 5 credits per referral\n"
            "• Earn more through referrals\n\n"
            "🔗 <b>Referral System:</b>\n"
            "• Get 5 credits per referral\n"
            "• 1-day premium at 15 referrals\n"
            "• Unlimited credits at 70 referrals!\n\n"
            "🚀 <b>Join our group for unlimited free searches!</b>"
        )
    
    await update.message.reply_text(help_text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(user.id))

async def phone_command(update: Update, context: CallbackContext) -> None:
    chat = update.effective_chat
    user = update.effective_user
    
    if not await check_maintenance(update, context):
        return
    
    if chat.type != 'private' and not is_official_group(chat.id):
        await send_restricted_message(update)
        return
    
    if await is_banned(user.id):
        return
    
    if chat.type == 'private':
        if not await check_and_require_subscription(update, context, user.id):
            return
    
    if not context.args:
        await update.message.reply_text("❌ Please provide a phone number.\nUsage: <code>/phone 9876543210</code>", parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(user.id))
        return
    
    raw_number = context.args[0].strip()
    normalized = normalize_phone_number(raw_number)
    if normalized is None:
        keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text("❌ <b>Invalid Input:</b> Please provide a valid 10-digit Indian mobile number.", parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        return
    
    await perform_phone_lookup_command(update, context, normalized, raw_original=raw_number)

async def redeem_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    chat = update.effective_chat
    
    if not await check_maintenance(update, context):
        return
    
    if await is_banned(user.id): return
    
    if chat.type != 'private' and not is_official_group(chat.id):
        await send_restricted_message(update)
        return
    
    if chat.type == 'private':
        if not await check_and_require_subscription(update, context, user.id):
            return
        
    if not context.args:
        context.user_data['state'] = 'awaiting_redeem_code'
        await update.message.reply_text("🎁 Send me your redeem code.", reply_markup=get_main_keyboard(user.id))
        return
    await process_redeem_code(context.args[0], update, context)

# ==================== MAIN SEARCH FUNCTION (UPDATED TO USE IMPORTED API) ====================
async def perform_phone_lookup_command(update: Update, context: CallbackContext, phone_number: str, raw_original: str = None):
    user = update.effective_user
    chat = update.effective_chat
    original_input = raw_original or phone_number
    
    if is_maintenance_mode_active() and not is_admin(user.id):
        return
    
    if is_number_protected(phone_number):
        protection_message = get_protection_message(phone_number)
        keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await log_search_to_channel(context, user, "Indian Phone Number", original_input, "🚫 <b>Blocked:</b> This number is protected from searches.\n" + protection_message, success=False, chat_id=chat.id)
        await update.message.reply_text(protection_message, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        return
    
    use_daily = False
    if chat.type == 'private':
        if not (is_global_free_mode_active() or is_free_mode_active() or is_admin(user.id) or await is_premium(user.id)):
            if await can_use_daily_free(user.id):
                use_daily = True
                await increment_daily_searches(user.id)
            else:
                if not await deduct_credits(user.id, chat.id):
                    user_data = load_data(USER_DATA_FILE)
                    credits = user_data.get(str(user.id), {}).get("credits", 0)
                    keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]]
                    reply_markup = InlineKeyboardMarkup(keyboard)
                    await log_search_to_channel(context, user, "Indian Phone Number", original_input, f"❌ <b>Insufficient Credits:</b> User has {credits} credits, needs {SEARCH_COST}", success=False, chat_id=chat.id)
                    await update.message.reply_text(f"❌ <b>Insufficient credits!</b>\n\nYou have {credits} credits but need {SEARCH_COST} credit for this search.\n\nEarn more credits through referrals or wait for free mode.", parse_mode=ParseMode.HTML, reply_markup=reply_markup)
                    return
    
    log_user_action(update.effective_user.id, "Phone Search", phone_number)

    keyboard = [[InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    sent_message = await update.message.reply_text("🔍 Searching for Indian phone details...", reply_markup=reply_markup)
    
    # --- Use the imported API function ---
    records = await fetch_from_new_api(phone_number, PHONE_API_NEW)
    
    if records is None:
        error_msg = "❌ <b>No data found for this phone number.</b>\n\n<i>The API returned no results.</i>"
        await log_search_to_channel(context, user, "Indian Phone Number", original_input, "❌ No data from API", success=False, chat_id=chat.id)
        error_msg += get_info_footer(update.effective_user.id, update.effective_chat.id)
        await sent_message.edit_text(error_msg, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        return

    result_text = f"🔍 <b>Phone Lookup Results for {phone_number}</b>\n\n"
    for i, record in enumerate(records[:10], 1):
        result_text += f"✅ <b>Result {i}:</b>\n\n"
        fields = [
            ("name", "👤 Name"),
            ("father_name", "👨‍👦 Father"),
            ("address", "📍 Address"),
            ("mobile", "📱 Mobile"),
            ("alt_mobile", "☎️ Alternate"),
            ("circle", "📡 Circle"),
            ("id_number", "🆔 ID Number"),
            ("email", "📧 Email")
        ]
        for key, label in fields:
            value = record.get(key, "")
            if value and str(value).strip() and str(value).lower() not in ['n/a', 'null', 'none', '']:
                if key == 'address':
                    value = format_address(str(value))
                result_text += f"<b>{label}:</b> {value}\n"
        extra_fields = [k for k in record.keys() if k not in [f[0] for f in fields]]
        for extra in extra_fields:
            val = record[extra]
            if val and str(val).strip() and str(val).lower() not in ['n/a', 'null', 'none', '']:
                result_text += f"<b>{extra.replace('_', ' ').title()}:</b> {val}\n"
        result_text += "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    result_text += "\n\n💞<b>Developer: @ll_VIPIN_ll</b>"
    
    if use_daily:
        daily_count, _ = get_user_daily_data(user.id)
        limit = get_daily_free_limit()
        remaining = limit - daily_count
        footer = f"\n\n🎁 <b>Daily Free Search Used:</b> {daily_count}/{limit} (You have {remaining} free searches left today)"
    else:
        footer = get_info_footer(user.id, chat.id)
    
    # Prepare final text with footer
    final_text = result_text + footer
    
    # Store for download
    context.user_data['last_search_result'] = result_text
    context.user_data['last_search_query'] = phone_number
    context.user_data['last_search_type'] = 'phone_lookup'
    
    await log_search_to_channel(context, user, "Indian Phone Number", original_input, result_text[:500] + "..." if len(result_text) > 500 else result_text, success=True, chat_id=chat.id)
    
    keyboard = [
        [InlineKeyboardButton("📥 Download Information", callback_data='download_info')],
        [InlineKeyboardButton("🔙 Back to Main", callback_data='back_to_main')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    auto_delete_time = get_auto_delete_time()
    
    # Attempt to edit the original message
    try:
        await sent_message.edit_text(final_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        if auto_delete_time > 0:
            context.job_queue.run_once(delete_message, auto_delete_time, data={'chat_id': sent_message.chat_id, 'message_id': sent_message.message_id})
    except Exception as e:
        if "Message is too long" in str(e):
            # Truncate the message to avoid Telegram's limit, add note about download
            truncated_text = final_text[:3500] + "\n\n...\n<i>Result truncated. Use download button for full data.</i>"
            await sent_message.edit_text(truncated_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            if auto_delete_time > 0:
                context.job_queue.run_once(delete_message, auto_delete_time, data={'chat_id': sent_message.chat_id, 'message_id': sent_message.message_id})
        else:
            # Some other error – try sending as new message, but we'll delete the old one to avoid duplicates
            logger.error(f"Error editing message: {e}. Sending as new message.")
            try:
                await sent_message.delete()
            except:
                pass
            new_msg = await update.message.reply_text(final_text, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
            if auto_delete_time > 0:
                context.job_queue.run_once(delete_message, auto_delete_time, data={'chat_id': new_msg.chat_id, 'message_id': new_msg.message_id})

async def process_redeem_code(code_text: str, update: Update, context: CallbackContext):
    user = update.effective_user
    user_id_str = str(user.id)
    user_data = load_data(USER_DATA_FILE)

    if user_id_str not in user_data:
        await update.message.reply_text("Please /start the bot first to create an account.")
        return

    last_redeem_time = user_data[user_id_str].get("last_redeem_timestamp", 0)
    current_time = time.time()

    if current_time - last_redeem_time < REDEEM_COOLDOWN_SECONDS:
        time_left = int((REDEEM_COOLDOWN_SECONDS - (current_time - last_redeem_time)) / 60)
        await update.message.reply_text(f"⏳ You are on a cooldown. Please try again in about {time_left+1} minutes.", reply_markup=get_main_keyboard(user.id))
        return

    code = code_text.strip().upper()
    redeem_codes = load_data(REDEEM_CODES_FILE)
    if code not in redeem_codes:
        await update.message.reply_text("❌ Invalid code.", reply_markup=get_main_keyboard(user.id))
        return
    if code in user_data[user_id_str].get("redeemed_codes", []):
        await update.message.reply_text("⚠️ You have already used this code.", reply_markup=get_main_keyboard(user.id))
        return
    if redeem_codes[code]["uses_left"] <= 0:
        await update.message.reply_text("⌛ This code has no uses left.", reply_markup=get_main_keyboard(user.id))
        return

    credits_to_add = redeem_codes[code]["credits"]
    user_data[user_id_str]["credits"] += credits_to_add
    if "redeemed_codes" not in user_data[user_id_str]:
        user_data[user_id_str]["redeemed_codes"] = []
    user_data[user_id_str]["redeemed_codes"].append(code)
    user_data[user_id_str]["last_redeem_timestamp"] = current_time
    redeem_codes[code]["uses_left"] -= 1

    save_data(user_data, USER_DATA_FILE)
    save_data(redeem_codes, REDEEM_CODES_FILE)
    log_user_action(user.id, "Redeemed Code", f"Code: {code}, Credits: {credits_to_add}")
    
    new_balance = user_data[user_id_str]["credits"]
    await notify_credits_added(context, user.id, credits_to_add, new_balance)
    
    await update.message.reply_text(f"✅ Success! <b>{credits_to_add} credits</b> have been added to your account.", parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(user.id))

# ==================== INLINE BUTTON HANDLER ====================

async def button_handler(update: Update, context: CallbackContext) -> None:
    query = update.callback_query
    user = query.from_user
    await query.answer()
    
    if is_maintenance_mode_active() and not is_admin(user.id):
        await query.edit_message_text(
            "⚠️ <b>Maintenance Mode is Active</b>\n\n"
            "The bot is currently under maintenance. Please try again later.",
            parse_mode=ParseMode.HTML
        )
        return
    
    data = query.data

    if data == 'back_to_main':
        context.user_data['menu_level'] = 'main'
        await query.message.delete()
        await query.message.reply_text(
            "🔙 Returned to main menu.",
            reply_markup=get_main_keyboard(user.id),
            parse_mode=ParseMode.HTML
        )
        return

    if data == 'verify_join':
        if await is_subscribed(user.id, context):
            user_data = load_data(USER_DATA_FILE)
            user_id_str = str(user.id)
            if user_id_str not in user_data:
                referrer_id = context.user_data.get('pending_referral')
                await process_new_user(context, user.id, query.message.chat_id, referrer_id)
                context.user_data.pop('pending_referral', None)
                user_data = load_data(USER_DATA_FILE)
                welcome_text = f"<b>🎉 Welcome, {user.first_name}!</b>\n\nYou have <b>{user_data[user_id_str]['credits']} credits</b> to get started.\n\nUse the buttons below to search."
                await query.edit_message_text(welcome_text, parse_mode=ParseMode.HTML)
                await query.message.reply_text("Main Menu:", reply_markup=get_main_keyboard(user.id))
            else:
                await query.edit_message_text("✅ Verification successful! You can now use the bot.")
        else:
            await query.edit_message_text("❌ You haven't joined all required channels. Please join both channels and try again.")
        return

    if data == 'download_info':
        if 'last_search_result' not in context.user_data:
            await query.answer("❌ No search result available to download.", show_alert=True)
            return
        
        result_text = context.user_data['last_search_result']
        query_str = context.user_data.get('last_search_query', 'unknown')
        search_type = context.user_data.get('last_search_type', 'search')
        
        bot_username = (await context.bot.get_me()).username
        file_bytes = create_search_result_file(result_text, query_str, search_type, bot_username)
        
        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=file_bytes,
            caption=f"📁 <b>Search Results Download</b>\n\nQuery: <code>{query_str}</code>\nType: {search_type.replace('_', ' ').title()}\n\n✅ File downloaded successfully!",
            parse_mode=ParseMode.HTML
        )
        
        await query.answer("✅ File sent successfully!")
        return

# ==================== MESSAGE HANDLER ====================

async def handle_message(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    message_text = update.message.text.strip()
    chat = update.effective_chat
    
    if not await check_maintenance(update, context):
        return
    
    if await is_banned(user.id):
        return
    
    if chat.type != 'private' and not is_official_group(chat.id):
        await send_restricted_message(update)
        return

    normalized = normalize_phone_number(message_text)
    if normalized is not None:
        if chat.type == 'private' and not await check_and_require_subscription(update, context, user.id):
            return
        await perform_phone_lookup_command(update, context, normalized, raw_original=message_text)
        return

    if context.user_data.get('state') == 'awaiting_redeem_code':
        context.user_data['state'] = None
        await process_redeem_code(message_text, update, context)
        return

    if context.user_data.get('admin_action'):
        if message_text in ["Back to Main 🔙", "Back to Admin 🔙"]:
            context.user_data['admin_action'] = None
        else:
            admin_action = context.user_data['admin_action']
            
            if admin_action == 'add_credits':
                try:
                    parts = message_text.split()
                    if len(parts) != 2:
                        await update.message.reply_text("❌ Invalid format. Use: user_id credits_amount", reply_markup=get_admin_keyboard())
                        context.user_data['admin_action'] = None
                        return
                    target_id = int(parts[0])
                    credits = int(parts[1])
                    if credits <= 0:
                        await update.message.reply_text("❌ Credits amount must be positive.", reply_markup=get_admin_keyboard())
                        context.user_data['admin_action'] = None
                        return
                    new_balance = await add_credits_to_user(target_id, credits, context)
                    await update.message.reply_text(f"✅ Added {credits} credits to user {target_id}. New balance: {new_balance}", reply_markup=get_admin_keyboard())
                    log_user_action(user.id, "Added Credits", f"To: {target_id}, Amount: {credits}")
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID or credits amount.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'remove_credits':
                try:
                    parts = message_text.split()
                    if len(parts) != 2:
                        await update.message.reply_text("❌ Invalid format. Use: user_id credits_amount", reply_markup=get_admin_keyboard())
                        context.user_data['admin_action'] = None
                        return
                    target_id = int(parts[0])
                    credits = int(parts[1])
                    if credits <= 0:
                        await update.message.reply_text("❌ Credits amount must be positive.", reply_markup=get_admin_keyboard())
                        context.user_data['admin_action'] = None
                        return
                    new_balance = await remove_credits_from_user(target_id, credits, context)
                    await update.message.reply_text(f"✅ Removed {credits} credits from user {target_id}. New balance: {new_balance}", reply_markup=get_admin_keyboard())
                    log_user_action(user.id, "Removed Credits", f"From: {target_id}, Amount: {credits}")
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID or credits amount.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'add_premium':
                try:
                    parts = message_text.split()
                    target_id = int(parts[0])
                    days = None
                    if len(parts) > 1:
                        days = int(parts[1])
                    if await add_user_to_premium(target_id, context, days):
                        if days:
                            await update.message.reply_text(f"✅ Added {days} days premium to user {target_id}", reply_markup=get_admin_keyboard())
                        else:
                            await update.message.reply_text(f"✅ Added permanent premium to user {target_id}", reply_markup=get_admin_keyboard())
                        log_user_action(user.id, "Added Premium", f"To: {target_id}, Days: {days}")
                    else:
                        await update.message.reply_text(f"❌ User {target_id} is already premium or user doesn't exist.", reply_markup=get_admin_keyboard())
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID or days.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'remove_premium':
                try:
                    target_id = int(message_text)
                    if await remove_user_from_premium(target_id, context):
                        await update.message.reply_text(f"✅ Removed premium from user {target_id}", reply_markup=get_admin_keyboard())
                        log_user_action(user.id, "Removed Premium", f"From: {target_id}")
                    else:
                        await update.message.reply_text(f"❌ User {target_id} is not premium or user doesn't exist.", reply_markup=get_admin_keyboard())
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'block_user':
                try:
                    target_id = int(message_text)
                    if ban_user(target_id):
                        await update.message.reply_text(f"✅ Blocked user {target_id}", reply_markup=get_admin_keyboard())
                        log_user_action(user.id, "Blocked User", f"User: {target_id}")
                    else:
                        await update.message.reply_text(f"❌ User {target_id} is already blocked or user doesn't exist.", reply_markup=get_admin_keyboard())
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'unblock_user':
                try:
                    target_id = int(message_text)
                    if unban_user(target_id):
                        await update.message.reply_text(f"✅ Unblocked user {target_id}", reply_markup=get_admin_keyboard())
                        log_user_action(user.id, "Unblocked User", f"User: {target_id}")
                    else:
                        await update.message.reply_text(f"❌ User {target_id} is not blocked or user doesn't exist.", reply_markup=get_admin_keyboard())
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'add_credits_all':
                try:
                    credits = int(message_text)
                    if credits <= 0:
                        await update.message.reply_text("❌ Credits amount must be positive.", reply_markup=get_admin_keyboard())
                        context.user_data['admin_action'] = None
                        return
                    admin_name = user.first_name or f"Admin {user.id}"
                    sent_msg = await update.message.reply_text(f"🔄 Adding {credits} credits to all users in background...", reply_markup=get_admin_keyboard())
                    summary = await add_credits_to_all_users_async(context, credits, admin_name)
                    await sent_msg.edit_text(summary, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard())
                    log_user_action(user.id, "Added Credits to All", f"Credits: {credits}")
                except ValueError:
                    await update.message.reply_text("❌ Please enter a valid number of credits.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'protect_number':
                try:
                    parts = message_text.split()
                    if len(parts) < 1:
                        await update.message.reply_text("❌ Please provide a number to protect.", reply_markup=get_number_protection_keyboard())
                        context.user_data['admin_action'] = None
                        return
                    number = parts[0].strip()
                    custom_message = None
                    if len(parts) > 1:
                        custom_message = ' '.join(parts[1:])
                    if protect_number(number, user.id, custom_message):
                        if custom_message:
                            await update.message.reply_text(
                                f"✅ <b>Number Protected!</b>\n\n📱 {number}\n💬 {custom_message}",
                                parse_mode=ParseMode.HTML,
                                reply_markup=get_number_protection_keyboard()
                            )
                        else:
                            await update.message.reply_text(
                                f"✅ <b>Number Protected!</b>\n\n📱 {number}",
                                parse_mode=ParseMode.HTML,
                                reply_markup=get_number_protection_keyboard()
                            )
                        log_user_action(user.id, "Protected Number", f"Number: {number}, Message: {custom_message}")
                    else:
                        await update.message.reply_text("❌ This number is already protected.", reply_markup=get_number_protection_keyboard())
                except Exception as e:
                    await update.message.reply_text(f"❌ Error protecting number: {e}", reply_markup=get_number_protection_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'unprotect_number':
                try:
                    number = message_text.strip()
                    if unprotect_number(number):
                        await update.message.reply_text(
                            f"✅ <b>Number Unprotected!</b>\n\n📱 {number}",
                            parse_mode=ParseMode.HTML,
                            reply_markup=get_number_protection_keyboard()
                        )
                        log_user_action(user.id, "Unprotected Number", f"Number: {number}")
                    else:
                        await update.message.reply_text("❌ This number is not protected.", reply_markup=get_number_protection_keyboard())
                except Exception as e:
                    await update.message.reply_text(f"❌ Error unprotecting number: {e}", reply_markup=get_number_protection_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'add_admin':
                if not is_owner(user.id):
                    await update.message.reply_text("❌ Only bot owners can add admins.", reply_markup=get_admin_management_keyboard())
                    context.user_data['admin_action'] = None
                    return
                try:
                    new_admin_id = int(message_text)
                    if add_admin(new_admin_id, user.id):
                        await update.message.reply_text(
                            f"✅ <b>New Admin Added!</b>\n\n👨‍💼 <code>{new_admin_id}</code>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=get_admin_management_keyboard()
                        )
                        await notify_new_admin(context, new_admin_id, user.id)
                    else:
                        await update.message.reply_text("❌ This user is already an admin or owner.", reply_markup=get_admin_management_keyboard())
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", reply_markup=get_admin_management_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'remove_admin':
                if not is_owner(user.id):
                    await update.message.reply_text("❌ Only bot owners can remove admins.", reply_markup=get_admin_management_keyboard())
                    context.user_data['admin_action'] = None
                    return
                try:
                    admin_id = int(message_text)
                    if remove_admin(admin_id, user.id):
                        await update.message.reply_text(
                            f"✅ <b>Admin Removed!</b>\n\n👨‍💼 <code>{admin_id}</code>",
                            parse_mode=ParseMode.HTML,
                            reply_markup=get_admin_management_keyboard()
                        )
                        await notify_removed_admin(context, admin_id, user.id)
                    else:
                        await update.message.reply_text("❌ Cannot remove this user. They might be an owner or not an admin.", reply_markup=get_admin_management_keyboard())
                except ValueError:
                    await update.message.reply_text("❌ Invalid user ID.", reply_markup=get_admin_management_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'broadcast':
                sent = await update.message.reply_text("📢 Sending broadcast message to all users...")
                success, fail = await broadcast_message(context, message_text)
                preview = message_text[:50] + "..." if len(message_text) > 50 else message_text
                await sent.edit_text(f"📢 Broadcast completed!\n✅ Success: {success}\n❌ Failed: {fail}\n📄 Message preview: {preview}", reply_markup=get_admin_keyboard())
                log_user_action(user.id, "Broadcast", f"Message: {message_text[:50]}..., Success: {success}, Failed: {fail}")
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'set_daily_limit':
                try:
                    new_limit = int(message_text)
                    if new_limit <= 0:
                        await update.message.reply_text("❌ Limit must be positive.", reply_markup=get_admin_keyboard())
                    else:
                        set_daily_free_limit(new_limit)
                        await update.message.reply_text(f"✅ Daily free search limit set to {new_limit}.", reply_markup=get_admin_keyboard())
                        log_user_action(user.id, "Set Daily Limit", f"Limit: {new_limit}")
                except ValueError:
                    await update.message.reply_text("❌ Please enter a valid number.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            elif admin_action == 'set_auto_delete':
                try:
                    seconds = int(message_text)
                    if seconds < 0:
                        await update.message.reply_text("❌ Seconds must be 0 or positive.", reply_markup=get_admin_keyboard())
                    else:
                        set_auto_delete_time(seconds)
                        await update.message.reply_text(f"✅ Auto-delete time set to {seconds} seconds (0 = disabled).", reply_markup=get_admin_keyboard())
                        log_user_action(user.id, "Set Auto-Delete Time", f"Seconds: {seconds}")
                except ValueError:
                    await update.message.reply_text("❌ Please enter a valid number of seconds.", reply_markup=get_admin_keyboard())
                context.user_data['admin_action'] = None
                return

            context.user_data['admin_action'] = None

    menu_level = context.user_data.get('menu_level', 'main')

    if menu_level == 'main':
        if message_text == "India Number 🇮🇳":
            context.user_data['state'] = 'awaiting_search'
            await update.message.reply_text("🔍 Please send the 10-digit Indian mobile number:", reply_markup=get_main_keyboard(user.id))
        elif message_text == "Check Credit 💰":
            user_data = load_data(USER_DATA_FILE)
            user_id_str = str(user.id)
            credits = user_data.get(user_id_str, {}).get("credits", 0)
            referral_count = user_data.get(user_id_str, {}).get("referral_count", 0)
            daily_count, _ = get_user_daily_data(user.id)
            limit = get_daily_free_limit()
            remaining = limit - daily_count
            msg = f"💰 <b>Your Credits:</b> {credits}\n📊 <b>Your Referrals:</b> {referral_count}\n🎁 <b>Daily Free Searches Used:</b> {daily_count}/{limit} ({remaining} left today)\n\n"
            if referral_count >= REFERRAL_TIER_2_COUNT:
                msg += "♾️ <b>Status:</b> UNLIMITED CREDITS (Tier 2 Reached!)"
            elif referral_count >= REFERRAL_TIER_1_COUNT:
                msg += "⭐ <b>Status:</b> Premium User (Tier 1 Reached!)"
            else:
                msg += f"🎯 <b>Next Tier:</b> {max(0, REFERRAL_TIER_1_COUNT - referral_count)} referrals needed for 1-day premium"
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(user.id))
        elif message_text == "Get Referral Link 🔗":
            bot_username = (await context.bot.get_me()).username
            referral_link = f"https://t.me/{bot_username}?start={user.id}"
            msg = (
                f"🔗 <b>Your Referral Link:</b>\n<code>{referral_link}</code>\n\n"
                f"📊 <b>Referral Rewards:</b>\n• Normal new user gets 3 credits\n• Referral user gets 2 credits\n• You get 5 credits per referral\n• 1-day Premium at {REFERRAL_TIER_1_COUNT} referrals\n• UNLIMITED credits at {REFERRAL_TIER_2_COUNT} referrals!"
            )
            keyboard = [[InlineKeyboardButton("📤 Share Link", url=f"https://t.me/share/url?url={referral_link}&text=Join%20this%20awesome%20Phone%20Info%20bot!")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text(msg, parse_mode=ParseMode.HTML, reply_markup=reply_markup)
        elif message_text == "Redeem Code 🎁":
            context.user_data['state'] = 'awaiting_redeem_code'
            await update.message.reply_text("🎁 Send me your redeem code.", reply_markup=get_main_keyboard(user.id))
        elif message_text == "Buy Premium & Credits 💎":
            context.user_data['menu_level'] = 'buy'
            await update.message.reply_text("💎 Choose an option:", reply_markup=get_buy_keyboard())
        elif message_text == "Support 👨‍💻":
            await update.message.reply_text(f"👨‍💻 <b>Support</b>\n\nContact @{SUPPORT_USERNAME} for any issues.", parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(user.id))
        elif message_text == "Official Group 🚀":
            await update.message.reply_text(f"🚀 <b>Official Group:</b> {OFFICIAL_GROUP_LINK}\n\nJoin for unlimited free searches!", parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(user.id))
        elif message_text == "Privacy Policy 🔒":
            privacy_text = (
                "🔒 <b>Privacy Policy</b>\n\n"
                "• We do NOT collect or store any personal data\n"
                "• We do NOT store your search queries or results\n"
                "• We do NOT share any information with third parties\n"
                "• We only store your credit balance and referral count\n"
                "• Your privacy is 100% secured\n\n"
                "Contact @KHRsupportBot for questions."
            )
            await update.message.reply_text(privacy_text, parse_mode=ParseMode.HTML, reply_markup=get_main_keyboard(user.id))
        elif message_text == "Admin Panel 👑" and is_admin(user.id):
            context.user_data['menu_level'] = 'admin'
            await update.message.reply_text("👑 Admin Panel", reply_markup=get_admin_keyboard())
        else:
            pass

    elif menu_level == 'admin':
        if message_text == "Back to Main 🔙":
            context.user_data['menu_level'] = 'main'
            await update.message.reply_text("🔙 Main Menu", reply_markup=get_main_keyboard(user.id))
        elif message_text == "Number Protection 🛡️":
            context.user_data['menu_level'] = 'admin_number_protection'
            await update.message.reply_text("🛡️ Number Protection", reply_markup=get_number_protection_keyboard())
        elif message_text == "Admin Management 👨‍💼":
            if not is_owner(user.id):
                await update.message.reply_text("❌ Only owners can manage admins.", reply_markup=get_admin_keyboard())
                return
            context.user_data['menu_level'] = 'admin_management'
            await update.message.reply_text("👨‍💼 Admin Management", reply_markup=get_admin_management_keyboard())
        elif message_text == "Auto-Delete Time ⏱️":
            context.user_data['admin_action'] = 'set_auto_delete'
            current = get_auto_delete_time()
            await update.message.reply_text(f"⏱️ Current auto-delete time: {current} seconds (0 = disabled).\n\nEnter new time in seconds:", reply_markup=get_admin_keyboard())
        elif message_text == "Maintenance Mode ⚠️":
            current = is_maintenance_mode_active()
            new_status = not current
            set_maintenance_mode(new_status)
            status_text = "ON" if new_status else "OFF"
            await update.message.reply_text(f"⚠️ Maintenance mode is now <b>{status_text}</b>.\n\nUsers {'will not' if new_status else 'will now'} be able to use the bot.", parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard())
            log_user_action(user.id, "Toggled Maintenance Mode", f"New: {new_status}")
        elif message_text == "Add Credits ➕":
            context.user_data['admin_action'] = 'add_credits'
            await update.message.reply_text("💰 Send user ID and credits (e.g., 123456789 10):", reply_markup=get_admin_keyboard())
        elif message_text == "Remove Credits ➖":
            context.user_data['admin_action'] = 'remove_credits'
            await update.message.reply_text("💰 Send user ID and credits (e.g., 123456789 5):", reply_markup=get_admin_keyboard())
        elif message_text == "Add Premium ⭐":
            context.user_data['admin_action'] = 'add_premium'
            await update.message.reply_text("⭐ Send user ID and optional days (e.g., 123456789 or 123456789 7):", reply_markup=get_admin_keyboard())
        elif message_text == "Remove Premium ⭐➖":
            context.user_data['admin_action'] = 'remove_premium'
            await update.message.reply_text("⭐ Send user ID:", reply_markup=get_admin_keyboard())
        elif message_text == "Add Credits to All 💰👥":
            context.user_data['admin_action'] = 'add_credits_all'
            await update.message.reply_text("💰 Enter number of credits to add to EVERY user:", reply_markup=get_admin_keyboard())
        elif message_text == "User History 📝":
            await update.message.reply_text("📝 Use /history <user_id>", reply_markup=get_admin_keyboard())
        elif message_text == "Broadcast 📢":
            context.user_data['admin_action'] = 'broadcast'
            await update.message.reply_text("📢 Send the message to broadcast:", reply_markup=get_admin_keyboard())
        elif message_text == "Premium List 📋":
            premium_users = load_data(PREMIUM_USERS_FILE)
            if not premium_users:
                await update.message.reply_text("⭐ No premium users.", reply_markup=get_admin_keyboard())
            else:
                text = "⭐ Premium Users:\n"
                for uid in premium_users[:50]:
                    text += f"<code>{uid}</code>\n"
                if len(premium_users) > 50:
                    text += f"... and {len(premium_users)-50} more"
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard())
        elif message_text == "Block User 🚫":
            context.user_data['admin_action'] = 'block_user'
            await update.message.reply_text("🚫 Send user ID to block:", reply_markup=get_admin_keyboard())
        elif message_text == "Unblock User ✅":
            context.user_data['admin_action'] = 'unblock_user'
            await update.message.reply_text("✅ Send user ID to unblock:", reply_markup=get_admin_keyboard())
        elif message_text == "Blocked List 📋🚫":
            banned = load_data(BANNED_USERS_FILE)
            if not banned:
                await update.message.reply_text("📋 No blocked users.", reply_markup=get_admin_keyboard())
            else:
                text = "🚫 Blocked Users:\n"
                for uid in banned:
                    text += f"<code>{uid}</code>\n"
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard())
        elif message_text == "Bot Stats 📊":
            user_data = load_data(USER_DATA_FILE)
            total = len(user_data)
            total_credits = sum(u.get('credits',0) for u in user_data.values())
            premium = len([u for u in user_data if user_data[u].get('premium_until')])
            banned = len(load_data(BANNED_USERS_FILE))
            protected = len(get_all_protected_numbers())
            admins = len(get_all_admins())
            tier1 = len([u for u in user_data if user_data[u].get('referral_count',0) >= REFERRAL_TIER_1_COUNT])
            tier2 = len([u for u in user_data if user_data[u].get('referral_count',0) >= REFERRAL_TIER_2_COUNT])
            stats = f"📊 Stats:\n👥 Users: {total}\n💰 Credits: {total_credits}\n⭐ Premium: {premium}\n🚫 Banned: {banned}\n🛡️ Protected: {protected}\n👑 Admins: {admins}\n🎯 Tier1: {tier1}\n♾️ Tier2: {tier2}"
            await update.message.reply_text(stats, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard())
        elif message_text == "Generate Code 🎁":
            await update.message.reply_text("🎁 Use /gencode <credits> <uses>", reply_markup=get_admin_keyboard())
        elif message_text == "Toggle Group Free 🎯":
            current = is_free_mode_active()
            set_free_mode(not current)
            status = "ON" if not current else "OFF"
            await update.message.reply_text(f"🎯 Group Free Mode is now {status}.", reply_markup=get_admin_keyboard())
            log_user_action(user.id, "Toggled Group Free Mode", f"New: {not current}")
        elif message_text == "Toggle Global Free 🌍":
            current = is_global_free_mode_active()
            new = not current
            set_global_free_mode(new)
            status = "ON" if new else "OFF"
            admin_name = user.first_name or f"Admin {user.id}"
            await update.message.reply_text(f"🌍 Global Free Mode set to {status}. Notifying users...", reply_markup=get_admin_keyboard())
            asyncio.create_task(notify_global_free_mode_change_async(context, new, admin_name))
            log_user_action(user.id, "Toggled Global Free Mode", f"New: {new}")
        elif message_text == "Set Daily Limit 🔢":
            context.user_data['admin_action'] = 'set_daily_limit'
            await update.message.reply_text("🔢 Enter the new daily free search limit (positive integer):", reply_markup=get_admin_keyboard())
        elif message_text == "Referral Stats 📈":
            user_data = load_data(USER_DATA_FILE)
            top = sorted([(uid, d.get('referral_count',0)) for uid,d in user_data.items() if d.get('referral_count',0)>0], key=lambda x: x[1], reverse=True)[:10]
            text = "📈 Top Referrers:\n"
            for i, (uid, cnt) in enumerate(top,1):
                text += f"{i}. <code>{uid}</code> - {cnt}\n"
            if not top:
                text = "No referrals yet."
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_admin_keyboard())
        else:
            pass

    elif menu_level == 'admin_number_protection':
        if message_text == "Back to Admin 🔙":
            context.user_data['menu_level'] = 'admin'
            await update.message.reply_text("👑 Admin Panel", reply_markup=get_admin_keyboard())
        elif message_text == "Protect Number ➕🛡️":
            context.user_data['admin_action'] = 'protect_number'
            await update.message.reply_text("🛡️ Send number and optional message (e.g., 9876543210 or 9876543210 This is private):", reply_markup=get_number_protection_keyboard())
        elif message_text == "Unprotect Number ➖🛡️":
            context.user_data['admin_action'] = 'unprotect_number'
            await update.message.reply_text("🛡️ Send number to unprotect:", reply_markup=get_number_protection_keyboard())
        elif message_text == "Protected List 📋🛡️":
            protected = get_all_protected_numbers()
            if not protected:
                await update.message.reply_text("🛡️ No protected numbers.", reply_markup=get_number_protection_keyboard())
            else:
                text = "🛡️ <b>Protected Numbers:</b>\n\n"
                items = list(protected.items())[:20]
                for idx, (num, det) in enumerate(items, 1):
                    admin_id = det.get('protected_by')
                    msg = det.get('message', 'No custom message')
                    try:
                        admin_chat = await context.bot.get_chat(admin_id)
                        admin_name = admin_chat.full_name or admin_chat.first_name or str(admin_id)
                    except Exception:
                        admin_name = f"Unknown (ID: {admin_id})"
                    text += f"{idx}. 📱 <code>{num}</code>\n"
                    text += f"   💬 <i>{msg}</i>\n"
                    text += f"   👤 <b>Protected by:</b> {admin_name} (<code>{admin_id}</code>)\n"
                    if 'protected_at' in det:
                        text += f"   🕒 {det['protected_at']}\n"
                    text += "\n"
                if len(protected) > 20:
                    text += f"... and {len(protected)-20} more protected numbers.\n"
                await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_number_protection_keyboard())
        else:
            pass

    elif menu_level == 'admin_management':
        if message_text == "Back to Admin 🔙":
            context.user_data['menu_level'] = 'admin'
            await update.message.reply_text("👑 Admin Panel", reply_markup=get_admin_keyboard())
        elif message_text == "Add Admin ➕👨‍💼":
            if not is_owner(user.id):
                await update.message.reply_text("❌ Only owners can add admins.", reply_markup=get_admin_management_keyboard())
                return
            context.user_data['admin_action'] = 'add_admin'
            await update.message.reply_text("👨‍💼 Send user ID to make admin:", reply_markup=get_admin_management_keyboard())
        elif message_text == "Remove Admin ➖👨‍💼":
            if not is_owner(user.id):
                await update.message.reply_text("❌ Only owners can remove admins.", reply_markup=get_admin_management_keyboard())
                return
            context.user_data['admin_action'] = 'remove_admin'
            await update.message.reply_text("👨‍💼 Send user ID to remove admin:", reply_markup=get_admin_management_keyboard())
        elif message_text == "Admin List 📋👨‍💼":
            text = get_admin_list_text()
            await update.message.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=get_admin_management_keyboard())
        else:
            pass

    elif menu_level == 'buy':
        if message_text == "Back to Main 🔙":
            context.user_data['menu_level'] = 'main'
            await update.message.reply_text("🔙 Main Menu", reply_markup=get_main_keyboard(user.id))
        elif message_text == "Premium Plans ⭐":
            premium_text = (
                "⭐ <b>Premium Plans</b>\n\n"
                "1. <b>1 Day Premium</b> - ₹35\n"
                "2. <b>1 Week Premium</b> - ₹99\n"
                "3. <b>1 Month Premium</b> - ₹299\n"
                "4. <b>Lifetime Premium</b> - ₹999\n\n"
                "To purchase, contact @KHRsupportBot"
            )
            keyboard = [[InlineKeyboardButton("Contact Support", url=f"https://t.me/{SUPPORT_USERNAME}")]]
            await update.message.reply_text(premium_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        elif message_text == "Credit Packages 💰":
            credit_text = (
                "💰 <b>Credit Packages</b>\n\n"
                "1. <b>10 Credits</b> - ₹15\n"
                "2. <b>27 Credits</b> - ₹35\n"
                "3. <b>55 Credits</b> - ₹65\n"
                "4. <b>115 Credits</b> - ₹110\n"
                "5. <b>250 Credits</b> - ₹200\n\n"
                "To purchase, contact @KHRsupportBot"
            )
            keyboard = [[InlineKeyboardButton("Contact Support", url=f"https://t.me/{SUPPORT_USERNAME}")]]
            await update.message.reply_text(credit_text, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(keyboard))
        else:
            pass

    else:
        context.user_data['menu_level'] = 'main'
        await update.message.reply_text("Returning to main menu.", reply_markup=get_main_keyboard(user.id))

# ==================== ADMIN COMMAND ====================

async def admin_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ This command is only for admins.")
        return
    context.user_data['menu_level'] = 'admin'
    await update.message.reply_text("👑 Admin Panel", reply_markup=get_admin_keyboard())

# ==================== OTHER ADMIN COMMANDS ====================

async def addadmin_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_owner(user.id):
        await update.message.reply_text("❌ This command is only for bot owners.")
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: /addadmin <user_id>")
        return
    try:
        new_id = int(context.args[0])
        if add_admin(new_id, user.id):
            await update.message.reply_text(f"✅ Admin added: {new_id}")
            await notify_new_admin(context, new_id, user.id)
        else:
            await update.message.reply_text("❌ Already admin or owner.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def removeadmin_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_owner(user.id):
        await update.message.reply_text("❌ This command is only for bot owners.")
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: /removeadmin <user_id>")
        return
    try:
        rem_id = int(context.args[0])
        if remove_admin(rem_id, user.id):
            await update.message.reply_text(f"✅ Admin removed: {rem_id}")
            await notify_removed_admin(context, rem_id, user.id)
        else:
            await update.message.reply_text("❌ Cannot remove. They might be owner or not admin.")
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def admins_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ This command is only for admins.")
        return
    await update.message.reply_text(get_admin_list_text(), parse_mode=ParseMode.HTML)

async def gencode(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ This command is only for admins.")
        return
    if not context.args or len(context.args) < 2:
        await update.message.reply_text("❌ Usage: /gencode <credits> <uses>")
        return
    try:
        credits = int(context.args[0])
        uses = int(context.args[1])
        if credits <= 0 or uses <= 0:
            await update.message.reply_text("❌ Credits and uses must be positive.")
            return
        code = secrets.token_hex(4).upper()
        redeem_codes = load_data(REDEEM_CODES_FILE)
        while code in redeem_codes:
            code = secrets.token_hex(4).upper()
        redeem_codes[code] = {
            "credits": credits,
            "uses_left": uses,
            "created_by": user.id,
            "created_at": datetime.now().isoformat()
        }
        save_data(redeem_codes, REDEEM_CODES_FILE)
        await update.message.reply_text(f"✅ Code: <code>{code}</code>\nCredits: {credits}\nUses: {uses}", parse_mode=ParseMode.HTML)
        log_user_action(user.id, "Generated Code", f"Code: {code}, Credits: {credits}, Uses: {uses}")
    except ValueError:
        await update.message.reply_text("❌ Please provide valid numbers.")

async def history_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ This command is only for admins.")
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: /history <user_id>")
        return
    try:
        target = int(context.args[0])
        history = load_data(USER_HISTORY_FILE).get(str(target), [])
        if not history:
            await update.message.reply_text(f"📝 No history for user {target}.")
            return
        text = f"📝 History for {target}:\n\n"
        for entry in history[:10]:
            text += f"⏰ {entry['timestamp']}\n🔧 {entry['action']}\n📄 {entry['details']}\n\n"
        if len(history) > 10:
            text += f"... and {len(history)-10} more"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID.")

async def protect_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ This command is only for admins.")
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: /protect <number> [custom message]")
        return
    number = context.args[0].strip()
    if not (number.isdigit() and (len(number)==10 or (number.startswith("91") and len(number)==12))):
        await update.message.reply_text("❌ Invalid Indian number.")
        return
    custom = ' '.join(context.args[1:]) if len(context.args)>1 else None
    if protect_number(number, user.id, custom):
        await update.message.reply_text(f"✅ Number {number} protected.")
        log_user_action(user.id, "Protected Number", f"Number: {number}, Message: {custom}")
    else:
        await update.message.reply_text("❌ Number already protected.")

async def unprotect_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ This command is only for admins.")
        return
    if not context.args:
        await update.message.reply_text("❌ Usage: /unprotect <number>")
        return
    number = context.args[0].strip()
    if unprotect_number(number):
        await update.message.reply_text(f"✅ Number {number} unprotected.")
        log_user_action(user.id, "Unprotected Number", f"Number: {number}")
    else:
        await update.message.reply_text("❌ Number not protected.")

async def protected_command(update: Update, context: CallbackContext) -> None:
    user = update.effective_user
    if not is_admin(user.id):
        await update.message.reply_text("❌ This command is only for admins.")
        return
    protected = get_all_protected_numbers()
    if not protected:
        await update.message.reply_text("🛡️ No protected numbers.")
        return
    text = "🛡️ <b>Protected Numbers:</b>\n\n"
    items = list(protected.items())[:20]
    for idx, (num, det) in enumerate(items, 1):
        admin_id = det.get('protected_by')
        msg = det.get('message', 'No custom message')
        try:
            admin_chat = await context.bot.get_chat(admin_id)
            admin_name = admin_chat.full_name or admin_chat.first_name or str(admin_id)
        except:
            admin_name = f"Unknown (ID: {admin_id})"
        text += f"{idx}. 📱 <code>{num}</code>\n"
        text += f"   💬 <i>{msg}</i>\n"
        text += f"   👤 <b>Protected by:</b> {admin_name} (<code>{admin_id}</code>)\n"
        if 'protected_at' in det:
            text += f"   🕒 {det['protected_at']}\n"
        text += "\n"
    if len(protected) > 20:
        text += f"... and {len(protected)-20} more protected numbers.\n"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

# ==================== MAIN ====================

def main() -> None:
    for f in [USER_DATA_FILE, REDEEM_CODES_FILE, BANNED_USERS_FILE, PREMIUM_USERS_FILE,
              FREE_MODE_FILE, USER_HISTORY_FILE, PROTECTED_NUMBERS_FILE, ADMINS_FILE,
              GLOBAL_FREE_MODE_FILE, DAILY_LIMIT_FILE, AUTO_DELETE_TIME_FILE, MAINTENANCE_MODE_FILE]:
        if not os.path.exists(f):
            default = {} if 'protected' in f or 'free' in f or 'global' in f or 'daily' in f or 'auto_delete' in f or 'maintenance' in f else []
            if 'free_mode' in f or 'global_free_mode' in f:
                default = {"active": False}
            if 'daily_free_limit' in f:
                default = {"limit": DEFAULT_DAILY_LIMIT}
            if 'auto_delete_time' in f:
                default = {"seconds": DEFAULT_AUTO_DELETE_TIME}
            if 'maintenance_mode' in f:
                default = {"active": False}
            save_data(default, f)

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("redeem", redeem_command))
    application.add_handler(CommandHandler("phone", phone_command))
    application.add_handler(CommandHandler("help", help_command))

    admin_filter = filters.User(user_id=get_all_admins())
    owner_filter = filters.User(ADMIN_IDS)

    application.add_handler(CommandHandler("protect", protect_command, filters=admin_filter))
    application.add_handler(CommandHandler("unprotect", unprotect_command, filters=admin_filter))
    application.add_handler(CommandHandler("protected", protected_command, filters=admin_filter))
    application.add_handler(CommandHandler("addadmin", addadmin_command, filters=owner_filter))
    application.add_handler(CommandHandler("removeadmin", removeadmin_command, filters=owner_filter))
    application.add_handler(CommandHandler("admins", admins_command, filters=admin_filter))
    application.add_handler(CommandHandler("admin", admin_command, filters=admin_filter))
    application.add_handler(CommandHandler("gencode", gencode, filters=admin_filter))
    application.add_handler(CommandHandler("history", history_command, filters=admin_filter))

    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    print("🚀 Phone Info Bot is running with ReplyKeyboardMarkup interface!")
    print("✅ New API Integration Active (cyber-apis.vercel.app) – fixed duplicate message issue")
    print("📥 Download Information feature active")
    print("🔗 Force channel join active")
    print("📊 Search logging active")
    print("💰 Bulk credit distribution active")
    print("🌍 Global free mode active")
    print("🎁 Daily free limit active (configurable by admin)")
    print("🔧 Broadcast cancellation fixed!")
    print("🛡️ Protected list now shows full details (serial, message, admin name & ID)")
    print("✅ Referral system fixed: now works even with mandatory channel join!")
    print("⏱️ Auto-delete feature added (default 60 seconds, configurable by admin)")
    print("⚠️ Maintenance mode added – admins can toggle to block all non‑admin users")
    print("🔧 Removed placeholder 'Data found' message – full results are always shown, truncated if too long with download option")
    application.run_polling()

if __name__ == '__main__':
    main()
