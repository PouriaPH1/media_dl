import os
import re
import requests
import logging
from telethon import events, Button
from config import PROXY, CHANNELS, ADMIN_BOT_TOKEN
import tempfile
import json

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

class RadioJavanDownloader():
    def __init__(self, client):
        self.client = client
        self.url_pattern = self.get_url_pattern()
        self.channels = CHANNELS
        self.proxy = PROXY
        self.ADMIN_BOT_TOKEN = ADMIN_BOT_TOKEN

    def get_url_pattern(self):
        # پشتیبانی از لینک‌های آهنگ و ویدیو (هم /m/ و هم /v/ و دامنه‌های مختلف)
        return re.compile(r"https?://(www\.)?(rj\.app/(m|v)/[\w\d]+|((play\.)?radiojavan\.com)/.+)")

    
    async def send_channel_links(self, event):
        channels = [
            {"name": channel.lstrip("@"), "url": f"https://t.me/{channel.lstrip('@')}"}
            for channel in self.channels
        ]
        buttons = [[Button.url(channel["name"], channel["url"])] for channel in channels]
        await event.reply(
            "برای استفاده از ربات در چنل های زیر عضو شوید و لینک موزیک را دوباره ارسال کنید.",
            buttons=buttons
        )

    def join_checker(self, user_id):
        for channel in self.channels:
            try:
                response = requests.get(
                    f"https://api.telegram.org/bot{self.ADMIN_BOT_TOKEN}/getChatMember?chat_id={channel}&user_id={user_id}"
                )
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

    def fetch_html(self, url):
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, headers=headers)
        if resp.status_code != 200:
            raise Exception(f"Failed to fetch page: {resp.status_code}")
        return resp.text

    def extract_mp3_link_from_html(self, html):
        match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.DOTALL)
        if not match:
            raise Exception("__NEXT_DATA__ section not found!")
        data_json = match.group(1)
        data = json.loads(data_json)
        media = data["props"]["pageProps"]["media"]
        mp3_url = media.get("link")
        title = media.get("title") or media.get("song")
        artist = media.get("artist")
        return mp3_url, title, artist

    def resolve_short_url(self, url):
        if "rj.app" in url:
            headers = {"User-Agent": "Mozilla/5.0"}
            resp = requests.get(url, headers=headers, allow_redirects=True)
            return resp.url
        return url

    def sanitize_filename(self, name):
        return re.sub(r'[\\/:*?"<>|]', '', name)

    async def download_media(self, url: str) -> str:
        url = self.resolve_short_url(url)
        html = self.fetch_html(url)
        mp3_url, title, artist = self.extract_mp3_link_from_html(html)
        if not mp3_url:
            raise Exception("MP3 link not found in HTML!")
        base_name = f"{artist or ''} - {title or 'RadioJavan'}".strip()
        base_name = self.sanitize_filename(base_name)
        output_path = os.path.join("downloads", base_name + ".mp3")
        r = requests.get(mp3_url, stream=True)
        with open(output_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return output_path

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=self.url_pattern))
        async def handle_radiojavan(event):
            user_id = event.sender_id
            url = event.text.strip()
            if self.channels and not self.join_checker(user_id):
                await self.send_channel_links(event)
                return
            if not re.match(self.url_pattern, url):
                await event.reply("یک لینک معتبر ارسال کنید.")
                return
            await event.reply("درحال دانلود ...")
            try:
                with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                    file_path = await self.download_media(url)
                    await event.reply("درحال آپلود ...")
                    await self.client.send_file(event.chat_id, file_path, caption="Downloaded by🚀 @media_dlrobot")
            except Exception as e:
                await event.reply(str(e))