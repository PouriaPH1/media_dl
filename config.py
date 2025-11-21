
# Load environment variables from .env file
from dotenv import load_dotenv
import os
import json
load_dotenv()


# ╔════════════════════════════════════════════════╗
# ║           YOUTUBE DOWNLOADER SETTINGS          ║
# ╚════════════════════════════════════════════════╝

# cookie directory is related to downloading files from YouTube and is used for the main YouTube and Spotify downloader.
COOKIE_DIR = os.getenv('COOKIE_DIR') 

# Caching options
CACHE_ENABLED = os.getenv('CACHE_ENABLED', 'true').lower() == 'true'  # Enable/disable caching for saving file in channel
CACHE_TYPE = os.getenv('CACHE_TYPE', 'database')  # 'database' or 'system'
CACHE_DB_FILE = os.getenv('CACHE_DB_FILE', 'video_cache.db')  # Path to cache database file

# Channel name for saving files
SAVE_CHANNEL_NAME = os.getenv('SAVE_CHANNEL_NAME', 'default_channel')  # telegram channel for saving file and catch with file id


# Daily user limits for youtube and spotify
DAILY_COUNT_LIMIT = int(os.getenv('DAILY_COUNT_LIMIT', 5))  # Max downloads per day
DAILY_SIZE_LIMIT = int(os.getenv('DAILY_SIZE_LIMIT', 1 * 1024 * 1024 * 1024))  # 1GB per day

# Send an advertising message with a button to send the file to the user.

# example: if you want to add multiple ads, you can add them like this:
AD_MESSAGE_TEXTS = os.getenv('AD_MESSAGE_TEXTS', 'برای دریافت جدیدترین آموزش‌ها عضو کانال ما شوید!').split('|')  # پیام‌ها با | جدا شوند
AD_BUTTON_IDS = os.getenv('AD_BUTTON_IDS', '@yourchannel').split('|')  # آیدی‌ها با | جدا شوند


# --- YouTube download balancing ---
"""
This section is for setting the maximum simultaneous download for YouTube, for example, 
if active_youtube_downloads exceeds 2 simultaneous downloads, it will switch to the self-downloader.
"""
active_youtube_downloads = 0
YOUTUBE_DOWNLOAD_THRESHOLD = 0





# ╔════════════════════════════════════════╗
# ║           SPOTIFY CONFIGURATION        ║
# ╚════════════════════════════════════════╝


# Spotify API credentials
CLIENT_ID = os.getenv('CLIENT_ID')
CLIENT_SECRET = os.getenv('CLIENT_SECRET')
REDIRECT_URL = os.getenv('REDIRECT_URL')
DEFAULT_DAILY_COUNT_SPOTIFY=10

# ╔════════════════════════════════════════╗
# ║           TELEGRAM CONFIGURATION       ║
# ╚════════════════════════════════════════╝

# Telegram API credentials
API_ID = os.getenv('API_ID')

API_ID = int(API_ID) 

API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')



# Validate required environment variables
if not all([API_ID, API_HASH, BOT_TOKEN]):
    raise ValueError("Missing required environment variables. Please check your .env file.")



# This token is for the admin bot inside the channel to check for forced user joins.
ADMIN_BOT_TOKEN = os.getenv('ADMIN_BOT_TOKEN') 


# telegram channels for joining to use the bot.
CHANNELS = os.getenv('CHANNELS')  # Comma-separated list, e.g. "@ch1,@ch2"
if CHANNELS:
    CHANNELS = [c.strip() for c in CHANNELS.split(',') if c.strip()]
else:
    CHANNELS = []


"""
List of admins' numeric IDs for performing a series of tasks such as:

 1 broad cast messages to all users 
 
 2 confirming payments by bot admins.
 
 3 sending backup databases to admin.
 
""" 

# Admin IDs (replace with your Telegram user ID)
ADMIN_IDS=[5019214713]




# ╔════════════════════════════════════════╗
# ║       SELF DOWNLOADER CONFIGURATION    ║
# ╚════════════════════════════════════════╝

youtube_selfbot_bot_username='@TopSaverBot'

sessions_dir=os.getenv('SESSIONS_DIR', '/home/pilo/projects/media_dl/self_sessions1')  # Directory for self-bot sessions
accounts_json=os.getenv('ACCOUNTS_JSON', '/home/pilo/projects/media_dl/self_sessions1/sessions_meta.json')  # Path to accounts JSON file
group_chat_id=os.getenv('GROUP_CHAT_ID', '@hjjhhjhjfg')  # Telegram group chat ID for sending file from self-bots




# ╔════════════════════════════════════════╗
# ║       SHAZAM DOWNLOADER                ║
# ╚════════════════════════════════════════╝

# مسیر اختصاصی شازم
shazam_route_bot_username = os.getenv('SHAZAM_ROUTE_BOT_USERNAME', '@YtbAudioBot')
try:
    SHAZAM_ROUTE_THRESHOLD = int(os.getenv('SHAZAM_ROUTE_THRESHOLD', 0))
