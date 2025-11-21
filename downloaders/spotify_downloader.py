import os
import random
import requests
import re
import tempfile
import logging
import asyncio
from yt_dlp import YoutubeDL
from telethon import events, Button
from spotipy import Spotify
from spotipy.oauth2 import SpotifyOAuth
from datetime import datetime, timedelta, date
from config import API_ID, API_HASH, BOT_TOKEN, ADMIN_BOT_TOKEN, CHANNELS, CLIENT_ID, CLIENT_SECRET, REDIRECT_URL,COOKIE_DIR
# Add import for proxy config
try:
    from config import PROXY
except ImportError:
    PROXY = None
# Add import for YouTube proxy config
try:
    from config import PROXY
except ImportError:
    PROXY = None
from downloaders.user_db import UserDB

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

class SpotifyDownloader:
    # DEFAULT_DAILY_COUNT_SPOTIFY = 10  # Max downloads per user per day

    def __init__(self, client):
        self.client = client
        self.spotify_pattern = re.compile(r"https?://open\.spotify\.com/(album|playlist)/[A-Za-z0-9]+(?:\?[^\s]*)?$")
        self.download_status = {}
        self.last_request_time_link = {}
        self.last_request_time_callback = {}
        self.channels = CHANNELS
        self.cookie_dir = COOKIE_DIR
        self.cookie_index = 0  # For round-robin cookie selection
        # self.daily_download_count = {}  # حذف خواهد شد
        # Add proxy support for Spotify
        proxy_settings = None
        if PROXY:
            proxy_settings = {
                'http': PROXY,
                'https': PROXY
            }
        self.sp = Spotify(auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URL,
            proxies=proxy_settings
        ))
        # --- اضافه کردن user_db برای منطق محدودیت ---
        self.user_db = UserDB()

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

    async def send_channel_links(self, event, not_joined_channels):
        if not not_joined_channels:
            return False
        buttons = [[Button.url(channel[1:], f"https://t.me/{channel[1:]}")] for channel in not_joined_channels]
        buttons.append([Button.inline("عضو شدم✅", b"check_membership")])
        await event.reply("برای استفاده از ربات لطفا در چنل های زیر عضو شوید:", buttons=buttons)
        return True

    def join_checker(self, user_id):
        not_joined_channels = []
        for channel in self.channels:
            try:
                response = requests.get(
                    f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/getChatMember?chat_id={channel}&user_id={user_id}",
                    timeout=10
                )
                if response.status_code == 200:
                    status = response.json()['result']['status']
                    if status not in ["member", "administrator", "creator"]:
                        not_joined_channels.append(channel)
                else:
                    not_joined_channels.append(channel)
                    continue
            except Exception as e:
                logger.error(f"Error checking channel membership: {e}")
                not_joined_channels.append(channel)
                continue
        return not_joined_channels

    def get_spotify_track_info(self, track_url):
        try:
            track_id = track_url.split("/")[-1].split("?")[0]
            track = self.sp.track(track_id)
            track_info = f"{track['artists'][0]['name']} - {track['name']}"
            logger.info(f"Retrieved track info: {track_info}")
            return track_info
        except Exception as e:
            logger.error(f"Failed to retrieve Spotify track info: {e}")
            return None

    def get_spotify_album_tracks(self, album_url):
        try:
            album_id = album_url.split("/")[-1].split("?", 1)[0]
            album = self.sp.album(album_id)
            tracks = album['tracks']['items']
            track_list = [f"{track['artists'][0]['name']} - {track['name']}" for track in tracks]
            logger.info(f"Retrieved {len(track_list)} tracks from album: {album['name']}")
            return track_list
        except Exception as e:
            logger.error(f"Failed to retrieve Spotify album tracks: {e}")
            return None

    def get_spotify_playlist_info(self, playlist_url):
        try:
            playlist_id = playlist_url.split("/")[-1].split("?")[0]
            playlist = self.sp.playlist(playlist_id)
            title = playlist.get('name', 'بدون عنوان')
            tracks = playlist['tracks']['items']
            track_list = []
            for item in tracks:
                track = item['track']
                track_list.append({
                    'id': track['id'],
                    'title': f"{track['artists'][0]['name']} - {track['name']}",
                    'query': f"{track['artists'][0]['name']} - {track['name']}"
                })
            return {
                'title': title,
                'playlist_url': playlist_url,
                'track_count': len(track_list),
                'tracks': track_list
            }
        except Exception as e:
            logger.error(f"Failed to retrieve Spotify playlist info: {e}")
            return None

    def get_spotify_album_info(self, album_url):
        try:
            album_id = album_url.split("/")[-1].split("?", 1)[0]
            album = self.sp.album(album_id)
            title = album.get('name', 'بدون عنوان')
            tracks = album['tracks']['items']
            track_list = []
            for track in tracks:
                track_list.append({
                    'id': track['id'],
                    'title': f"{track['artists'][0]['name']} - {track['name']}",
                    'query': f"{track['artists'][0]['name']} - {track['name']}"
                })
            return {
                'title': title,
                'album_url': album_url,
                'track_count': len(track_list),
                'tracks': track_list
            }
        except Exception as e:
            logger.error(f"Failed to retrieve Spotify album info: {e}")
            return None

    def get_quality_buttons(self):
        return [
            [Button.inline("128kbps", b"sp_playlist_quality_128")],
            [Button.inline("192kbps", b"sp_playlist_quality_192")],
            [Button.inline("256kbps", b"sp_playlist_quality_256")],
            [Button.inline("320kbps", b"sp_playlist_quality_320")],
        ]

    def get_playlist_menu_buttons(self):
        return [
            [Button.inline("🎬 دانلود همه ترک‌های پلی‌لیست", b"sp_playlist_download_all")],
            [Button.inline("📥 دانلود انتخابی ترک از لیست", b"sp_playlist_select_tracks")],
            [Button.inline("📚 دانلود دسته‌ای سفارشی", b"sp_playlist_custom_range")],
            [Button.inline("❌ لغو و بستن منو", b"sp_playlist_cancel")],
        ]

    def get_back_to_main_menu_button(self):
        return [Button.inline("🔙 بازگشت به منوی اصلی", b"sp_playlist_main_menu")]

    def get_range_buttons(self, track_count):
        return [
            [Button.inline("۵ ترک اول", b"sp_playlist_range_0_5")],
            [Button.inline("۱۰ ترک اول", b"sp_playlist_range_0_10")],
            [Button.inline("۲۰ ترک اول", b"sp_playlist_range_0_20")],
            [Button.inline("بازه دلخواه...", b"sp_playlist_ask_custom_range")],
            self.get_back_to_main_menu_button()
        ]

    def get_track_select_buttons(self, tracks, selected, page=0):
        buttons = []
        for idx, track in enumerate(tracks[page*10:(page+1)*10]):
            checked = "✅ " if any(t['id'] == track['id'] for t in selected) else ""
            buttons.append([Button.inline(f"{checked}{page*10+idx+1}. {track['title'][:40]}", f"sp_playlist_pick_{track['id']}".encode())])
        if (page+1)*10 < len(tracks):
            buttons.append([Button.inline("صفحه بعد ⏭️", f"sp_playlist_select_page_{page+1}".encode())])
        if page > 0:
            buttons.append([Button.inline("⏮️ صفحه قبل", f"sp_playlist_select_page_{page-1}".encode())])
        buttons.append([Button.inline("اتمام انتخاب و ادامه ⏭️", b"sp_playlist_finish_selection")])
        buttons.append(self.get_back_to_main_menu_button())
        return buttons

    def get_selected_tracks(self, all_tracks, selected_ids):
        return [t for t in all_tracks if t['id'] in selected_ids]

    def parse_range(self, text, track_count):
        import re
        match = re.match(r'^(\d+)[\s\-_,]+(\d+)$', text)
        if not match:
            return None, None
        start, end = int(match.group(1)), int(match.group(2))
        if start < 1 or end > track_count or start >= end:
            return None, None
        return start, end

    @staticmethod
    def is_spotify_playlist(url):
        return "playlist" in url

    def download_from_youtube(self, query, output_path):
        cookie_file = self.get_next_cookie_file()
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': output_path,
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'cookiefile': cookie_file,
            'noplaylist': True,
        }
        # Add proxy support for yt_dlp if PROXY is set
        if PROXY:
            ydl_opts['proxy'] = PROXY
        try:
            with YoutubeDL(ydl_opts) as ydl:
                ydl.download([f"ytsearch:{query}"])
            downloaded_file = f"{output_path}.mp3"
            if os.path.exists(downloaded_file):
                os.rename(downloaded_file, output_path)
                logger.info(f"Renamed file to: {output_path}")
            logger.info(f"Downloaded and saved track as: {output_path}")
        except Exception as e:
            logger.error(f"Failed to download from YouTube: {e}")
            logger.error(f"Yt-dlp options: {ydl_opts}")
            raise e

    async def check_and_update_limit(self, user_id, track_count=1):
        import datetime
        today = datetime.date.today().strftime('%Y-%m-%d')
        is_vip = await self.user_db.is_vip(user_id)
        if is_vip:
            DEFAULT_DAILY_COUNT = 50
            DEFAULT_DAILY_SIZE_SPOTIFY = 20 * 1024 * 1024 * 1024
        else:
            from config import DEFAULT_DAILY_COUNT_SPOTIFY, DAILY_SIZE_LIMIT
            DEFAULT_DAILY_COUNT = DEFAULT_DAILY_COUNT_SPOTIFY
            DEFAULT_DAILY_SIZE_SPOTIFY = DAILY_SIZE_LIMIT
        limits = await self.user_db.get_limits(user_id, today, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE_SPOTIFY)
        remaining_bonus_count = limits['bonus_count']
        remaining_daily_count = DEFAULT_DAILY_COUNT - limits['daily_count']
        # اگر سهمیه روزانه و بونس هر دو تمام شده باشد False برگردان
        if remaining_daily_count + remaining_bonus_count < track_count:
            return False, limits, remaining_daily_count, remaining_bonus_count
        # اگر سهمیه روزانه کافی باشد
        if remaining_daily_count >= track_count:
            return True, limits, track_count, 0
        # اگر فقط از بونس استفاده شد (سهمیه روزانه صفر)
        elif remaining_daily_count == 0 and remaining_bonus_count >= track_count:
            return True, limits, 0, track_count
        # اگر بخشی از بونس لازم است
        elif remaining_daily_count < track_count:
            use_bonus = track_count - remaining_daily_count
            return True, limits, remaining_daily_count, use_bonus
        return True, limits, 0, 0

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=self.spotify_pattern))
        async def handle_spotify_link(event):
            user_id = event.sender_id
            url = event.text.strip()
            current_time = datetime.now()
            if user_id in self.last_request_time_link:
                time_diff = current_time - self.last_request_time_link[user_id]
                remaining_time = timedelta(seconds=30) - time_diff
                if remaining_time > timedelta(seconds=0):
                    remaining_seconds = int(remaining_time.total_seconds())
                    await event.reply(f"Please wait {remaining_seconds} seconds before sending another request.")
                    return
            self.last_request_time_link[user_id] = current_time
            if f'{user_id}_spotify' in self.download_status and self.download_status[f'{user_id}_spotify'].get('downloading', False):
                await event.reply("Downloading... Please wait until the previous download is finished.")
                return
            # Daily limit check (async, DB-based)
            ok, limits, remaining_daily_count, remaining_bonus_count = await self.check_and_update_limit(user_id, 1)
            if not ok:
                is_vip = limits.get('is_vip') == 1 and limits.get('vip_expiry')
                if is_vip:
                    plan = 'ویژه (VIP)'
                    max_count = 50
                else:
                    plan = 'عادی'
                    from config import DEFAULT_DAILY_COUNT_SPOTIFY
                    max_count = DEFAULT_DAILY_COUNT_SPOTIFY
                await event.reply(f"🚫 شما به محدودیت روزانه {max_count} دانلود ({plan}) رسیده‌اید.\nبرای دور زدن این محدودیت، می‌توانید با دعوت دوستان خود به ربات جایزه بگیرید یا حساب خود را به ویژه (VIP) ارتقا دهید.\nلطفاً فردا دوباره تلاش کنید یا از این روش‌ها استفاده کنید.")
                return
            self.download_status[f'{user_id}_spotify'] = {"downloading": True}
            try:
                if self.channels:
                    not_joined_channels = self.join_checker(user_id)
                    if not not_joined_channels:
                        pass
                    else:
                        await self.send_channel_links(event, not_joined_channels)
                        self.download_status[f'{user_id}_spotify']["downloading"] = False
                        return
                # Unified logic for playlist and album
                if "spotify" in url and (self.is_spotify_playlist(url) or 'album' in url):
                    # --- حذف چک محدودیت برای کل ترک‌ها در نمایش اطلاعات آلبوم/پلی‌لیست ---
                    if self.is_spotify_playlist(url):
                        info = self.get_spotify_playlist_info(url)
                        info_type = 'playlist'
                    else:
                        info = self.get_spotify_album_info(url)
                        info_type = 'album'
                    if not info:
                        await event.reply("خطا در بازیابی اطلاعات.")
                        return
                    self.download_status[f'{user_id}_spotify'] = {
                        f'{info_type}_info': info,
                        f'{info_type}_url': url,
                        'playlist_selected_tracks': [],
                        'playlist_mode': True,
                        'info_type': info_type
                    }
                    info_msg = f"📃 <b>اطلاعات {'پلی‌لیست' if info_type == 'playlist' else 'آلبوم'}:</b>\n"
                    info_msg += f"<b>عنوان:</b> {info['title']}\n"
                    info_msg += f"<b>تعداد ترک:</b> {info['track_count']}\n"
                    info_msg += f"<b>لینک:</b> <a href=\"{url}\">مشاهده در اسپاتیفای</a>"
                    await event.reply(info_msg, buttons=self.get_playlist_menu_buttons(), parse_mode="html")
                    return
                # Single track logic remains
                elif "spotify" in url:
                    track_info = self.get_spotify_track_info(url)
                    if not track_info:
                        await event.reply("اطلاعات آهنگ بازیابی نشد.")
                        return
                    ok, limits, daily_count_to_use, bonus_count_to_use = await self.check_and_update_limit(user_id, 1)
                    if not ok:
                        is_vip = limits.get('is_vip') == 1 and limits.get('vip_expiry')
                        if is_vip:
                            plan = 'ویژه (VIP)'
                            max_count = 50
                        else:
                            plan = 'عادی'
                            from config import DEFAULT_DAILY_COUNT_SPOTIFY
                            max_count = DEFAULT_DAILY_COUNT_SPOTIFY
                        await event.reply(f"🚫 شما به محدودیت روزانه {max_count} دانلود ({plan}) رسیده‌اید. لطفاً فردا دوباره تلاش کنید یا حساب خود را ارتقا دهید.")
                        return
                    await event.reply(f"Downloading: {track_info}...")
                    with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                        output_path = os.path.join(tmp_dir, f"{track_info}.mp3")
                        try:
                            await asyncio.get_event_loop().run_in_executor(None, self.download_from_youtube, track_info, output_path)
                            if not os.path.exists(output_path):
                                await event.reply("دانلود با خطا مواجه شد، فایل پیدا نشد.")
                                return
                            await self.client.send_file(event.chat_id, output_path, caption="Downloaded by🚀 @media_dlrobot")
                            logger.info(f"Sent {track_info} to the user.")
                            # افزایش شمارنده بعد از ارسال موفق
                            today = datetime.now().strftime('%Y-%m-%d')
                            if daily_count_to_use > 0:
                                is_vip = await self.user_db.is_vip(user_id)
                                if is_vip:
                                    DEFAULT_DAILY_COUNT = 50
                                    DEFAULT_DAILY_SIZE_SPOTIFY = 20 * 1024 * 1024 * 1024
                                else:
                                    from config import DEFAULT_DAILY_COUNT_SPOTIFY, DAILY_SIZE_LIMIT
                                    DEFAULT_DAILY_COUNT = DEFAULT_DAILY_COUNT_SPOTIFY
                                    DEFAULT_DAILY_SIZE_SPOTIFY = DAILY_SIZE_LIMIT
                                await self.user_db.update_limits(user_id, today, daily_count_to_use, 0, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE_SPOTIFY)
                            if bonus_count_to_use > 0:
                                await self.user_db.consume_bonus(user_id, bonus_count_to_use, 0)
                        except Exception as e:
                            await event.reply(f"Download failed: {e}")
                            logger.error(f"Download failed: {e}")
            except Exception as e:
                logger.error(f"Error in Spotify download: {e}")
                await event.reply("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            finally:
                self.download_status[f'{user_id}_spotify']["downloading"] = False

        
        # Only keep album/single track selection for non-playlist
        @self.client.on(events.CallbackQuery(pattern=re.compile(r"track_\d+")))
        async def on_track_selection(event):
            user_id = event.sender_id
            data = event.data.decode("utf-8")
            track_index = int(data.split("_")[1])
            current_time = datetime.now()
            if user_id in self.last_request_time_callback:
                time_diff = current_time - self.last_request_time_callback[user_id]
                remaining_time = timedelta(seconds=30) - time_diff
                if remaining_time > timedelta(seconds=0):
                    remaining_seconds = int(remaining_time.total_seconds())
                    await event.answer(f"Please wait {remaining_seconds} seconds before sending another request.", alert=True)
                    return
            self.last_request_time_callback[user_id] = current_time
            if f'{user_id}_spotify' not in self.download_status or 'tracks' not in self.download_status[f'{user_id}_spotify']:
                await event.answer("Invalid selection.", alert=True)
                return
            track_list = self.download_status[f'{user_id}_spotify']['tracks']
            selected_track = track_list[track_index]
            await event.answer("در حال دانلود...", alert=False)
            status_message = await event.respond(f"Selected track: {selected_track}...\nدر حال دانلود...")
            with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                output_path = os.path.join(tmp_dir, f"{selected_track}.mp3")
                try:
                    await asyncio.get_event_loop().run_in_executor(None, self.download_from_youtube, selected_track, output_path)
                    if not os.path.exists(output_path):
                        await status_message.edit("دانلود با خطا مواجه شد، فایل پیدا نشد.")
                        return
                    await self.client.send_file(event.chat_id, output_path, caption="Downloaded by🚀 @media_dlrobot")
                    logger.info(f"Sent {selected_track} to the user.")
                    await status_message.edit(f"✅ {selected_track} ارسال شد!")
                except Exception as e:
                    await status_message.edit(f"Download failed: {e}")
                    logger.error(f"Download failed: {e}")

        @self.client.on(events.NewMessage(pattern=None))
        async def handle_custom_range_message(event):
            user_id = event.sender_id
            user = self.download_status.get(f'{user_id}_spotify')
            if not user or not user.get('playlist_mode') or not user.get('awaiting_custom_range'):
                return
            info_type = user.get('info_type', 'playlist')
            info = user.get(f'{info_type}_info')
            text = event.raw_text.strip()
            start, end = self.parse_range(text, info['track_count'])
            if start is None:
                await event.reply("❌ فرمت بازه صحیح نیست. لطفاً به صورت <b>شروع-پایان</b> (مثلاً 5-10) ارسال کنید.", parse_mode="html")
                return
            selected = info['tracks'][start-1:end]
            self.download_status[f'{user_id}_spotify']['playlist_selected_tracks'] = selected
            self.download_status[f'{user_id}_spotify']['awaiting_custom_range'] = False
            await event.reply(f"{len(selected)} ترک انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=self.get_quality_buttons())

        @self.client.on(events.CallbackQuery(pattern=b"sp_"))
        async def playlist_callback_handler(event):
            user_id = event.sender_id
            data = event.data.decode("utf-8")
            user = self.download_status.get(f'{user_id}_spotify')
            if not user or not user.get('playlist_mode'):
                await event.respond("❌ این درخواست دیگر معتبر نیست یا منقضی شده است. لطفاً دوباره تلاش کنید.")
                return
            info_type = user.get('info_type', 'playlist')
            info = user.get(f'{info_type}_info')
            tracks = info['tracks']
            try:
                if data == "sp_playlist_main_menu":
                    try:
                        await event.edit("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", buttons=self.get_playlist_menu_buttons())
                    except Exception:
                        await event.respond("لطفاً یکی از گزینه‌های زیر را انتخاب کنید:", buttons=self.get_playlist_menu_buttons())
                    return
                if data == "sp_playlist_cancel":
                    self.download_status[f'{user_id}_spotify'].pop('playlist_mode', None)
                    try:
                        await event.edit("فرآیند انتخاب لغو شد.")
                    except Exception:
                        await event.respond("فرآیند انتخاب لغو شد.")
                    return
                if data == "sp_playlist_download_all":
                    self.download_status[f'{user_id}_spotify']['playlist_selected_tracks'] = tracks
                    try:
                        await event.edit("همه ترک‌ها انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=self.get_quality_buttons())
                    except Exception:
                        await event.respond("همه ترک‌ها انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=self.get_quality_buttons())
                    return
                elif data == "sp_playlist_select_tracks":
                    page = 0
                    self.download_status[f'{user_id}_spotify']['playlist_select_page'] = page
                    selected = self.download_status[f'{user_id}_spotify'].get('playlist_selected_tracks', [])
                    buttons = self.get_track_select_buttons(tracks, selected, page)
                    try:
                        await event.edit("از لیست زیر ترک‌های مورد نظر را انتخاب کنید:", buttons=buttons)
                    except Exception:
                        await event.respond("از لیست زیر ترک‌های مورد نظر را انتخاب کنید:", buttons=buttons)
                    return
                elif data.startswith("sp_playlist_select_page_"):
                    page = int(data.replace("sp_playlist_select_page_", ""))
                    self.download_status[f'{user_id}_spotify']['playlist_select_page'] = page
                    selected = self.download_status[f'{user_id}_spotify'].get('playlist_selected_tracks', [])
                    buttons = self.get_track_select_buttons(tracks, selected, page)
                    try:
                        await event.edit("از لیست زیر ترک‌های مورد نظر را انتخاب کنید:", buttons=buttons)
                    except Exception:
                        await event.respond("از لیست زیر ترک‌های مورد نظر را انتخاب کنید:", buttons=buttons)
                    return
                elif data.startswith("sp_playlist_pick_"):
                    track_id = data.replace("sp_playlist_pick_", "")
                    selected = self.download_status[f'{user_id}_spotify'].get('playlist_selected_tracks', [])
                    if any(t['id'] == track_id for t in selected):
                        selected = [t for t in selected if t['id'] != track_id]
                    else:
                        track = next((t for t in tracks if t['id'] == track_id), None)
                        if track:
                            selected.append(track)
                    self.download_status[f'{user_id}_spotify']['playlist_selected_tracks'] = selected
                    page = self.download_status[f'{user_id}_spotify'].get('playlist_select_page', 0)
                    buttons = self.get_track_select_buttons(tracks, selected, page)
                    try:
                        await event.edit("از لیست زیر ترک‌های مورد نظر را انتخاب کنید:", buttons=buttons)
                    except Exception:
                        await event.respond("از لیست زیر ترک‌های مورد نظر را انتخاب کنید:", buttons=buttons)
                    return
                elif data == "sp_playlist_finish_selection":
                    selected = self.download_status[f'{user_id}_spotify'].get('playlist_selected_tracks', [])
                    if not selected:
                        await event.answer("حداقل یک ترک انتخاب کنید!", alert=True)
                        return
                    try:
                        await event.edit(f"{len(selected)} ترک انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=self.get_quality_buttons())
                    except Exception:
                        await event.respond(f"{len(selected)} ترک انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=self.get_quality_buttons())
                    return
                elif data == "sp_playlist_custom_range":
                    range_buttons = self.get_range_buttons(len(tracks))
                    try:
                        await event.edit("یک بازه از ترک‌ها را انتخاب کنید یا بازه دلخواه وارد نمایید:", buttons=range_buttons)
                    except Exception:
                        await event.respond("یک بازه از ترک‌ها را انتخاب کنید یا بازه دلخواه وارد نمایید:", buttons=range_buttons)
                    return
                elif data.startswith("sp_playlist_range_"):
                    parts = data.split("_")
                    start = int(parts[2])
                    end = int(parts[3])
                    selected = tracks[start:end]
                    self.download_status[f'{user_id}_spotify']['playlist_selected_tracks'] = selected
                    try:
                        await event.edit(f"{len(selected)} ترک انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=self.get_quality_buttons())
                    except Exception:
                        await event.respond(f"{len(selected)} ترک انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=self.get_quality_buttons())
                    return
                elif data == "sp_playlist_ask_custom_range":
                    self.download_status[f'{user_id}_spotify']['awaiting_custom_range'] = True
                    try:
                        await event.edit("لطفاً بازه دلخواه را به صورت <b>شروع-پایان</b> (مثلاً 5-10) ارسال کنید:", buttons=None, parse_mode="html")
                    except Exception:
                        await event.respond("لطفاً بازه دلخواه را به صورت <b>شروع-پایان</b> (مثلاً 5-10) ارسال کنید:", buttons=None, parse_mode="html")
                    return
                # --- در دانلود گروهی (playlist_callback_handler، کیفیت انتخاب شد) ---
                elif data.startswith("sp_playlist_quality_"):
                    quality = int(data.replace("sp_playlist_quality_", ""))
                    selected = self.download_status[f'{user_id}_spotify'].get('playlist_selected_tracks', [])
                    track_count = len(selected)
                    try:
                        await event.edit(f"دانلود {track_count} ترک با کیفیت {quality}kbps آغاز شد...\nلطفاً منتظر بمانید.")
                    except Exception:
                        await event.respond(f"دانلود {track_count} ترک با کیفیت {quality}kbps آغاز شد...\nلطفاً منتظر بمانید.")
                    for idx, track in enumerate(selected):
                        # چک محدودیت برای هر ترک
                        ok, limits, daily_count_to_use, bonus_count_to_use = await self.check_and_update_limit(user_id, 1)
                        if not ok:
                            is_vip = limits.get('is_vip') == 1 and limits.get('vip_expiry')
                            if is_vip:
                                plan = 'ویژه (VIP)'
                                max_count = 50
                            else:
                                plan = 'عادی'
                                from config import DEFAULT_DAILY_COUNT_SPOTIFY
                                max_count = DEFAULT_DAILY_COUNT_SPOTIFY
                            await event.respond(f"🚫 شما به محدودیت روزانه {max_count} دانلود ({plan}) رسیده‌اید. ادامه دانلود متوقف شد.")
                            break
                        try:
                            await event.respond(f"⬇️ دانلود {idx+1}/{track_count}: {track['title']} ({quality}kbps)")
                            with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                                output_path = os.path.join(tmp_dir, f"{track['title']}.mp3")
                                await asyncio.get_event_loop().run_in_executor(None, self.download_from_youtube, track['query'], output_path)
                                if not os.path.exists(output_path):
                                    await event.respond(f"دانلود با خطا مواجه شد، فایل پیدا نشد: {track['title']}")
                                    continue
                                await self.client.send_file(event.chat_id, output_path, caption="Downloaded by🚀 @media_dlrobot")
                            # افزایش شمارنده بعد از ارسال موفق هر ترک
                            today = datetime.now().strftime('%Y-%m-%d')
                            if daily_count_to_use > 0:
                                is_vip = await self.user_db.is_vip(user_id)
                                if is_vip:
                                    DEFAULT_DAILY_COUNT = 50
                                    DEFAULT_DAILY_SIZE_SPOTIFY = 20 * 1024 * 1024 * 1024
                                else:
                                    from config import DEFAULT_DAILY_COUNT_SPOTIFY, DAILY_SIZE_LIMIT
                                    DEFAULT_DAILY_COUNT = DEFAULT_DAILY_COUNT_SPOTIFY
                                    DEFAULT_DAILY_SIZE_SPOTIFY = DAILY_SIZE_LIMIT
                                await self.user_db.update_limits(user_id, today, daily_count_to_use, 0, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE_SPOTIFY)
                            if bonus_count_to_use > 0:
                                await self.user_db.consume_bonus(user_id, bonus_count_to_use, 0)
                        except Exception as e:
                            await event.respond(f"❌ خطا در دانلود {track['title']}: {e}")
                        if idx < track_count - 1:
                            await event.respond("تا دانلود بعدی 30 ثانیه صبر کنید.")
                            await asyncio.sleep(30)
                    await event.respond(f"✅ دانلود گروهی تمام شد!")
                    return
            except Exception as e:
                try:
                    await event.respond("خطا در پردازش درخواست: " + str(e))
                except:
                    pass 