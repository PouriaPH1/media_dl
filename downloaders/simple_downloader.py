import os
import re
import logging
from yt_dlp import YoutubeDL
from telethon import events, Button
import requests
import urllib.parse
from config import PROXY

logger = logging.getLogger(__name__)

def sanitize_filename(title: str, max_length: int = 200) -> str:
    """Sanitize and truncate filename to prevent filesystem errors."""
    import string
    # Remove/replace invalid filename characters
    valid_chars = "-_.() %s%s" % (string.ascii_letters, string.digits)
    sanitized = ''.join(c if c in valid_chars else '_' for c in title)
    # Remove multiple underscores
    sanitized = re.sub(r'_+', '_', sanitized)
    # Truncate if too long (leave room for extension)
    if len(sanitized) > max_length:
        sanitized = sanitized[:max_length].rstrip('_')
    return sanitized

class SimpleDownloader:
    def __init__(self, client, url_pattern, channels=None, admin_bot_token=None):
        self.client = client
        self.url_pattern = url_pattern
        # Compile regex to match any allowed domain in a URL
        allowed_domains_regex = r"|".join([re.escape(domain) for domain in self.url_pattern])
        self.compiled_url_pattern = re.compile(rf"https?://[^\s]*({allowed_domains_regex})", re.IGNORECASE)
        self.channels = channels or []
        self.ADMIN_BOT_TOKEN = admin_bot_token
        self.proxy=PROXY

    def set_allowed_domains(self, domains):
        """Update allowed domains at runtime and rebuild compiled regex."""
        try:
            # filter empties and whitespace
            self.url_pattern = [d.strip() for d in list(domains) if isinstance(d, str) and d.strip()]
            allowed_domains_regex = r"|".join([re.escape(domain) for domain in self.url_pattern])
            self.compiled_url_pattern = re.compile(rf"https?://[^\s]*({allowed_domains_regex})", re.IGNORECASE)
            return True
        except Exception as e:
            logger.error(f"Failed to update allowed domains: {e}")
            return False

    def join_checker(self, user_id: int) -> list:
        not_joined_channels = []
        for channel in self.channels:
            try:
                channel_username = channel.lstrip('@')
                response = requests.get(
                    f"https://api.telegram.org/bot{self.ADMIN_BOT_TOKEN}/getChatMember?chat_id=@{channel_username}&user_id={user_id}",
                    timeout=10,
                    verify=True
                )
                if response.status_code == 200:
                    status = response.json()['result']['status']
                    if status not in ["member", "administrator", "creator"]:
                        not_joined_channels.append(channel)
                else:
                    not_joined_channels.append(channel)
                    logger.warning(f"Failed to check membership for channel {channel}: {response.status_code}")
                    continue
            except requests.exceptions.SSLError as e:
                logger.error(f"SSL Error checking channel membership: {e}")
                not_joined_channels.append(channel)
                continue
            except requests.exceptions.RequestException as e:
                logger.error(f"Request error checking channel membership: {e}")
                not_joined_channels.append(channel)
                continue
            except Exception as e:
                logger.error(f"Error checking channel membership: {e}")
                not_joined_channels.append(channel)
                continue
        return not_joined_channels

    async def send_channel_links(self, event, not_joined_channels):
        try:
            if not not_joined_channels:
                return True
            buttons = [[Button.url(channel[1:], f"https://t.me/{channel[1:]}")] for channel in not_joined_channels]
            buttons.append([Button.inline("عضو شدم✅", b"check_membership")])
            await event.respond("برای استفاده از ربات لطفا در چنل های زیر عضو شوید:", buttons=buttons)
            return False
        except Exception as e:
            logger.error(f"Error sending channel links: {e}")
            try:
                await event.respond("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            except:
                pass
            return True

    async def download_media(self, url: str) -> str:
        download_path = "downloads"
        os.makedirs(download_path, exist_ok=True)
        # First, get info to sanitize the title
        info_opts = {
            'quiet': True,
            'noplaylist': True,
        }
        try:
            with YoutubeDL(info_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                # Check if the link is a playlist/album/set
                if (isinstance(info, dict) and (info.get('_type') == 'playlist' or 'entries' in info)):
                    raise Exception("❌ این لینک شامل پلی‌لیست یا آلبوم است و فقط ترک تکی قابل دانلود است.")
                # Get title and sanitize it
                original_title = info.get('title', 'video')
                sanitized_title = sanitize_filename(original_title)
                # Get extension
                ext = info.get('ext', 'mp4')
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            raise Exception(f"Error: {str(e)}")
        
        # Now download with sanitized filename
        ydl_opts = {
            'outtmpl': os.path.join(download_path, f'{sanitized_title}.%(ext)s'),
            'quiet': True,
            'noplaylist': True,
            'restrictfilenames': True,
            'proxy':self.proxy
        }
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                file_path = ydl.prepare_filename(info)
                # Final safety check: if filename is still too long, rename it
                filename = os.path.basename(file_path)
                if len(filename) > 240:
                    ext = os.path.splitext(file_path)[1]
                    dir_path = os.path.dirname(file_path)
                    # Use timestamp as fallback
                    import time
                    safe_name = f"media_{int(time.time())}{ext}"
                    new_path = os.path.join(dir_path, safe_name)
                    if os.path.exists(file_path):
                        os.rename(file_path, new_path)
                        file_path = new_path
                        logger.info(f"Renamed file due to excessive length: {filename[:50]}... -> {safe_name}")
            return file_path
        except Exception as e:
            logger.error(f"Error downloading media: {e}")
            raise Exception(f"Error: {str(e)}")

    def is_soundcloud_single_track(self, url):
        parsed = urllib.parse.urlparse(url)
        netloc = parsed.netloc.lower()
        path_parts = [p for p in parsed.path.split('/') if p]
        # دامنه‌های کوتاه‌کننده
        short_domains = [
            'on.soundcloud.com', 'm.soundcloud.com',
            'soundcloud.app.goo.gl', 'soundcloud.page.link'
        ]
        if any(domain in netloc for domain in short_domains):
            # فقط کافی است path خالی نباشد
            return len(path_parts) == 1
        if 'soundcloud.com' in netloc:
            # فقط لینک‌های ترک تکی مثل /artist/track
            if len(path_parts) == 2 and path_parts[0] and path_parts[1] and path_parts[0] != "sets":
                return True
            return False
        return True  # اگر لینک SoundCloud نیست، بقیه لینک‌ها رو اجازه بده


    def is_castbox_single_track(self, url):
        parsed = urllib.parse.urlparse(url)
        if 'castbox.fm' in parsed.netloc:
            # Castbox single episode: /episode/...
            # Block /channel/ and /series/ and others
            path_parts = [p for p in parsed.path.split('/') if p]
            if len(path_parts) >= 2 and path_parts[0] == 'episode':
                return True
            return False
        return True  # Not castbox, allow

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=self.compiled_url_pattern))
        async def handle_simple(event):
            user_id = event.sender_id
            url = event.text.strip()
            
            
            if self.channels and self.ADMIN_BOT_TOKEN:
                not_joined = self.join_checker(user_id)
                if not_joined:
                    await self.send_channel_links(event, not_joined)
                    return
            # Strict SoundCloud single track check
            if not self.is_soundcloud_single_track(url):
                await event.reply("❌ فقط لینک ترک تکی SoundCloud قابل دانلود است. پلی‌لیست، پروفایل یا ست مجاز نیست.")
                return
            await event.reply("در حال دانلود ...")
            try:
                file_path = await self.download_media(url)
                await self.client.send_file(event.chat_id, file_path, caption="Downloaded by🚀 @media_dlrobot")
            except Exception as e:
                await event.reply(str(e)) 