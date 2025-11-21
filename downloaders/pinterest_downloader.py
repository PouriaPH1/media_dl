import os
import re
import uuid
import random
import logging
import mimetypes
import requests
import asyncio
from bs4 import BeautifulSoup
from telethon import events, Button
from typing import Optional, Pattern
from config import PROXY, CHANNELS, COOKIE_DIR, ADMIN_BOT_TOKEN
import tempfile

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

class PinterestDownloader():
    def __init__(self, client):
        self.client = client
        self.url_pattern = self.get_url_pattern()
        self.download_status = {}
        self.user_data = {}
        self.executor = None  # Will be set on first use
        self.COOKIE_DIR = COOKIE_DIR or "cookies/"
        self.DOWNLOAD_SIZE_LIMIT = 1.5 * 1024 * 1024 * 1024  # 1.5 GB
        self.channels = CHANNELS 
        self.ADMIN_BOT_TOKEN = ADMIN_BOT_TOKEN
        self.proxy = PROXY

    def get_url_pattern(self) -> Pattern:
        return re.compile(r"(https?://)?(www\.)?(pin\.it|pinterest\.com)/.+")

    def get_random_cookie_file(self) -> Optional[str]:
        cookie_files = [os.path.join(self.COOKIE_DIR, f) for f in os.listdir(self.COOKIE_DIR) if f.endswith('.txt')]
        if not cookie_files:
            logger.warning("No cookie files found!")
            return None
        return random.choice(cookie_files)

    def sanitize_filename(self, filename):
        sanitized_name = "".join(c for c in filename if c.isalnum() or c in (" ", ".", "_")).rstrip()
        unique_id = str(uuid.uuid4())
        return f"{unique_id}_{sanitized_name}"

    def get_media_type(self, file_path: str):
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type:
            if mime_type.startswith('video'):
                return 'video'
            elif mime_type.startswith('image'):
                return 'image'
        return 'document'

    async def send_media(self, event, media_file, media_title):
        media_type = self.get_media_type(media_file)
        try:
            if media_type == 'video':
                await self.client.send_file(event.chat_id, media_file, caption=media_title)
            elif media_type == 'image':
                await self.client.send_file(event.chat_id, media_file, caption=media_title)
            else:
                await self.client.send_file(event.chat_id, media_file, caption=media_title)
        except Exception as e:
            logger.error(f"Error sending media: {e}")
            await event.reply("An error occurred while sending the media.")

    def download_video(self, url, output_path):
        import yt_dlp
        cookie_file = self.get_random_cookie_file()
        ydl_opts = {
            'outtmpl': f'{output_path}/%(title)s.%(ext)s',
            'cookiefile': cookie_file,
        }
        if self.proxy:
            ydl_opts['proxy'] = self.proxy
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            title = info_dict['title']
            file_ext = info_dict['ext']
            sanitized_filename = self.sanitize_filename(title)
            downloaded_file_path = os.path.join(output_path, f"{sanitized_filename}.{file_ext}")
            os.rename(os.path.join(output_path, f"{title}.{file_ext}"), downloaded_file_path)
            return downloaded_file_path, info_dict

    def download_file(self, url, dest):
        try:
            response = requests.get(url, stream=True)
            response.raise_for_status()
            with open(dest, 'wb') as file:
                for chunk in response.iter_content(chunk_size=8192):
                    file.write(chunk)
            return True
        except Exception as e:
            logger.error(f"Error downloading file: {e}")
            return False

    def download_pinterest_image(self, link, save_path):
        try:
            response = requests.get(link, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            image_tag = soup.find('meta', property='og:image')
            image_url = image_tag['content'] if image_tag else None
            if image_url:
                sanitized_filename = self.sanitize_filename(f'{os.path.basename(save_path)}.jpg')
                full_save_path = os.path.join(os.path.dirname(save_path), sanitized_filename)
                if self.download_file(image_url, full_save_path):
                    logger.info(f"Pinterest image downloaded successfully as {full_save_path}")
                    return full_save_path
                else:
                    return None
            else:
                logger.error("Image URL not found.")
                return None
        except Exception as e:
            logger.error(f"Error downloading Pinterest image: {str(e)}")
            return None

    async def handle_video_downloader(self, event, media_url):
        user_id = event.sender_id
        if self.download_status.get(user_id, 0) == 1:
            await event.reply("Downloading in progress... Please wait until the current download is complete.")
            return
        try:
            self.download_status[user_id] = 1
            await event.reply("Fetching media details... Please wait.")
            with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                download_path = tmp_dir
                if not self.executor:
                    from concurrent.futures import ThreadPoolExecutor
                    self.executor = ThreadPoolExecutor(max_workers=10)
                loop = asyncio.get_event_loop()
                media_file, media_info = await loop.run_in_executor(
                    self.executor, self.download_video, media_url, download_path)
                media_title = media_info.get('title', 'No Title') + "\n\nDownloaded by🚀 @media_dlrobot"
                await self.send_media(event, media_file, media_title)
        except Exception as e:
            error_message = str(e)
            if "No video formats found" in error_message or "Unsupported URL" in error_message:
                await event.reply("This Pinterest link does not contain a video. Please select image type or send a valid video link.")
            else:
                await event.reply(f"An error occurred while downloading the video: {error_message}")
        finally:
            self.download_status[user_id] = 0

    async def handle_image_downloader(self, event, media_url):
        user_id = event.sender_id
        if self.download_status.get(user_id, 0) == 1:
            await event.reply("Downloading in progress... Please wait until the current download is complete.")
            return
        try:
            self.download_status[user_id] = 1
            await event.reply("Fetching image details... Please wait.")
            with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                download_path = tmp_dir
                if not self.executor:
                    from concurrent.futures import ThreadPoolExecutor
                    self.executor = ThreadPoolExecutor(max_workers=10)
                loop = asyncio.get_event_loop()
                image_file = await loop.run_in_executor(
                    self.executor, self.download_pinterest_image, media_url, download_path)
                if image_file:
                    media_title = "Downloaded by🚀 @media_dlrobot"
                    await self.send_media(event, image_file, media_title)
                else:
                    await event.reply("Failed to download the image. Please try again.")
        except Exception as e:
            await event.reply(f"An error occurred: {str(e)}")
        finally:
            self.download_status[user_id] = 0

    async def send_channel_links(self, event):
        channels = [
            {"name": channel.lstrip("@"), "url": f"https://t.me/{channel.lstrip('@')}"} for channel in self.channels
        ]
        buttons = [[Button.url(channel["name"], channel["url"])] for channel in channels]
        await event.reply(
            "To use the bot, subscribe to the following channels and resend the video link.",
            buttons=buttons
        )

    def join_checker(self, user_id):
        for channel in self.channels:
            try:
                response = requests.get(f"https://api.telegram.org/bot{self.ADMIN_BOT_TOKEN}/getChatMember?chat_id={channel}&user_id={user_id}")
                if response.status_code == 200:
                    member = response.json()
                    if member['result']['status'] in ["kicked", "left"]:
                        return False
                else:
                    return False
            except Exception as e:
                logger.error(f"Error checking membership: {e}")
                return False
        return True

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=self.url_pattern))
        async def handle_message(event):
            user_id = event.sender_id
            media_url = event.text.strip()
            if self.join_checker(user_id):
                # Ask user whether they want to download image or video
                buttons = [
                    [Button.inline("Video", b"download_video")],
                    [Button.inline("Image", b"download_image")]
                ]
                await event.reply(
                    "Please choose the media type you want to download:",
                    buttons=buttons
                )
                self.user_data[user_id] = {'media_url': media_url}
            else:
                await self.send_channel_links(event)

        @self.client.on(events.CallbackQuery(pattern=re.compile(br"^download_")))
        async def download_media_choice(event):
            user_id = event.sender_id
            data = event.data.decode("utf-8")
            user_choice = data.split('_')[1]  # "video" or "image"
            media_url = self.user_data.get(user_id, {}).get('media_url')
            if not media_url:
                await event.reply("No media link found. Please send a Pinterest link first.")
                return
            if user_choice == "video":
                await self.handle_video_downloader(event, media_url)
            elif user_choice == "image":
                await self.handle_image_downloader(event, media_url)
            else:
                await event.reply("Invalid selection. Please choose again.")

    async def download_media(self, url: str) -> str:
        """
        Download media from the given Pinterest URL and return the path to the downloaded file.
        This method is required by BaseDownloader.
        """
        download_path = "downloads"
        os.makedirs(download_path, exist_ok=True)
        if not self.executor:
            from concurrent.futures import ThreadPoolExecutor
            self.executor = ThreadPoolExecutor(max_workers=10)
        loop = asyncio.get_event_loop()
        media_file, _ = await loop.run_in_executor(
            self.executor, self.download_video, url, download_path
        )
        return media_file 