except Exception:
    SHAZAM_ROUTE_THRESHOLD = 3
SHAZAM_ROUTE_MEDIA_FILTER = os.getenv('SHAZAM_ROUTE_MEDIA_FILTER', 'audio_only')
if SHAZAM_ROUTE_MEDIA_FILTER:
    SHAZAM_ROUTE_MEDIA_FILTER = SHAZAM_ROUTE_MEDIA_FILTER.strip() or None




# Generic downloader patterns and bot_username

GENERIC_PATTERNS_FILE = os.getenv('GENERIC_PATTERNS_FILE', 'generic_patterns.json')


_DEFAULT_GENERIC_PATTERNS = [
    {"pattern": r"https://t\.me/[\w\d_]+/s/\d+", "bot_username": "@Dl_telegram_Robot"},
    {"pattern": r"https?://(?:www\.)?threads\.(?:net|com)/.*/post/[^/?#]+(?:\?[^\s]*)?", "bot_username": "@abbasaghaa_bot"},
    {"pattern": r"https?://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+/?(?:\?[^\s]*)?", "bot_username": "@allsaverbot"},
    {"pattern": r"https?://(?:www\.)?instagram\.com/stories/[^/]+/\d+/?(?:\?[^\s]*)?", "bot_username": "@allsaverbot"},
    {"pattern": r"^https:\/\/t\.me\/([A-Za-z0-9_]{5,32})\/(\d+)$", "bot_username": "@ForwardLockBot"},
    {"pattern": r"https?://(www\.)?(twitter|x)\.com/.+/status/\d+", "bot_username": "@RegaTwitter_Bot"},
    
    
    {"pattern": r"https?://(?:www\.)?castbox\.fm/[^\s]+", 
     
    "bot_username": "@DownloadiaBot",
     
      "media_filter": "audio_only"  # فقط صوت
     },
    
     {"pattern": r"https?://(www\.)?(rj\.app/(m|v)/[\w\d]+|((play\.)?radiojavan\.com)/.+)", 
     
    "bot_username": "@DownloadiaBot",
     
      "media_filter": "audio_only"  # فقط صوت
      
     },
     {"pattern": r"https?://open\.spotify\.com/track/[A-Za-z0-9]+(?:\?[^\s]*)?$", 
     
    "bot_username": "@RegaSpotify_Bot",
     
      "media_filter": "audio_only"  # فقط صوت
      
     },
      {"pattern": r"https?:\/\/(www\.)?tiktok\.com\/@[A-Za-z0-9._]+\/video\/\d+", 
     
    "bot_username": "@RegaTikTok_Bot"
    
      
     }
     
]


def load_generic_patterns():
    try:
        if GENERIC_PATTERNS_FILE and os.path.isfile(GENERIC_PATTERNS_FILE):
            with open(GENERIC_PATTERNS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
    except Exception:
        pass
    return _DEFAULT_GENERIC_PATTERNS


generic_patterns = load_generic_patterns()





# ========== GENERAL CONFIGURATION ==========


# List of authorized domains for simple_downloader

allowed_domains = [
    "snapchat.com",
    "facebook.com",
    "soundcloud.com",
    # "tiktok.com",
                ]
                    



# Optional: Proxy, Channels, Cookie Directory
PROXY = os.getenv('PROXY')  # e.g. socks5://user:pass@host:port

# cookie directory  for Twitter downloader.
COOKIE_DIR_TWITTER = os.getenv('COOKIE_DIR_TWITTER', 'cookies_twitter/')


# User database file
USER_DB_FILE = os.getenv('USER_DB_FILE', 'user_db.sqlite3')  # Path to user database file

# To fill these values , need to get API. (Spotify Developer Account)


# Instagram cookie file path
INSTAGRAM_COOKIE_dir_insta = os.getenv('INSTAGRAM_COOKIE_dir_insta', 'cookies/cookies_instagram/cookies.txt')

interval_hours=12 # Interval hours for backup databases (send backup files to admin in telegram bot)

# ===== Admin-editable settings overrides =====
ADMIN_SETTINGS_FILE = os.getenv('ADMIN_SETTINGS_FILE', 'admin_settings.json')

def _apply_admin_overrides():
    try:
        if ADMIN_SETTINGS_FILE and os.path.isfile(ADMIN_SETTINGS_FILE):
            with open(ADMIN_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    global youtube_selfbot_bot_username, ADMIN_IDS, allowed_domains
                    if 'youtube_selfbot_bot_username' in data and isinstance(data['youtube_selfbot_bot_username'], str):
                        youtube_selfbot_bot_username = data['youtube_selfbot_bot_username']
                    if 'ADMIN_IDS' in data and isinstance(data['ADMIN_IDS'], list):
                        try:
                            ADMIN_IDS = [int(x) for x in data['ADMIN_IDS']]
                        except Exception:
                            pass
                    if 'allowed_domains' in data and isinstance(data['allowed_domains'], list):
                        allowed_domains = [str(x) for x in data['allowed_domains'] if isinstance(x, str)]
    except Exception:
        pass

_apply_admin_overrides()