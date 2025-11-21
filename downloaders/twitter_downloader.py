import os
import re
import uuid
import random
import logging
import asyncio
import requests
import subprocess
import tempfile
from typing import Optional, Pattern, Dict, List
from yt_dlp import YoutubeDL
from telethon import events, Button
from config import PROXY, CHANNELS, COOKIE_DIR_TWITTER, ADMIN_BOT_TOKEN

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

class TwitterDownloader():
    def __init__(self, client):
        self.client = client
        self.url_pattern = self.get_url_pattern()
        self.channels = CHANNELS
        self.proxy = PROXY
        self.ADMIN_BOT_TOKEN = ADMIN_BOT_TOKEN
        self.COOKIE_DIR = COOKIE_DIR_TWITTER or "cookies_twitter/"
        self.download_status: Dict[int, int] = {}
        self.user_data: Dict[int, dict] = {}
        self.executor = None  # For async thread pool
        self.last_request_time: Dict[int, float] = {}
        self.cookie_dir = self.COOKIE_DIR
        self.cookie_index = 0  # For round-robin cookie selection

    def get_url_pattern(self) -> Pattern:
        return re.compile(r"https?://(www\.)?(twitter|x)\.com/.+/status/\d+")

    def get_next_cookie_file(self) -> Optional[str]:
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

    async def send_channel_links(self, event):
        channels = [
            {"name": channel.lstrip("@"), "url": f"https://t.me/{channel.lstrip('@')}"} for channel in self.channels
        ]
        buttons = [[Button.url(channel["name"], channel["url"])] for channel in channels]
        await event.reply(
            "برای استفاده از ربات در چنل های زیر عضو شوید و لینک ویدیو را دوباره ارسال کنید.",
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

    def get_available_formats(self, url: str):
        cookie_file = self.get_next_cookie_file()
        ydl_opts = {
            'listformats': True,
            'cookiefile': cookie_file,
        }
        if self.proxy:
            ydl_opts['proxy'] = self.proxy
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=False)
            formats = info_dict.get('formats', [])
            video_formats = []
            audio_formats = []
            for f in formats:
                if f.get('vcodec') != 'none':
                    video_formats.append(f)
                elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                    audio_formats.append(f)
            best_audio = max((f for f in audio_formats if f.get('abr') is not None), key=lambda x: x.get('abr', 0), default=None)
            return best_audio, video_formats, audio_formats

    def download_format(self, url, format_id, output_path):
        unique_id = uuid.uuid4().hex
        cookie_file = self.get_next_cookie_file()
        ydl_opts = {
            'format': format_id,
            'outtmpl': f'{output_path}/{unique_id}.%(ext)s',
            'cookiefile': cookie_file,
        }
        if self.proxy:
            ydl_opts['proxy'] = self.proxy
        with YoutubeDL(ydl_opts) as ydl:
            info_dict = ydl.extract_info(url, download=True)
            file_path = f"{output_path}/{unique_id}.{info_dict['ext']}"
            return file_path, info_dict

    def merge_video_audio(self, video_path, audio_path, output_path):
        output_file = f"{output_path}/merged_{uuid.uuid4().hex}.mp4"
        cmd = [
            'ffmpeg', '-y', '-i', video_path, '-i', audio_path,
            '-c:v', 'copy', '-c:a', 'aac', '-strict', 'experimental', output_file
        ]
        subprocess.run(cmd, capture_output=True)
        return output_file

    def sanitize_filename(self, filename: str) -> str:
        return re.sub(r'[\\/:*?"<>|]', '_', filename)

    async def handle_video(self, event, twitter_url):
        user_id = event.sender_id
        if self.download_status.get(user_id, 0) == 1:
            await event.reply("در حال دانلود... لطفا صبر کنید تا دانلود قبلی کامل شود.")
            return
        try:
            self.download_status[user_id] = 1
            search_message = await event.reply("در حال جستجو... لطفا صبر کنید.")
            loop = asyncio.get_event_loop()
            best_audio, video_formats, audio_formats = await loop.run_in_executor(
                self.executor, self.get_available_formats, twitter_url
            )
            if not video_formats:
                await event.reply("هیچ نتیجه ای یافت نشد.")
                await search_message.delete()
                return
            buttons = []
            for f in video_formats:
                quality = f.get('format_note') or f.get('resolution') or (f.get('height') and f"{f['height']}p") or f.get('format_id')
                ext = f.get('ext', 'unknown')
                size = f.get('filesize')
                size_str = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown Size"
                buttons.append([
                    Button.inline(
                        f"🎥 {quality} ({ext}, {size_str})",
                        f"format_{f['format_id']}"
                    )
                ])
            for af in audio_formats:
                quality = af.get('format_note') or af.get('abr') or af.get('format_id')
                ext = af.get('ext', 'unknown')
                size = af.get('filesize')
                size_str = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown Size"
                abr = af.get('abr')
                label = f"🎵 Audio {abr}kbps ({ext}, {size_str})" if abr else f"🎵 Audio ({ext}, {size_str})"
                buttons.append([
                    Button.inline(
                        label,
                        f"audio_{af['format_id']}"
                    )
                ])
            if buttons:
                self.user_data[user_id] = {
                    'twitter_url': twitter_url,
                    'best_audio': best_audio['format_id'] if best_audio else None,
                    'video_formats': video_formats,
                    'audio_formats': audio_formats
                }
                await event.reply("نوع و کیفیت فایل را انتخاب کنید:", buttons=buttons)
                await search_message.delete()
            else:
                await event.reply("No formats available.")
                await search_message.delete()
        except Exception as e:
            logger.error(f"Error in handle_video: {e}")
            # Send simple error to user
            await event.reply("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            # Send full error to admin
            try:
                await self.client.send_message(5019214713, f"[TwitterDownloader Error]\nUser: {user_id}\nURL: {twitter_url}\nError: {str(e)}")
            except Exception as admin_err:
                logger.error(f"Failed to send error to admin: {admin_err}")
        finally:
            self.download_status[user_id] = 0

    def convert_to_mp3_format(self, input_file, output_file):
        try:
            cmd = ['ffmpeg', '-i', input_file, '-q:a', '0', '-map', 'a', output_file]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                raise Exception(f"FFmpeg failed: {result.stderr}")
        except Exception as e:
            logger.error(f"Error converting to MP3: {e}")
            raise

    async def send_selected_format(self, event, twitter_url, format_id, is_audio, user):
        user_id = event.sender_id
        with LoggingTempDirectory(dir="./downloads") as tmp_dir:
            download_path = tmp_dir
            loop = asyncio.get_event_loop()
            try:
                if is_audio:
                    audio_file, audio_info = await loop.run_in_executor(
                        self.executor, self.download_format, twitter_url, format_id, download_path
                    )
                    audio_title = self.sanitize_filename(audio_info.get('title', 'No Title'))
                    mp3_file = os.path.join(download_path, f"{audio_title}.mp3")
                    await loop.run_in_executor(None, self.convert_to_mp3_format, audio_file, mp3_file)
                    await self.client.send_file(event.chat_id, mp3_file, caption=f"{audio_title}\n\nDownloaded by🚀 @media_dlrobot")
                else:
                    video_file, video_info = await loop.run_in_executor(
                        self.executor, self.download_format, twitter_url, format_id, download_path
                    )
                    if video_info.get('acodec') == 'none' and user:
                        audio_formats = user.get('audio_formats', [])
                        best_audio = None
                        if audio_formats:
                            best_audio = max((f for f in audio_formats if f.get('abr') is not None), key=lambda x: x.get('abr', 0), default=None)
                        if best_audio:
                            audio_file, _ = await loop.run_in_executor(
                                self.executor, self.download_format, twitter_url, best_audio['format_id'], download_path
                            )
                            merged_file = await loop.run_in_executor(
                                self.executor, self.merge_video_audio, video_file, audio_file, download_path
                            )
                            await self.client.send_file(event.chat_id, merged_file, caption=f"Downloaded by🚀 @media_dlrobot")
                            return
                    video_title = self.sanitize_filename(video_info.get('title', 'No Title'))
                    await self.client.send_file(event.chat_id, video_file, caption=f"{video_title}\n\nDownloaded by🚀 @media_dlrobot")
            except Exception as e:
                logger.error(f"Error in send_selected_format: {e}")
                await event.reply(f"❌ {str(e)}")

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=self.url_pattern))
        async def handle_twitter(event):
            user_id = event.sender_id
            url = event.text.strip()
            import time
            current_time = time.time()
            # Throttle requests to 30 seconds
            if user_id in self.last_request_time:
                time_diff = current_time - self.last_request_time[user_id]
                remaining_time = 30 - time_diff
                if remaining_time > 0:
                    await event.reply(f"Please wait {int(remaining_time)} seconds before sending another request.")
                    return
            self.last_request_time[user_id] = current_time
            # Check channel membership
            if self.channels and not self.join_checker(user_id):
                await self.send_channel_links(event)
                return
            # Validate Twitter link
            if not re.match(self.url_pattern, url):
                await event.reply("یک لینک معتبر ارسال کنید.")
                return
            if not self.executor:
                from concurrent.futures import ThreadPoolExecutor
                self.executor = ThreadPoolExecutor(max_workers=5)
            await self.handle_video(event, url)

        @self.client.on(events.CallbackQuery())
        async def format_callback_handler(event):
            try:
                user_id = event.sender_id
                data = event.data.decode("utf-8")
                user = self.user_data.get(user_id)
                if not user:
                    return
                twitter_url = user['twitter_url']
                if self.download_status.get(user_id, 0) == 1:
                    await event.answer("Download in progress. Please wait.", alert=True)
                    return
                downloading_message = None
                try:
                    try:
                        downloading_message = await event.edit("در حال دانلود... لطفا صبر کنید.", buttons=None)
                    except Exception as e:
                        logger.warning(f"Could not edit message: {e}. Sending new message instead.")
                        downloading_message = await event.reply("در حال دانلود... لطفا صبر کنید.")
                    self.download_status[user_id] = 1
                    if data.startswith("audio_"):
                        format_id = data.replace("audio_", "")
                        await self.send_selected_format(event, twitter_url, format_id, is_audio=True, user=user)
                    elif data.startswith("format_"):
                        format_id = data.replace("format_", "")
                        await self.send_selected_format(event, twitter_url, format_id, is_audio=False, user=user)
                    if downloading_message:
                        try:
                            await downloading_message.delete()
                        except Exception as e:
                            logger.warning(f"Could not delete downloading message: {e}")
                except Exception as e:
                    logger.error(f"Error in download process: {e}")
                    await event.reply(str(e))
                finally:
                    self.download_status[user_id] = 0
            except Exception as e:
                logger.error(f"Error in callback handler: {e}")
                try:
                    await event.answer("خطایی رخ داد. لطفاً دوباره تلاش کنید.", alert=True)
                except:
                    pass

    def download_media(self, url: str) -> str:
        raise NotImplementedError("Use handle_video and send_selected_format for TwitterDownloader.") 