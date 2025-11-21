import os
import re
import uuid
import logging
import asyncio
import requests
import tempfile
import json
from yt_dlp import YoutubeDL
from telethon import events, Button
from config import PROXY, CHANNELS, ADMIN_BOT_TOKEN, INSTAGRAM_COOKIE_dir_insta
from downloaders.SelfManager import SelfBotManager
import shutil

logger = logging.getLogger(__name__)

class LoggingTempDirectory(tempfile.TemporaryDirectory):
    def __enter__(self):
        self._path = super().__enter__()
        logger.info(f"Temporary directory created: {self._path}")
        return self._path
    def __exit__(self, exc_type, exc_val, exc_tb):
        path = self._path
        result = super().__exit__(exc_type, exc_val, exc_tb)
        if not os.path.exists(path):
            logger.info(f"Temporary directory deleted: {path}")
        else:
            logger.warning(f"Temporary directory NOT deleted: {path}")
        return result

def get_default_cookie_dir():
    try:
        return INSTAGRAM_COOKIE_dir_insta or "cookies/cookies_instagram"
    except Exception as e:
        logger.error(f"Error getting default cookie directory: {e}")
        return None

class InstagramDownloader:
    def __init__(self, client, selfbot_manager, bot_username, **kwargs):
        self.client = client
        if selfbot_manager is None:
            raise ValueError("selfbot_manager is required and must be shared between downloaders.")
        self.selfbot_manager = selfbot_manager
        self.bot_username = bot_username
        self.url_pattern = re.compile(r"https?://(www\.)?instagram\.com/(p|reel|tv|stories)/[a-zA-Z0-9_\-/.]+", re.IGNORECASE)
        self.channels = CHANNELS
        self.proxy = PROXY
        self.ADMIN_BOT_TOKEN = ADMIN_BOT_TOKEN
        self.download_status = {}
        self.cookie_dir = get_default_cookie_dir()
        self.cookie_index = 0
        self.api_key = "7398029078:uCDZTP971UjEbwp@Api_ManagerRoBot"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def get_next_cookie_file(self):
        try:
            if not self.cookie_dir or not os.path.exists(self.cookie_dir):
                return None
            cookie_files = [os.path.join(self.cookie_dir, f) for f in os.listdir(self.cookie_dir) if f.endswith('.txt')]
            if not cookie_files:
                return None
            cookie_file = cookie_files[self.cookie_index % len(cookie_files)]
            self.cookie_index = (self.cookie_index + 1) % len(cookie_files)
            return cookie_file
        except Exception as e:
            logger.error(f"Error getting next cookie file: {e}")
            return None

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

    def sanitize_filename(self, filename):
        return re.sub(r'[\\/:*?"<>|]', '_', filename)

    def download_with_yt_dlp(self, url, output_path):
        unique_id = uuid.uuid4().hex
        cookie_file = self.get_next_cookie_file()
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best',
            'outtmpl': f'{output_path}/{unique_id}.%(ext)s',
            'cookiefile': cookie_file,
            'quiet': True,
            'merge_output_format': 'mp4',
        }
        if self.proxy:
            ydl_opts['proxy'] = self.proxy
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            file_ext = info_dict.get('ext')
            if not file_ext:
                files = [f for f in os.listdir(output_path) if f.startswith(unique_id + ".")]
                if files:
                    file_ext = files[0].split('.')[-1]
                else:
                    file_ext = 'mp4'
            downloaded_file_path = os.path.join(output_path, f"{unique_id}.{file_ext}")
            return downloaded_file_path, info_dict

    def try_api_download(self, url, tmp_dir):
        content_type = "story" if "stories" in url else "post"
        api_url = f"https://api.fast-creat.ir/instagram?apikey={self.api_key}&type={content_type}&url={url}"
        try:
            response = requests.get(api_url, headers=self.headers, timeout=30)
            if response.status_code != 200:
                logger.error(f"API request failed with status {response.status_code}")
                return []
            api_response = response.json()
            logger.info(f"API Response: {json.dumps(api_response, indent=2)}")
            result = api_response.get("result", [])
            # --- اصلاح ساختار خروجی ---
            if isinstance(result, dict) and "result" in result:
                result = result["result"]
            if not result:
                return []
            if isinstance(result, dict):
                result = [result]
            downloaded_files = []
            for idx, media in enumerate(result):
                if isinstance(media, dict):
                    if media.get("video_url"):
                        media_url = media["video_url"]
                        file_extension = ".mp4"
                        media_response = requests.get(media_url, headers=self.headers, timeout=60)
                        if media_response.status_code == 200:
                            file_path = os.path.join(tmp_dir, f"instagram_media_{idx}{file_extension}")
                            with open(file_path, "wb") as f:
                                f.write(media_response.content)
                            downloaded_files.append({"path": file_path, "caption": media.get("caption", "")})
                    if media.get("video_img"):
                        media_url = media["video_img"]
                        file_extension = ".jpg"
                        media_response = requests.get(media_url, headers=self.headers, timeout=60)
                        if media_response.status_code == 200:
                            file_path = os.path.join(tmp_dir, f"instagram_media_{idx}_cover{file_extension}")
                            with open(file_path, "wb") as f:
                                f.write(media_response.content)
                            downloaded_files.append({"path": file_path, "caption": ""})
                    if media.get("image_url"):
                        media_url = media["image_url"]
                        file_extension = ".jpg"
                        media_response = requests.get(media_url, headers=self.headers, timeout=60)
                        if media_response.status_code == 200:
                            file_path = os.path.join(tmp_dir, f"instagram_media_{idx}{file_extension}")
                            with open(file_path, "wb") as f:
                                f.write(media_response.content)
                            downloaded_files.append({"path": file_path, "caption": media.get("caption", "")})
                    if media.get("media") and isinstance(media["media"], list):
                        for m_idx, m in enumerate(media["media"]):
                            media_url = m.get("url")
                            if not media_url:
                                continue
                            file_extension = ".mp4" if m.get("type") == "video" else ".jpg"
                            media_response = requests.get(media_url, headers=self.headers, timeout=60)
                            if media_response.status_code == 200:
                                file_path = os.path.join(tmp_dir, f"instagram_media_{idx}_{m_idx}{file_extension}")
                                with open(file_path, "wb") as f:
                                    f.write(media_response.content)
                                downloaded_files.append({"path": file_path, "caption": media.get("caption", "")})
                    if media.get("url"):
                        media_url = media["url"]
                        file_extension = ".mp4" if media.get("type") == "video" else ".jpg"
                        media_response = requests.get(media_url, headers=self.headers, timeout=60)
                        if media_response.status_code == 200:
                            file_path = os.path.join(tmp_dir, f"instagram_media_{idx}{file_extension}")
                            with open(file_path, "wb") as f:
                                f.write(media_response.content)
                            downloaded_files.append({"path": file_path, "caption": media.get("caption", "")})
            return downloaded_files
        except Exception as e:
            logger.error(f"Unexpected error in try_api_download: {e}")
            return []

    async def handle_instagram(self, event, url):
        user_id = event.sender_id
        if self.download_status.get(user_id, 0) == 1:
            await event.reply("در حال دانلود... لطفا صبر کنید تا دانلود قبلی کامل شود.")
            return
        self.download_status[user_id] = 1
        try:
            not_joined_channels = self.join_checker(user_id)
            if not_joined_channels:
                await self.send_channel_links(event, not_joined_channels)
                self.download_status[user_id] = 0
                return
            await event.reply("در حال دانلود از اینستاگرام... لطفا صبر کنید.")
            downloaded_files = []
            success = False
            # تلاش با API و yt-dlp فقط در پوشه موقت
            with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                downloaded_files = self.try_api_download(url, tmp_dir)
                if downloaded_files:
                    for item in downloaded_files:
                        await self.client.send_file(event.chat_id, item["path"], caption="Downloaded by🚀 @media_dlrobot")
                    success = True
                else:
                    # اگر API موفق نبود، تلاش با yt-dlp
                    loop = asyncio.get_event_loop()
                    try:
                        video_file, info_dict = await loop.run_in_executor(
                            None, self.download_with_yt_dlp, url, tmp_dir
                        )
                        await self.client.send_file(event.chat_id, video_file, caption="Downloaded by🚀 @media_dlrobot")
                        success = True
                    except Exception as e:
                        logger.error(f"Error in yt-dlp fallback: {e}")
                        success = False
            # اگر هیچکدام موفق نبودند، تلاش با سلف‌بات (بدون ساخت یا حذف پوشه)
            if not (downloaded_files or success):
                if self.selfbot_manager:
                    request_uuid = str(uuid.uuid4())
                    user_id = event.sender_id
                    group_id = self.selfbot_manager.group_chat_id
                    msg_text = f"{user_id}|{request_uuid}|{url}"
                    await self.client.send_message(group_id, msg_text)
                    future = self.selfbot_manager.get_or_create_future(request_uuid, bot_username=self.bot_username)
                    try:
                        message_ids = await asyncio.wait_for(future, timeout=60)
                        group_entity = await self.client.get_entity(self.selfbot_manager.group_chat_id)
                        for message_id in message_ids:
                            msg = await self.client.get_messages(group_entity, ids=message_id)
                            await self.client.send_file(
                                event.chat_id,
                                msg.media,
                                caption="Downloaded by🚀 @media_dlrobot"
                            )
                        success = True
                    except asyncio.TimeoutError:
                        logger.error("Timeout waiting for selfbot download.")
                        await event.reply("متاسفانه دانلود در زمان مناسب انجام نشد.")
                        self.download_status[user_id] = 0
                        success = False
                    finally:
                        self.selfbot_manager.pop_future(request_uuid)
                else:
                    success = False
            # اگر هیچ روشی موفق نبود، پیام خطا به کاربر بده
            if not (downloaded_files or success):
                await event.reply("متاسفانه دانلود موفق نشد.")
        except Exception as e:
            logger.error(f"Error in Instagram download: {e}")
            try:
                await self.client.send_message(5019214713, f"[InstagramDownloader Error]\nUser: {user_id}\nURL: {url}\nError: {str(e)}")
            except Exception as admin_err:
                logger.error(f"Failed to send error to admin: {admin_err}")
        finally:
            self.download_status[user_id] = 0

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=self.url_pattern))
        async def handle_message(event):
            url = event.text.strip()
            if url.startswith('@'):
                url = url[1:].strip()
            await self.handle_instagram(event, url) 