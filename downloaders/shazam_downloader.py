import os
import logging
import tempfile
import mimetypes
from telethon import events
from shazamio import Shazam
from downloaders.spotify_downloader import SpotifyDownloader
import config
import uuid
import asyncio
import yt_dlp

logger = logging.getLogger(__name__)

class ShazamDownloader:
    def __init__(self, client, **kwargs):
        self.client = client
        self.shazam = Shazam()
        self.download_status = {}
        self.spotify_downloader = SpotifyDownloader(client)
        self.youtube_selfbot_downloader = None
        # شناسه گروه سلف‌بات‌ها و تنظیمات مسیر از config یا kwargs
        self.group_chat_id = kwargs.get('group_chat_id', getattr(config, 'group_chat_id', None))
        self.shazam_route_bot_username = kwargs.get('shazam_route_bot_username', getattr(config, 'shazam_route_bot_username', None))
        self.shazam_route_threshold = kwargs.get('shazam_route_threshold', getattr(config, 'SHAZAM_ROUTE_THRESHOLD', 3))

    def set_selfbot_downloader(self, youtube_selfbot_downloader):
        self.youtube_selfbot_downloader = youtube_selfbot_downloader

    async def recognize_file(self, file_path: str):
        try:
            out = await self.shazam.recognize(file_path)
            track = out.get("track") or {}
            if not track:
                return None
            cover = None
            if 'images' in track and track['images'].get('coverart'):
                cover = track['images']['coverart']
            elif track.get('hub', {}).get('image'):
                cover = track['hub']['image']
            shazam_url = track.get('url')
            if not shazam_url:
                shazam_url = track.get('share', {}).get('href')
            return {
                "title": track.get("title"),
                "subtitle": track.get("subtitle"),
                "cover": cover,
                "shazam_url": shazam_url,
            }
        except Exception as e:
            logger.error(f"Shazam recognition error: {e}")
        return None

    def get_best_query(self, song_info: dict):
        q = song_info.get('title') or ''
        artist = song_info.get('subtitle') or ''
        if artist and artist.lower() not in q.lower():
            q = f"{q} {artist}"
        return q.strip()

    async def get_first_youtube_url(self, query: str, event=None) -> str:
        loop = asyncio.get_event_loop()
        def sync_search(q):
            ydl_opts = {'quiet': True, 'skip_download': True, 'extract_flat': 'in_playlist'}
            search = f"ytsearch1:{q}"
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(search, download=False)
                    logger.info(f"[ytsearch] yt_dlp info: {info}")
                    entries = info.get('entries') or []
                    url = None
                    if entries and entries[0]:
                        url = entries[0].get('webpage_url') or entries[0].get('url')
                        logger.info(f"[ytsearch] got url robust: {url}")
                        return url
                    url = info.get('webpage_url') or info.get('url')
                    logger.info(f"[ytsearch] fallback url robust: {url}")
                    return url
            except Exception as err:
                logger.error(f"[ytsearch] yt-dlp error: {err}")
                if event:
                    asyncio.run_coroutine_threadsafe(event.reply(f'[LOG] yt-dlp error: {err}'), loop)
                return None
        url = await loop.run_in_executor(None, sync_search, query)
        return url

    async def branch_download(self, event, song_info, youtube_url, query):
        # =============================
        # DIRECT YOUTUBE DOWNLOAD ONLY (Called if already below threshold!)
        # =============================
        import config
        try:
            config.active_youtube_downloads += 1
            try:
                await event.reply("⏳ درحال دانلود  ... لطفاً صبر کنید.")
                import tempfile, os
                with tempfile.TemporaryDirectory(dir="./downloads") as tmp_dir:
                    output_path = os.path.join(tmp_dir, f"{query}.mp3")
                    import asyncio
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self.spotify_downloader.download_from_youtube, query, output_path)
                    if os.path.exists(output_path):
                        await event.reply("Downloaded by🚀 @media_dlrobot", file=output_path)
                    else:
                        await event.reply("❌ دانلود موفق نبود.")
            finally:
                config.active_youtube_downloads -= 1
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f'[DOWNLOADS after dec NORMAL] {config.active_youtube_downloads}')
        except Exception as e:
            config.active_youtube_downloads -= 1
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f'[DOWNLOADS after dec ERR-EXCEPT] {config.active_youtube_downloads}')
            raise

    async def branch_route_to_general(self, event, youtube_url):
        """
        فقط لینک را با uuid مخصوص شازم داخل گروه ارسال می‌کنیم.
        SelfManager تشخیص و پردازش را انجام می‌دهد.
        """
        if not self.group_chat_id:
            await event.reply("تنظیمات روت برای شازم کامل نیست (group_chat_id تنظیم نشده).")
            return
        user_id = event.sender_id
        # uuid با پیشوند برای تشخیص در SelfManager
        request_uuid = f"shazam-{uuid.uuid4()}"
        msg_text = f"{user_id}|{request_uuid}|{youtube_url}"
        await self.client.send_message(self.group_chat_id, msg_text)
      

    def register_handlers(self):
        @self.client.on(events.NewMessage(func=lambda e: e.file and (e.file.mime_type.startswith("audio") or e.file.mime_type.startswith("video"))))
        async def handle_voice_or_media(event):
            user_id = event.sender_id  # <-- define user_id at the top so it's always available
            # Only handle messages in private chats (bot dialog), ignore groups/channels
            try:
                if not getattr(event, 'is_private', False):
                    return
            except Exception:
                return

            await event.reply("در حال شناسایی آهنگ..")
          
            self.download_status[user_id] = 1
            mime = event.file.mime_type or ""
            ext = mimetypes.guess_extension(mime) or os.path.splitext(event.file.name or "")[1] or ".bin"
            # Pre-download size check: skip processing if larger than 50 MB
            try:
                file_size_bytes = getattr(event.file, 'size', None)
                if file_size_bytes is not None and file_size_bytes > 50 * 1024 * 1024:
                    await event.reply("❌ حجم فایل بیش از 50 مگابایت است و پردازش نمی‌شود.")
                    self.download_status[user_id] = 0
                    return
            except Exception:
                # If size is unavailable, proceed as before
                pass
            with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as temp:
                temp_path = temp.name
            try:
                await event.download_media(temp_path)
                song_info = await self.recognize_file(temp_path)
                if song_info and song_info["title"]:
                    msg = f"🎵 <b>{song_info['title']}</b>"
                    if song_info.get('subtitle'):
                        msg += f"\n👤 {song_info['subtitle']}"
                    links_line = ""
                    if song_info.get('shazam_url'):
                        links_line += f"<a href='{song_info['shazam_url']}'>Shazam Link</a>"
                    query = self.get_best_query(song_info)
                    youtube_url = await self.get_first_youtube_url(query, event)
                    if youtube_url:
                        if links_line:
                            links_line += " | "
                        links_line += f"<a href='{youtube_url}'>YouTube Link</a>"
                    if links_line:
                        msg += f"\n{links_line}"
                    cover = song_info['cover'] if song_info.get('cover') else None
                    await event.reply(msg, file=cover, parse_mode='html')
                    # تصمیم‌گیری: اگر تعداد دانلودهای فعال از آستانه بیشتر یا مساوی بود، به روت گروهی بفرست
                    active = getattr(config, 'active_youtube_downloads', 0)
                    threshold = getattr(config, 'SHAZAM_ROUTE_THRESHOLD', self.shazam_route_threshold or 3)
                    if active >= threshold and youtube_url:
                        await self.branch_route_to_general(event, youtube_url)
                    else:
                        await self.branch_download(event, song_info, youtube_url, query)
                else:
                    await event.reply("موفق به تشخیص نشد.")
            except Exception as e:
                logger.error(f"Error in media recognition: {e}")
                await event.reply("خطا در پردازش فایل.")
            finally:
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
                self.download_status[user_id] = 0
