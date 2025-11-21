import os
import uuid
import asyncio
import yt_dlp
import logging
import re
import random
from typing import Pattern, Dict, List, Tuple, Optional, Set
from telethon import TelegramClient, events, Button
from telethon.errors import FloodWaitError, ChatWriteForbiddenError, MediaEmptyError
import subprocess
import requests
from config import PROXY, CHANNELS, COOKIE_DIR, CACHE_ENABLED, CACHE_TYPE, CACHE_DB_FILE, SAVE_CHANNEL_NAME
from config import ADMIN_BOT_TOKEN
from config import DAILY_COUNT_LIMIT, DAILY_SIZE_LIMIT,COOKIE_DIR
import aiosqlite
from yt_dlp.utils import DownloadError, ExtractorError
import tempfile
from collections import defaultdict
from FastTelethon import upload_file
from telethon.tl import types
from telethon.tl.types import DocumentAttributeFilename, DocumentAttributeVideo, DocumentAttributeAudio
from PIL import Image
from downloaders.user_db import UserDB
from config import AD_MESSAGE_TEXTS, AD_BUTTON_IDS
import datetime
import config

YTDL_TIMEOUT = 120  # ثانیه
QUALITY_SELECT_TIMEOUT = 30  # ثانیه


ad_index = 0  # Global/static index for round-robin ad selection

logger = logging.getLogger(__name__)

# کدک صوتی پیش‌فرض برای هر فرمت ویدیو
DEFAULT_AUDIO_CODECS_FOR_EXT = {
    'mp4': 'aac',
    'mkv': 'aac',
    'webm': 'opus',
    'mov': 'aac',
    'flv': 'mp3',
}

# کدک‌های صوتی سازگار با هر فرمت کانتینر
COMPATIBLE_AUDIO_CODECS = {
    'mp4': ['aac', 'mp3', 'ac3', 'vorbis'],
    'mkv': ['aac', 'mp3', 'ac3', 'vorbis', 'opus'],
    'webm': ['opus', 'vorbis'],
    'mov': ['aac', 'mp3', 'ac3'],
    'flv': ['mp3', 'aac'],
}

def get_audio_codec(file_path):
    """با ffprobe کدک صوتی فایل رو بگیر"""
    import subprocess
    cmd = [
        'ffprobe', '-v', 'error',
        '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        file_path
    ]
    try:
        output = subprocess.check_output(cmd).decode().strip()
        return output
    except subprocess.CalledProcessError:
        return None

def find_compatible_audio(video_format, audio_formats):
    mux_compatibility = {
        'mp4':     ['aac', 'mp4a.40.2', 'mp3', 'ac3'],
        'webm':    ['opus', 'vorbis'],
        'mkv':     ['aac', 'mp3', 'opus', 'vorbis', 'ac3'],
        'mov':     ['aac', 'mp4a.40.2'],
        'flv':     ['mp3', 'aac'],
        '3gp':     ['aac', 'mp4a.40.2'],
    }
    video_ext = video_format.get('ext', '').lower()
    # مرحله 1: پیدا کردن صداهایی که هم پسوند و هم acodec مناسب دارند
    for af in audio_formats:
        acodec = af.get('acodec', '').lower()
        ext = af.get('ext', '').lower()
        if ext == video_ext and acodec in mux_compatibility.get(video_ext, []):
            return af
    # مرحله 2: پیدا کردن صدایی که فقط acodec مناسب دارد
    for af in audio_formats:
        acodec = af.get('acodec', '').lower()
        if acodec in mux_compatibility.get(video_ext, []):
            return af
    # مرحله 3: پیدا کردن صدایی که ext مشابه دارد ولی کدک نه لزوماً
    for af in audio_formats:
        ext = af.get('ext', '').lower()
        if ext == video_ext:
            return af
    # مرحله 4: اگر هیچ‌کدام پیدا نشد، fallback به بهترین کیفیت (بالاترین bitrate)
    return max(audio_formats, key=lambda x: x.get('abr', 0), default=None)

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

class YouTubeDownloader:
    def __init__(self, client: TelegramClient, youtube_selfbot_downloader=None):
        try:
            logger.info("Initializing YouTubeDownloader...")
            self.client = client
            self.url_pattern = self.get_url_pattern()
            self.download_status: Dict[int, int] = {}
            self.user_data: Dict[int, dict] = {}
            self.last_request_time: Dict[int, float] = {}
            self.DOWNLOAD_SIZE_LIMIT = 1 * 1024 * 1024 * 1024  # 1 GB in bytes
            self.MP3_SIZE_LIMIT = 14 * 1024 * 1024  # 14 MB in bytes
            self.proxy = PROXY
            self.channels = CHANNELS
            self.cookie_dir = COOKIE_DIR
            logger.info(f"PROXY: {self.proxy}")
            logger.info(f"CHANNELS: {self.channels}")
            logger.info(f"COOKIE_DIR: {self.cookie_dir}")
            self.cookie_index = 0  # For round-robin cookie selection
            self.user_requests = {}  # key: user_id, value: url
            self.awaiting_subtitle_urls: Set[int] = set()
            self.TOP_LANGUAGES = [
                'en', 'zh', 'hi', 'es', 'fr', 'ar', 'bn', 'ru', 'pt', 'de', 'fa'
            ]
            self.LANGUAGE_FLAGS = {
                'en': '\U0001F1EC\U0001F1E7',  # English
                'zh': '\U0001F1E8\U0001F1F3',  # Chinese
                'hi': '\U0001F1EE\U0001F1F3',  # Hindi
                'es': '\U0001F1EA\U0001F1F8',  # Spanish
                'fr': '\U0001F1EB\U0001F1F7',  # French
                'ar': '\U0001F1F8\U0001F1E6',  # Arabic
                'bn': '\U0001F1E7\U0001F1E9',  # Bengali
                'ru': '\U0001F1F7\U0001F1FA',  # Russian
                'pt': '\U0001F1F5\U0001F1F9',  # Portuguese
                'de': '\U0001F1E9\U0001F1EA',  # German
                'fa': '\U0001F1EE\U0001F1F7',  # Farsi
            }
            
            # Quality categories
            self.QUALITY_CATEGORIES = {
                'low': (0, 300),
                'medium': (301, 700),
                'high': (701, 1080),
                'ultra': (1081, float('inf'))
            }
            
            # Audio quality categories (in kbps)
            self.AUDIO_QUALITY_CATEGORIES = {
                'low': (0, 96),
                'medium': (97, 192),
                'high': (193, 320),
                'ultra': (321, float('inf'))
            }
            
            # Quality timeout tasks
            self.quality_timeout_tasks = {}
            
            
            # Cache DB setup
            self.cache_enabled = CACHE_ENABLED
            self.cache_type = CACHE_TYPE
            self.cache_db_file = CACHE_DB_FILE
            self.save_channel = int(SAVE_CHANNEL_NAME)
            self.cache_conn = None
            # Restore config attributes
            self.MESSAGES_PER_SECOND = 30  # Telegram's limit
            self.BATCH_SIZE = 30  # Number of messages to send in each batch
            self.BATCH_DELAY = 1  # Delay between batches in seconds
            self.TELEGRAM_LIMIT = 2 * 1024 * 1024 * 1024  # 2GB
            self.DAILY_COUNT_LIMIT = DAILY_COUNT_LIMIT
            self.DAILY_SIZE_LIMIT = DAILY_SIZE_LIMIT
            # user_db remains as before
            self.user_db = UserDB()
            self.youtube_selfbot_downloader = youtube_selfbot_downloader
            logger.info("YouTubeDownloader initialized successfully.")
        except Exception as e:
            logger.error(f"Error initializing YouTubeDownloader: {e}")
            raise

    async def ainit(self):
        # Only create tables if needed, do not keep persistent connection
        if self.cache_enabled and self.cache_type == 'database':
            async with aiosqlite.connect(self.cache_db_file, timeout=30) as db:
                await db.execute("PRAGMA journal_mode=WAL;")
                async with db.cursor() as cursor:
                    await cursor.execute('''
                        CREATE TABLE IF NOT EXISTS video_cache (
                            video_id TEXT,
                            quality INTEGER,
                            quality_category TEXT,
                            file_id TEXT,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (video_id, quality)
                        )
                    ''')
                    await cursor.execute('''
                        CREATE TABLE IF NOT EXISTS audio_cache (
                            video_id TEXT,
                            audio_quality INTEGER,
                            audio_quality_category TEXT,
                            file_id TEXT,
                            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                            PRIMARY KEY (video_id, audio_quality)
                        )
                    ''')
                    await db.commit()
        self.save_channel = await self.client.get_entity(self.save_channel)

    def get_url_pattern(self) -> Pattern:
        try:
            return re.compile(r"(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)
        except Exception as e:
            logger.error(f"Error creating URL pattern: {e}")
            raise

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

    async def join_checker(self, user_id: int) -> List[str]:
        import aiohttp
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            logger.warning(f'Invalid user_id for join check (not convertible to int): {user_id}')
            return [channel for channel in self.channels]
        if user_id is None or not isinstance(user_id, int) or user_id <= 0:
            logger.warning(f'Invalid user_id for join check: {user_id}')
            return [channel for channel in self.channels]
        not_joined_channels = []
        async with aiohttp.ClientSession() as session:
            for channel in self.channels:
                try:
                    logger.info(f"Checking membership for user_id={user_id} in channel={channel}")
                    channel_username = channel.lstrip('@')
                    url = f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/getChatMember?chat_id=@{channel_username}&user_id={user_id}"
                    async with session.get(url, timeout=10, ssl=True) as response:
                        if response.status == 200:
                            data = await response.json()
                            status = data['result']['status']
                            if status not in ["member", "administrator", "creator"]:
                                not_joined_channels.append(channel)
                        else:
                            response_text = await response.text()
                            not_joined_channels.append(channel)
                            logger.warning(f"Failed to check membership for channel {channel}: {response.status} - {response_text}")
                            continue
                except aiohttp.ClientSSLError as e:
                    logger.error(f"SSL Error checking channel membership: {e}")
                    not_joined_channels.append(channel)
                    continue
                except aiohttp.ClientError as e:
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
            if not not_joined_channels:  # If no channels need joining
                return True  # Allow the user to proceed
                
            buttons = [[Button.url(channel[1:], f"https://t.me/{channel[1:]}")] for channel in not_joined_channels]
            buttons.append([Button.inline("عضو شدم✅", b"check_membership")])
            await event.respond("برای استفاده از ربات لطفا در چنل های زیر عضو شوید:", buttons=buttons)
            return False  # Don't allow the user to proceed
        except Exception as e:
            logger.error(f"Error sending channel links: {e}")
            try:
                await event.respond("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            except:
                pass
            return True  # Allow the user to proceed in case of error

    def get_available_formats(self, url: str):
        try:
            cookie_file = self.get_next_cookie_file()
            ydl_opts = {
                'listformats': True,
                'cookiefile': cookie_file,
                'noplaylist': True,
               
            }
            if self.proxy:
                ydl_opts['proxy'] = self.proxy
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info_dict = ydl.extract_info(url, download=False)
                    formats = info_dict.get('formats', [])
                    duration = info_dict.get('duration')
                    if duration is not None:
                        for f in formats:
                            f['duration'] = duration
                    video_formats = []
                    audio_formats = []
                    for f in formats:
                        # Accept all video formats, ignore filesize limits
                        if f.get('vcodec') != 'none':
                            video_formats.append(f)
                        elif f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                            audio_formats.append(f)
                    best_audio = max((f for f in audio_formats if f.get('abr') is not None), key=lambda x: x.get('abr', 0), default=None)
                    return best_audio, video_formats, audio_formats
                except DownloadError as e:
                    logger.error(f"Download error getting formats: {e}")
                    raise
                except ExtractorError as e:
                    logger.error(f"Extractor error getting formats: {e}")
                    raise
        except Exception as e:
            logger.error(f"Error getting available formats: {e}")
            raise

    def get_playlist_info(self, url):
        """
        اطلاعات کلی پلی‌لیست و لیست ویدیوها را با yt-dlp استخراج می‌کند.
        خروجی: dict شامل title, playlist_url, video_count, videos (لیست dict)
        """
        try:
            cookie_file = self.get_next_cookie_file()
            ydl_opts = {
                'extract_flat': True,
                'cookiefile': cookie_file,
                'quiet': True,
                'noplaylist': False,
               
            }
            if self.proxy:
                ydl_opts['proxy'] = self.proxy
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=False)
                # اگر پلی‌لیست نبود، None برگردان
                if not info_dict.get('_type') == 'playlist':
                    return None
                playlist_title = info_dict.get('title', 'بدون عنوان')
                playlist_url = info_dict.get('webpage_url', url)
                entries = info_dict.get('entries', [])
                videos = []
                for entry in entries:
                    videos.append({
                        'id': entry.get('id'),
                        'title': entry.get('title', 'بدون عنوان'),
                        'url': f"https://www.youtube.com/watch?v={entry.get('id')}"
                    })
                return {
                    'title': playlist_title,
                    'playlist_url': playlist_url,
                    'video_count': len(videos),
                    'videos': videos
                }
        except Exception as e:
            logger.error(f"Error extracting playlist info: {e}")
            return None

    @staticmethod
    def sanitize_filename(filename: str) -> str:
        try:
            return re.sub(r'[\\/:*?"<>|]', '_', filename)
        except Exception as e:
            logger.error(f"Error sanitizing filename: {e}")
            return "sanitized_filename"

    def download_thumbnail(self, url, output_path):
        try:
            unique_id = uuid.uuid4().hex
            cookie_file = self.get_next_cookie_file()
            ydl_opts = {
                'quiet': True,
                'extract_flat': True,
                'cookiefile': cookie_file,
                'noplaylist': True,
               
            }
            if self.proxy:
                ydl_opts['proxy'] = self.proxy
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info_dict = ydl.extract_info(url, download=False)
                    thumbnail_url = info_dict.get('thumbnail', None)
                    if thumbnail_url:
                        thumbnail_path = os.path.join(output_path, f'{unique_id}_thumbnail.jpg')
                        response = requests.get(thumbnail_url)
                        if response.status_code == 200:
                            with open(thumbnail_path, 'wb') as f:
                                f.write(response.content)
                            return thumbnail_path
                except DownloadError as e:
                    logger.error(f"Download error getting thumbnail: {e}")
                except ExtractorError as e:
                    logger.error(f"Extractor error getting thumbnail: {e}")
        except Exception as e:
            logger.error(f"Error downloading thumbnail: {e}")
        return None

    def download_media(self, url, format_id, output_path):
        try:
            unique_id = uuid.uuid4().hex
            cookie_file = self.get_next_cookie_file()
            ydl_opts = {
                'format': format_id,
                'outtmpl': f'{output_path}/{unique_id}.%(ext)s',
                'cookiefile': cookie_file,
                'noplaylist': True,
              
            }
            if self.proxy:
                ydl_opts['proxy'] = self.proxy
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info_dict = ydl.extract_info(url, download=True)
                    file_ext = info_dict['ext']
                    downloaded_file_path = os.path.join(output_path, f"{unique_id}.{file_ext}")
                    return downloaded_file_path, info_dict
                except DownloadError as e:
                    logger.error(f"Download error: {e}")
                    raise
                except ExtractorError as e:
                    logger.error(f"Extractor error: {e}")
                    raise
        except Exception as e:
            logger.error(f"Error downloading media: {e}")
            raise

    def merge_audio_video(self, video_file, audio_file, output_file):
        import os
        import subprocess
        import shutil
        video_ext = video_file.split('.')[-1].lower()
        target_acodec = DEFAULT_AUDIO_CODECS_FOR_EXT.get(video_ext, 'aac')
        compatible_codecs = COMPATIBLE_AUDIO_CODECS.get(video_ext, [])
        current_acodec = get_audio_codec(audio_file)
        logger.info(f"Current audio codec: {current_acodec}")
        is_compatible = current_acodec in compatible_codecs
        temp_audio_file = None
        try:
            if not is_compatible:
                logger.info(f"Audio codec {current_acodec} is not compatible with container {video_ext}. Encoding audio to {target_acodec}...")
                with tempfile.NamedTemporaryFile(delete=False, suffix=f'.{video_ext}') as tempf:
                    temp_audio_file = tempf.name
                cmd_encode_audio = [
                    'ffmpeg', '-y',
                    '-i', audio_file,
                    '-vn'
                ]
                if target_acodec == 'opus':
                    cmd_encode_audio += ['-c:a', 'libopus']
                else:
                    cmd_encode_audio += ['-c:a', target_acodec]
                cmd_encode_audio.append(temp_audio_file)
                result_encode = subprocess.run(cmd_encode_audio, capture_output=True, text=True)
                if result_encode.returncode != 0:
                    logger.error(f"Audio encoding failed: {result_encode.stderr}")
                    raise Exception(f"Audio encoding failed: {result_encode.stderr}")
                audio_file_to_use = temp_audio_file
            else:
                audio_file_to_use = audio_file
            cmd_merge = [
                'ffmpeg', '-y',
                '-i', video_file,
                '-i', audio_file_to_use,
                '-c:v', 'copy',
                '-c:a', 'copy',
                output_file
            ]
            result_merge = subprocess.run(cmd_merge, capture_output=True, text=True)
            if result_merge.returncode != 0:
                logger.error(f"FFmpeg merge failed: {result_merge.stderr}")
                raise Exception(f"FFmpeg merge failed: {result_merge.stderr}")
        finally:
            if temp_audio_file and os.path.exists(temp_audio_file):
                try:
                    os.remove(temp_audio_file)
                except Exception as e:
                    logger.warning(f"Could not remove temp audio file: {e}")
        return output_file

    @staticmethod
    def convert_to_mp3_format(input_file, output_file):
        try:
            cmd = ['ffmpeg', '-i', input_file, '-q:a', '0', '-map', 'a', output_file]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                logger.error(f"FFmpeg error: {result.stderr}")
                raise Exception(f"FFmpeg failed: {result.stderr}")
        except Exception as e:
            logger.error(f"Error converting to MP3: {e}")
            raise

    def get_video_id(self, url):
        try:
            patterns = [
                # الگوهای قبلی (watch, embed, shorts, v, ...)
                r"(?:https?:\/\/)?(?:www\.)?(?:youtube\.com\/(?:[^\/\n\s]+\/\S+\/|(?:v|embed|shorts|watch)\/?.*|.*[?&]v=)|youtu\.be\/)([a-zA-Z0-9_-]{11})",
                # الگوی جدید برای /live/VIDEO_ID
                r"youtube\.com\/live\/([a-zA-Z0-9_-]{11})",
                # الگوی مستقیم فقط آیدی
                r"[?&]v=([a-zA-Z0-9_-]{11})",
                # الگوی کوتاه youtu.be
                r"youtu\.be\/([a-zA-Z0-9_-]{11})"
            ]
            for pattern in patterns:
                match = re.search(pattern, url)
                if match:
                    return match.group(1)
            return None
        except Exception as e:
            logger.error(f"Error extracting video_id: {e}")
            return None

    def get_quality_category(self, height: int) -> str:
        """Determine the quality category based on video height."""
        for category, (min_height, max_height) in self.QUALITY_CATEGORIES.items():
            if min_height <= height <= max_height:
                return category
        return 'unknown'

    def get_audio_quality_category(self, bitrate: int) -> str:
        """Determine the audio quality category based on bitrate in kbps."""
        for category, (min_bitrate, max_bitrate) in self.AUDIO_QUALITY_CATEGORIES.items():
            if min_bitrate <= bitrate <= max_bitrate:
                return category
        return 'unknown'

    async def get_cached_video(self, video_id: str, requested_quality: int) -> Optional[Tuple[str, int]]:
        """Get cached video, trying to find the best available quality."""
        try:
            if not (self.cache_enabled and self.cache_type == 'database'):
                return None
            async with aiosqlite.connect(self.cache_db_file, timeout=30) as db:
                await db.execute("PRAGMA journal_mode=WAL;")
                async with db.cursor() as cursor:
                    await cursor.execute(
                        "SELECT file_id, quality FROM video_cache WHERE video_id = ? AND quality = ?",
                        (video_id, requested_quality)
                    )
                    result = await cursor.fetchone()
                    if result:
                        return result
                    await cursor.execute(
                        "SELECT file_id, quality FROM video_cache WHERE video_id = ? AND quality > ? ORDER BY quality ASC LIMIT 1",
                        (video_id, requested_quality)
                    )
                    result = await cursor.fetchone()
                    if result:
                        return result
                    await cursor.execute(
                        "SELECT file_id, quality FROM video_cache WHERE video_id = ? AND quality < ? ORDER BY quality DESC LIMIT 1",
                        (video_id, requested_quality)
                    )
                    return await cursor.fetchone()
        except Exception as e:
            logger.error(f"Database error getting cached video: {e}")
            return None

    async def get_cached_audio(self, video_id: str, requested_bitrate: int) -> Optional[Tuple[str, int]]:
        """Get cached audio, trying to find the best available quality."""
        try:
            if not (self.cache_enabled and self.cache_type == 'database'):
                return None
            async with aiosqlite.connect(self.cache_db_file, timeout=30) as db:
                await db.execute("PRAGMA journal_mode=WAL;")
                async with db.cursor() as cursor:
                    await cursor.execute(
                        "SELECT file_id, audio_quality FROM audio_cache WHERE video_id = ? AND audio_quality = ?",
                        (video_id, requested_bitrate)
                    )
                    result = await cursor.fetchone()
                    if result:
                        return result
                    await cursor.execute(
                        "SELECT file_id, audio_quality FROM audio_cache WHERE video_id = ? AND audio_quality > ? ORDER BY audio_quality ASC LIMIT 1",
                        (video_id, requested_bitrate)
                    )
                    result = await cursor.fetchone()
                    if result:
                        return result
                    await cursor.execute(
                        "SELECT file_id, audio_quality FROM audio_cache WHERE video_id = ? AND audio_quality < ? ORDER BY audio_quality DESC LIMIT 1",
                        (video_id, requested_bitrate)
                    )
                    return await cursor.fetchone()
        except Exception as e:
            logger.error(f"Database error getting cached audio: {e}")
            return None

    async def save_video_to_cache(self, video_id: str, quality: int, file_id: str):
        try:
            if not (self.cache_enabled and self.cache_type == 'database'):
                return
            quality_category = self.get_quality_category(quality)
            async with aiosqlite.connect(self.cache_db_file, timeout=30) as db:
                await db.execute("PRAGMA journal_mode=WAL;")
                async with db.cursor() as cursor:
                    await cursor.execute(
                        "INSERT OR REPLACE INTO video_cache (video_id, quality, quality_category, file_id) VALUES (?, ?, ?, ?)",
                        (video_id, quality, quality_category, file_id)
                    )
                    await db.commit()
        except Exception as e:
            logger.error(f"Database error saving video to cache: {e}")

    async def save_audio_to_cache(self, video_id: str, audio_bitrate: int, file_id: str):
        try:
            if not (self.cache_enabled and self.cache_type == 'database'):
                return
            audio_quality_category = self.get_audio_quality_category(audio_bitrate) if audio_bitrate else 'unknown'
            async with aiosqlite.connect(self.cache_db_file, timeout=30) as db:
                await db.execute("PRAGMA journal_mode=WAL;")
                async with db.cursor() as cursor:
                    await cursor.execute(
                        "INSERT OR REPLACE INTO audio_cache (video_id, audio_quality, audio_quality_category, file_id) VALUES (?, ?, ?, ?)",
                        (video_id, audio_bitrate, audio_quality_category, file_id)
                    )
                    await db.commit()
        except Exception as e:
            logger.error(f"Database error saving audio to cache: {e}")

    def get_format_size(self, format_info):
        size = format_info.get('filesize') or format_info.get('filesize_approx')
        if size:
            return size
        tbr = format_info.get('tbr')
        duration = format_info.get('duration')
        if tbr and duration:
            return int(duration * tbr * 1000 / 8)
        return None

    async def send_from_cache_or_download(self, event, url, format_id, quality, is_audio=False, user=None, audio_bitrate=None):
        try:
            logger.info("Starting send_from_cache_or_download...")
            video_id = self.get_video_id(url)
            if not video_id:
                logger.warning("Invalid YouTube link provided.")
                await event.reply("لینک معتبر یوتیوب وارد کنید!")
                return

            user_id = event.sender_id
            today = datetime.date.today().strftime('%Y-%m-%d')
            # --- VIP logic ---
            is_vip = await self.user_db.is_vip(user_id)
            if is_vip:
                DEFAULT_DAILY_COUNT = 50
                DEFAULT_DAILY_SIZE = 20 * 1024 * 1024 * 1024
                
                
            else:
                from config import DAILY_COUNT_LIMIT, DAILY_SIZE_LIMIT
                DEFAULT_DAILY_COUNT = DAILY_COUNT_LIMIT
                DEFAULT_DAILY_SIZE = DAILY_SIZE_LIMIT
            limits = await self.user_db.get_limits(user_id, today, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)
            logger.debug(f"User limits fetched: {limits}")

            remaining_bonus_count = limits['bonus_count']
            remaining_bonus_size = limits['bonus_size']
            remaining_daily_count = DEFAULT_DAILY_COUNT - limits['daily_count']
            remaining_daily_size = DEFAULT_DAILY_SIZE - limits['daily_size']

            file_size = None
            format_info = None
            if is_audio and user:
                for f in user.get('audio_formats', []):
                    if f['format_id'] == format_id:
                        format_info = f
                        break
            elif not is_audio and user:
                for f in user.get('video_formats', []):
                    if f['format_id'] == format_id:
                        format_info = f
                        break
            if format_info:
                file_size = self.get_format_size(format_info)
            if file_size is None:
                file_size = 0
            logger.debug(f"Calculated file size: {file_size} bytes.")

            can_download = False
            use_bonus = False
            # Adjust logic to allow flexible use of bonus and daily limits
            if remaining_bonus_count > 0:
                if remaining_bonus_size >= file_size or remaining_daily_size >= file_size:
                    can_download = True
                    use_bonus = True
            elif remaining_daily_count > 0:
                if remaining_daily_size >= file_size or remaining_bonus_size >= file_size:
                    can_download = True
                    use_bonus = False

            if not can_download:
                logger.warning("User has reached download or size limits.")
                total_count_left = remaining_bonus_count + remaining_daily_count
                total_size_left = remaining_bonus_size + remaining_daily_size
                await event.reply(
                    f"🚫 شما به سقف مجاز دانلود یا حجم رسیده‌اید.\n"
                    f"تعداد دانلود باقی‌مانده: {total_count_left}\n"
                    f"حجم باقی‌مانده: {(total_size_left/(1024*1024)):.2f} MB\n"
                    f"حجم فایل درخواستی: {(file_size/(1024*1024)):.2f} MB\n"
                    f"لطفاً فردا دوباره تلاش کنید یا با دعوت دوستان جایزه بگیرید یا حساب خود را با خرید اشتراک ارتقا دهید."
                )
                # پیشنهاد رفرال و VIP
                from telethon import Button
                
                suggest_buttons = [
                [Button.text('ℹ️ اطلاعات حساب')],
                [Button.text('🎁 دریافت لینک رفرال و جایزه')],
                [Button.text('👥 نمایش زیرمجموعه‌ها')],
                [Button.text('💳 خرید اشتراک')],
                [Button.text('📊 مقایسه پلن‌ها')],
                [Button.text('❓ راهنما')]
            ]

                await event.respond(
                    "برای دور زدن این محدودیت دانلود، یکی از راه حل های گفته شده رو امتحان کنید:",
                    buttons=suggest_buttons
                )
                return

            if use_bonus:
                logger.info(f"Consuming bonus for user_id={user_id}, count=1, size={file_size}.")
                await self.user_db.consume_bonus(user_id, 1, file_size)
            # Update limits in the database based on flexible usage
            if use_bonus:
                if remaining_bonus_size < file_size:
                    # Bonus count is used, but daily size is consumed
                    logger.info(f"Updating daily size limits for user_id={user_id}.")
                    await self.user_db.update_limits(user_id, today, 0, file_size - remaining_bonus_size, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)
            else:
                if remaining_daily_size < file_size:
                    # Daily count is used, but bonus size is consumed
                    logger.info(f"Updating bonus size limits for user_id={user_id}.")
                    await self.user_db.consume_bonus(user_id, 0, file_size - remaining_daily_size)

            cached = await (self.get_cached_audio(video_id, audio_bitrate) if is_audio else self.get_cached_video(video_id, quality))
            if cached:
                try:
                    file_id, cached_quality = cached
                    logger.info(f"Cache hit for video_id={video_id}, file_id={file_id}, quality={cached_quality}.")
                    quality_message = ""
                    if is_audio:
                        if cached_quality != audio_bitrate:
                            quality_message = f"\nNote: Sending {cached_quality}kbps instead of requested {audio_bitrate}kbps quality."
                        await self.client.send_file(
                            event.chat_id,
                            file=file_id,
                            caption=f"Downloaded by🚀 @media_dlrobot{quality_message}"
                        )
                        await send_advertisement_message(self.client, event)
                    else:
                        if cached_quality != quality:
                            quality_message = f"\nNote: Sending {cached_quality}p instead of requested {quality}p quality."
                        await self.client.send_file(
                            event.chat_id,
                            file=file_id,
                            caption=f"Downloaded by🚀 @media_dlrobot{quality_message}",
                            supports_streaming=True
                        )
                        await send_advertisement_message(self.client, event)
                    return True
                except (FloodWaitError, ChatWriteForbiddenError, MediaEmptyError) as e:
                    logger.error(f"Error sending cached file: {e}")
                    return False

            try:
                with LoggingTempDirectory(dir="./downloads") as tmp_dir:
                    logger.info(f"Temporary directory created: {tmp_dir}")
                    download_path = tmp_dir
                    if is_audio:
                        logger.info(f"Downloading audio for video_id={video_id}, format_id={format_id}.")
                        media_file, audio_info = await asyncio.get_event_loop().run_in_executor(
                            None, self.download_media, url, format_id, download_path
                        )
                        logger.info(f"Audio downloaded: {media_file}")
                        if not use_bonus:
                            logger.info(f"Updating daily limits for user_id={user_id}.")
                            await self.user_db.update_limits(user_id, today, 1, file_size, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)

                        video_title = self.sanitize_filename(audio_info.get('title', 'No Title'))
                        thumbnail_path = await asyncio.get_event_loop().run_in_executor(
                            None, self.download_thumbnail, url, download_path)
                        logger.debug(f"Thumbnail downloaded: {thumbnail_path}")
                        file_size = os.path.getsize(media_file)
                        if file_size <= self.MP3_SIZE_LIMIT:
                            output_file = os.path.join(download_path, f"{video_title}.mp3")
                            await asyncio.get_event_loop().run_in_executor(None, self.convert_to_mp3_format, media_file, output_file)
                            send_file_path = output_file
                            file_ext = 'mp3'
                        else:
                            send_file_path = media_file
                            file_ext = os.path.splitext(media_file)[1][1:] or 'mp3'

                        duration = int(audio_info.get('duration', 0))
                        performer = audio_info.get('artist') or audio_info.get('uploader') or ""
                        title = audio_info.get('title', video_title)

                        with open(send_file_path, "rb") as file:
                            tg_file = await upload_file(self.client, file, progress_callback=None)
                        logger.info(f"File uploaded to Telegram: {tg_file}")
                        attributes = [
                            DocumentAttributeAudio(duration=duration, title=title, performer=performer, voice=False),
                            DocumentAttributeFilename(f"{video_title}.{file_ext}")
                        ]
                        mime_type = "audio/mpeg"
                        media = types.InputMediaUploadedDocument(
                            file=tg_file,
                            mime_type=mime_type,
                            attributes=attributes,
                            force_file=False
                        )
                        sent_message = await self.client.send_file(
                            self.save_channel,
                            file=media,
                            caption=f"{video_title}\n\nDownloaded by🚀 @media_dlrobot",
                            thumb=thumbnail_path
                        )
                        file_id = sent_message.file.id
                        logger.info(f"File sent to save channel: {file_id}")
                        await self.save_audio_to_cache(video_id, audio_bitrate, file_id)
                        await self.client.send_file(
                            event.chat_id,
                            file=file_id,
                            caption=f"{video_title}\n\nDownloaded by🚀 @media_dlrobot",
                            thumb=thumbnail_path
                        )
                        await send_advertisement_message(self.client, event)
                        return True
                    else:
                        logger.info(f"Downloading video for video_id={video_id}, format_id={format_id}.")
                        media_file, video_info = await asyncio.get_event_loop().run_in_executor(
                            None, self.download_media, url, format_id, download_path
                        )
                        logger.info(f"Video downloaded: {media_file}")
                        if not use_bonus:
                            logger.info(f"Updating daily limits for user_id={user_id}.")
                            await self.user_db.update_limits(user_id, today, 1, file_size, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)

                        video_title = self.sanitize_filename(video_info.get('title', 'No Title'))
                        thumbnail_path = await asyncio.get_event_loop().run_in_executor(
                            None, self.download_thumbnail, url, download_path)
                        logger.debug(f"Thumbnail downloaded: {thumbnail_path}")
                        if video_info.get('acodec') == 'none' and user:
                            audio_formats = user.get('audio_formats', [])
                            compatible_audio = None
                            if audio_formats:
                                compatible_audio = find_compatible_audio(video_info, audio_formats)
                            if compatible_audio:
                                audio_file, _ = await asyncio.get_event_loop().run_in_executor(
                                    None, self.download_media, url, compatible_audio['format_id'], download_path
                                )
                                final_file_path = os.path.join(download_path, f"{video_title}.mp4")
                                await asyncio.get_event_loop().run_in_executor(None, self.merge_audio_video, media_file, audio_file, final_file_path)
                                send_file_path = final_file_path
                                file_ext = 'mp4'
                            else:
                                send_file_path = media_file
                                file_ext = os.path.splitext(media_file)[1][1:] or 'mp4'
                        else:
                            send_file_path = media_file
                            file_ext = os.path.splitext(media_file)[1][1:] or 'mp4'

                        duration = int(video_info.get('duration', 0))
                        w = int(video_info.get('width', 0) or 0)
                        h = int(video_info.get('height', 0) or 0)
                        attributes = [
                            DocumentAttributeVideo(duration=duration, w=w, h=h, supports_streaming=True),
                            DocumentAttributeFilename(f"{video_title}.{file_ext}")
                        ]
                        mime_type = f"video/{file_ext}" if file_ext in ["mp4", "mkv", "webm"] else "video/mp4"

                        with open(send_file_path, "rb") as file:
                            tg_file = await upload_file(self.client, file, progress_callback=None)
                        logger.info(f"File uploaded to Telegram: {tg_file}")
                        media = types.InputMediaUploadedDocument(
                            file=tg_file,
                            mime_type=mime_type,
                            attributes=attributes,
                            force_file=False
                        )
                        sent_message = await self.client.send_file(
                            self.save_channel,
                            file=media,
                            caption=f"{video_title} (video)",
                            thumb=thumbnail_path,
                            supports_streaming=True
                        )
                        file_id = sent_message.file.id
                        logger.info(f"File sent to save channel: {file_id}")
                        await self.save_video_to_cache(video_id, quality, file_id)
                        await self.client.send_file(
                            event.chat_id,
                            file=file_id,
                            caption=f"{video_title}\n\nDownloaded by🚀 @media_dlrobot",
                            supports_streaming=True,
                            thumb=thumbnail_path
                        )
                        await send_advertisement_message(self.client, event)
                        return True
            except Exception as e:
                logger.error(f"Error in send_from_cache_or_download (upload): {e}")
                try:
                    await event.reply("متأسفانه خطایی در آپلود رخ داد. لطفاً دوباره تلاش کنید.")
                except:
                    pass
                return False
        except Exception as e:
            logger.error(f"Error in send_from_cache_or_download: {e}")
            try:
                await event.reply("متأسفانه خطایی در دانلود رخ داد. لطفاً دوباره تلاش کنید.")
            except:
                pass
            return False
        finally:
            if config.active_youtube_downloads > 0:
                config.active_youtube_downloads -= 1
            print(f"active download : {config.active_youtube_downloads}")

    def list_available_subtitle_languages_yt_dlp(self, url):
        ydl_opts = {
            'skip_download': True,
            'writesubtitles': True,
            'outtmpl': '%(id)s.%(ext)s',
           
        }
        cookie_file = self.get_next_cookie_file()
        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                languages = {}
                subs = info.get('subtitles') or {}
                for code, tracks in subs.items():
                    if code in self.TOP_LANGUAGES:
                        languages[code] = f"{self.LANGUAGE_FLAGS.get(code, '')} {code.upper()} (manual)"
                auto_subs = info.get('automatic_captions') or {}
                for code, tracks in auto_subs.items():
                    if code in self.TOP_LANGUAGES and code not in languages:
                        languages[code] = f"{self.LANGUAGE_FLAGS.get(code, '')} {code.upper()} (auto)"
                return languages if languages else None
        except Exception as e:
            logger.error(f"yt_dlp language listing failed: {e}")
            return None

    def fetch_and_save_subtitles_yt_dlp(self, url, target_lang, file_format='srt', output_file=None, tempdir=None):
        cookies_path = self.get_next_cookie_file()
        outtmpl = os.path.join(tempdir if tempdir else '.', '%(title)s.%(ext)s')
        ydl_opts = {
            'skip_download': True,
            'writesubtitles': False,
            'writeautomaticsub': True,
            'subtitleslangs': [target_lang],
            'subtitlesformat': 'srt',
            'outtmpl': outtmpl,
            'cookiefile': cookies_path,
           
        }
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                title = info.get('title')
                if not title:
                    return None
                srt_filename = os.path.join(tempdir if tempdir else '.', f"{title}.srt")
                if not os.path.exists(srt_filename):
                    for f in os.listdir(tempdir if tempdir else '.'):
                        if f.endswith('.srt'):
                            srt_filename = os.path.join(tempdir if tempdir else '.', f)
                            break
                    else:
                        return None
                if file_format == 'srt':
                    return srt_filename
                elif file_format == 'txt':
                    with open(srt_filename, 'r', encoding='utf-8') as f:
                        lines = f.readlines()
                    texts = []
                    last_text = None
                    i = 0
                    while i < len(lines):
                        if lines[i].strip().isdigit():
                            i += 1
                            if i < len(lines) and '-->' in lines[i]:
                                i += 1
                                text_lines = []
                                while i < len(lines) and lines[i].strip() and '-->' not in lines[i]:
                                    text_lines.append(lines[i].strip())
                                    i += 1
                                text = ' '.join(text_lines)
                                if text and text != last_text:
                                    texts.append(text)
                                    last_text = text
                            else:
                                i += 1
                        else:
                            i += 1
                    txt_filename = os.path.join(tempdir if tempdir else '.', f"{title}.txt")
                    with open(txt_filename, 'w', encoding='utf-8') as txt_file:
                        for text in texts:
                            txt_file.write(text + '\n')
                    return txt_filename
                else:
                    return None
        except Exception as e:
            logger.error(f"yt_dlp subtitle extraction failed: {e}")
            return None

    def request_subtitle_only(self, user_id: int):
        try:
            self.awaiting_subtitle_urls.add(user_id)
            self.user_requests.pop(user_id, None)
            if user_id in self.user_data:
                self.user_data.pop(user_id, None)
            task = self.quality_timeout_tasks.pop(user_id, None)
            if task and not task.done():
                task.cancel()
            self.download_status[user_id] = 0
        except Exception as e:
            logger.error(f"Error enabling subtitle-only mode for user {user_id}: {e}")

    async def present_subtitle_language_menu(self, event, user_id: int, youtube_url: str, subtitle_only: bool = False):
        try:
            self.user_requests[user_id] = youtube_url
            user_state = self.user_data.get(user_id, {})
            user_state['youtube_url'] = youtube_url
            if subtitle_only:
                user_state['subtitle_only'] = True
            else:
                user_state.pop('subtitle_only', None)
            self.user_data[user_id] = user_state
            await event.reply("در حال بررسی زبان‌های زیرنویس...")
            available_languages = self.list_available_subtitle_languages_yt_dlp(youtube_url)
            if not available_languages:
                await event.reply("هیچ زیرنویسی برای این ویدیو موجود نیست.")
                return
            lang_buttons = [
                [Button.inline(lang, f"yt_sub_translate|||{user_id}|||{code}".encode())]
                for code, lang in available_languages.items()
            ]
            await event.reply("زبان زیرنویس را انتخاب کنید:", buttons=lang_buttons)
        except Exception as e:
            logger.error(f"Error preparing subtitle language menu for user {user_id}: {e}")
            try:
                await event.reply("متأسفانه خطایی در بررسی زیرنویس رخ داد. لطفاً دوباره تلاش کنید.")
            except Exception:
                pass

    async def handle_subtitle_only_request(self, event, youtube_url: str):
        user_id = event.sender_id
        self.awaiting_subtitle_urls.discard(user_id)
        try:
            if "list=" in youtube_url.lower():
                await event.reply("لطفاً لینک یک ویدیوی تکی یوتیوب را ارسال کنید. دریافت زیرنویس برای پلی‌لیست پشتیبانی نمی‌شود.")
                return
            await self.present_subtitle_language_menu(event, user_id, youtube_url, subtitle_only=True)
        except Exception as e:
            logger.error(f"Error handling subtitle-only request for user {user_id}: {e}")
            try:
                await event.reply("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            except Exception:
                pass

    def register_handlers(self):
        @self.client.on(events.NewMessage(pattern=self.url_pattern))
        async def handle_message(event):
            try:
                user_id = event.sender_id
                message_text = event.raw_text.strip()
                import time
                current_time = time.time()
                # Throttle requests to 30 seconds
                if user_id in self.last_request_time:
                    time_diff = current_time - self.last_request_time[user_id]
                    remaining_time = 40 - time_diff
                    if remaining_time > 0:
                        await event.reply(f"Please wait {int(remaining_time)} seconds before sending another request.")
                        return
                self.last_request_time[user_id] = current_time
                # --- Channel URL filter ---
                import re
                def is_youtube_channel_url(url: str) -> bool:
                    channel_patterns = [
                        r"(https?://)?(www\.)?youtube\.com/channel/[\w\-]+/?(\?.*)?$",
                        r"(https?://)?(www\.)?youtube\.com/user/[\w\-]+/?(\?.*)?$",
                        r"(https?://)?(www\.)?youtube\.com/c/[\w\-]+/?(\?.*)?$",
                        r"(https?://)?(www\.)?youtube\.com/@[\w\-]+/?(\?.*)?$",
                        r"(https?://)?youtube\.com/@[\w\-]+/?(\?.*)?$",
                    ]
                    for pat in channel_patterns:
                        if re.fullmatch(pat, url.strip()):
                            return True
                    return False
                if is_youtube_channel_url(message_text):
                    await event.reply("❌ لینک اسم چنل یوتیوب رو نفرست. فقط لینک ویدیو یا پلی‌لیست مجاز است.")
                    return
                # Channel membership check
                if self.channels:
                    not_joined_channels = await self.join_checker(user_id)
                    if not_joined_channels:
                        await self.send_channel_links(event, not_joined_channels)
                        return
                if user_id in self.awaiting_subtitle_urls:
                    await self.handle_subtitle_only_request(event, message_text)
                    return
                # --- Playlist validation logic ---
                def is_youtube_playlist(url):
                    # فقط لینک‌های معتبر پلی‌لیست یوتیوب را قبول کن
                    import re
                    # فقط اگر مسیر /playlist باشد یا دامنه playlist باشد
                    playlist_patterns = [
                        r"youtube\.com/playlist[?]list=",  # https://www.youtube.com/playlist?list=...
                        r"youtu\.be/playlist[?]list=",      # https://youtu.be/playlist?list=...
                    ]
                    for pat in playlist_patterns:
                        if re.search(pat, url):
                            return True
                    return False
                if is_youtube_playlist(message_text):
                    playlist_info = self.get_playlist_info(message_text)
                    if playlist_info:
                        # ذخیره اطلاعات پلی‌لیست برای user
                        self.user_data[user_id] = {
                            'playlist_info': playlist_info,
                            'playlist_url': playlist_info['playlist_url'],
                            'youtube_url': message_text,
                            'playlist_selected_videos': [],
                            'playlist_mode': True
                        }
                        # نمایش اطلاعات کلی پلی‌لیست
                        info_msg = f"📃 <b>اطلاعات پلی‌لیست:</b>\n"
                        info_msg += f"<b>عنوان:</b> {playlist_info['title']}\n"
                        info_msg += f"<b>تعداد ویدیو:</b> {playlist_info['video_count']}\n"
                        info_msg += f"<b>لینک:</b> <a href=\"{playlist_info['playlist_url']}\">مشاهده در یوتیوب</a>"
                        # منوی انتخاب نوع دانلود
                        buttons = [
                            [Button.inline("🎬 دانلود همه ویدیوهای پلی‌لیست", b"playlist_download_all")],
                            [Button.inline("📥 دانلود انتخابی ویدیو از لیست", b"playlist_select_videos")],
                            [Button.inline("📚 دانلود دسته‌ای سفارشی", b"playlist_custom_range")],
                        ]
                        buttons.append([Button.inline("❌ لغو و بستن منو", b"playlist_cancel")])
                        await event.reply(info_msg, buttons=buttons, parse_mode="html")
                        return
                    else:
                        await event.reply("❌ لینک ارسال‌شده یک پلی‌لیست معتبر یوتیوب نیست یا قابل استخراج نیست.")
                        return
                # --- End playlist validation logic ---
                await self.handle_video(event, message_text)
            except Exception as e:
                logger.error(f"Error in message handler: {e}")
                try:
                    await event.reply("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
                except:
                    pass

        @self.client.on(events.NewMessage(pattern=None))
        async def handle_custom_range_message(event):
            user_id = event.sender_id
            user = self.user_data.get(user_id)
            if not user or not user.get('playlist_mode') or not user.get('awaiting_custom_range'):
                return  # پیام مربوط به بازه دلخواه نیست
            text = event.raw_text.strip()
            # اعتبارسنجی بازه
            import re
            match = re.match(r'^(\d+)[\s\-_,]+(\d+)$', text)
            if not match:
                await event.reply("❌ فرمت بازه صحیح نیست. لطفاً به صورت <b>شروع-پایان</b> (مثلاً 5-10) ارسال کنید.", parse_mode="html")
                return
            start, end = int(match.group(1)), int(match.group(2))
            playlist_info = user['playlist_info']
            videos = playlist_info['videos']
            if start < 1 or end > len(videos) or start >= end:
                await event.reply(f"❌ بازه باید بین 1 تا {len(videos)} و شروع کمتر از پایان باشد.")
                return
            selected = videos[start-1:end]
            self.user_data[user_id]['playlist_selected_videos'] = selected
            self.user_data[user_id]['awaiting_custom_range'] = False
            await event.reply(f"{len(selected)} ویدیو انتخاب شد.\nلطفاً فرمت و کیفیت مورد نظر را انتخاب کنید:", buttons=[
                [Button.inline("MP4 (ویدیو)", b"playlist_format_mp4")],
                [Button.inline("MP3 (صوتی)", b"playlist_format_mp3")],
                [Button.inline("❌ لغو و بستن منو", b"playlist_cancel")],
            ])

        @self.client.on(events.CallbackQuery())
        async def format_callback_handler(event):
            try:
                user_id = event.sender_id
                data = event.data.decode("utf-8")
                user = self.user_data.get(user_id)
                # --- لغو تایم‌اوت انتخاب کیفیت در صورت انتخاب ---
                task = self.quality_timeout_tasks.pop(user_id, None)
                if task and not task.done():
                    task.cancel()
                if (data.startswith("youtube_format_") or data.startswith("youtube_audio_")) and not user:
                    await event.answer("اطلاعات کاربری یافت نشد. لطفاً مجدداً تلاش کنید.", alert=True)
                    return
                if data.startswith("youtube_format_") or data.startswith("youtube_audio_"):
                    format_id = data.replace("youtube_format_", "").replace("youtube_audio_", "")
                    # Look up merged_size from user_data
                    merged_size = None
                    user_limit = user.get('user_limit') if user else None
                    if data.startswith("youtube_format_"):
                        for f in user.get('video_formats', []):
                            if f.get('format_id') == format_id:
                                merged_size = self.get_format_size(f)
                                # اگر ویدیو بدون صدا بود، حجم صوت را هم اضافه کن
                                if f.get('acodec') == 'none' and user.get('audio_formats'):
                                    compatible_audio = find_compatible_audio(f, user.get('audio_formats'))
                                    if compatible_audio:
                                        audio_size = self.get_format_size(compatible_audio)
                                        if audio_size is not None:
                                            merged_size = (merged_size or 0) + audio_size
                                break
                    else:
                        for af in user.get('audio_formats', []):
                            if af.get('format_id') == format_id:
                                merged_size = self.get_format_size(af)
                                break
                    if merged_size is not None and user_limit is not None and merged_size > user_limit:
                        if user and user.get('is_vip'):
                            await event.answer("🚫 سقف مجاز دانلود برای حساب ویژه 1.5 گیگابایت است. لطفاً کیفیت پایین‌تر انتخاب کنید.", alert=True)
                        else:
                            await event.answer("🚫 سقف مجاز دانلود برای حساب عادی 500 مگابایت است. لطفاً کیفیت پایین‌تر انتخاب کنید.", alert=True)
                        return
                    # ادامه منطق قبلی دانلود ...
                if data == "check_membership":
                    if self.channels and user_id is not None:
                        not_joined_channels = await self.join_checker(user_id)
                        if not_joined_channels:
                            await event.answer("برای استفاده از ربات در تمام چنل ها عضو شوید.", alert=True)
                            return
                if not user:
                    return
                # --- Playlist menu logic ---
                if user.get('playlist_mode'):
                    playlist_info = user['playlist_info']
                    videos = playlist_info['videos']
                    if data == "playlist_download_all":
                        # همه ویدیوها انتخاب شود و مرحله انتخاب فرمت/کیفیت شروع شود
                        self.user_data[user_id]['playlist_selected_videos'] = videos
                        await event.edit("همه ویدیوهای پلی‌لیست انتخاب شد.\nلطفاً فرمت و کیفیت مورد نظر را انتخاب کنید:", buttons=[
                            [Button.inline("MP4 (ویدیو)", b"playlist_format_mp4")],
                            [Button.inline("MP3 (صوتی)", b"playlist_format_mp3")],
                            [Button.inline("❌ لغو و بستن منو", b"playlist_cancel")],
                        ])
                        return
                    elif data == "playlist_select_videos":
                        # نمایش 10 ویدیوی اول با دکمه انتخابی و دکمه صفحه بعد
                        page = 0
                        self.user_data[user_id]['playlist_select_page'] = page
                        buttons = []
                        for idx, vid in enumerate(videos[page*10:(page+1)*10]):
                            checked = "✅ " if any(v['id'] == vid['id'] for v in self.user_data[user_id].get('playlist_selected_videos', [])) else ""
                            buttons.append([Button.inline(f"{checked}{page*10+idx+1}. {vid['title'][:40]}", f"playlist_pick_{vid['id']}".encode())])
                        if (page+1)*10 < len(videos):
                            buttons.append([Button.inline("صفحه بعد ⏭️", f"playlist_select_page_{page+1}".encode())])
                        buttons.append([Button.inline("اتمام انتخاب و ادامه ⏭️", b"playlist_finish_selection")])
                        buttons.append([Button.inline("❌ لغو و بستن منو", b"playlist_cancel")])
                        self.user_data[user_id]['playlist_selected_videos'] = self.user_data[user_id].get('playlist_selected_videos', [])
                        await event.edit("از لیست زیر ویدیوهای مورد نظر را انتخاب کنید:", buttons=buttons)
                        return
                    elif data.startswith("playlist_select_page_"):
                        # نمایش صفحه بعدی انتخاب ویدیوها
                        page = int(data.replace("playlist_select_page_", ""))
                        self.user_data[user_id]['playlist_select_page'] = page
                        selected = self.user_data[user_id].get('playlist_selected_videos', [])
                        buttons = []
                        for idx, vid in enumerate(videos[page*10:(page+1)*10]):
                            checked = "✅ " if any(v['id'] == vid['id'] for v in selected) else ""
                            buttons.append([Button.inline(f"{checked}{page*10+idx+1}. {vid['title'][:40]}", f"playlist_pick_{vid['id']}".encode())])
                        if (page+1)*10 < len(videos):
                            buttons.append([Button.inline("صفحه بعد ⏭️", f"playlist_select_page_{page+1}".encode())])
                        if page > 0:
                            buttons.append([Button.inline("⏮️ صفحه قبل", f"playlist_select_page_{page-1}".encode())])
                        buttons.append([Button.inline("اتمام انتخاب و ادامه ⏭️", b"playlist_finish_selection")])
                        buttons.append([Button.inline("❌ لغو و بستن منو", b"playlist_cancel")])
                        await event.edit("از لیست زیر ویدیوهای مورد نظر را انتخاب کنید:", buttons=buttons)
                        return
                    elif data.startswith("playlist_pick_"):
                        # اضافه یا حذف ویدیو به/از لیست انتخابی با کلیک مجدد
                        vid_id = data.replace("playlist_pick_", "")
                        selected = self.user_data[user_id].get('playlist_selected_videos', [])
                        if any(v['id'] == vid_id for v in selected):
                            # اگر قبلاً انتخاب شده بود، حذف کن
                            selected = [v for v in selected if v['id'] != vid_id]
                        else:
                            # اگر نبود، اضافه کن
                            video = next((v for v in videos if v['id'] == vid_id), None)
                            if video:
                                selected.append(video)
                        self.user_data[user_id]['playlist_selected_videos'] = selected
                        # نمایش مجدد لیست با علامت انتخاب شده و صفحه فعلی
                        page = self.user_data[user_id].get('playlist_select_page', 0)
                        buttons = []
                        for idx, vid in enumerate(videos[page*10:(page+1)*10]):
                            checked = "✅ " if any(v['id'] == vid['id'] for v in selected) else ""
                            buttons.append([Button.inline(f"{checked}{page*10+idx+1}. {vid['title'][:40]}", f"playlist_pick_{vid['id']}".encode())])
                        if (page+1)*10 < len(videos):
                            buttons.append([Button.inline("صفحه بعد ⏭️", f"playlist_select_page_{page+1}".encode())])
                        if page > 0:
                            buttons.append([Button.inline("⏮️ صفحه قبل", f"playlist_select_page_{page-1}".encode())])
                        buttons.append([Button.inline("اتمام انتخاب و ادامه ⏭️", b"playlist_finish_selection")])
                        buttons.append([Button.inline("❌ لغو و بستن منو", b"playlist_cancel")])
                        await event.edit("از لیست زیر ویدیوهای مورد نظر را انتخاب کنید:", buttons=buttons)
                        return
                    elif data == "playlist_finish_selection":
                        selected = self.user_data[user_id].get('playlist_selected_videos', [])
                        if not selected:
                            await event.answer("حداقل یک ویدیو انتخاب کنید!", alert=True)
                            return
                        await event.edit(f"{len(selected)} ویدیو انتخاب شد.\nلطفاً فرمت و کیفیت مورد نظر را انتخاب کنید:", buttons=[
                            [Button.inline("MP4 (ویدیو)", b"playlist_format_mp4")],
                            [Button.inline("MP3 (صوتی)", b"playlist_format_mp3")],
                            [Button.inline("❌ لغو و بستن منو", b"playlist_cancel")],
                        ])
                        return
                    elif data == "playlist_custom_range":
                        # نمایش گزینه‌های بازه آماده و بازه دلخواه
                        range_buttons = [
                            [Button.inline("۵ ویدیوی اول", b"playlist_range_0_5")],
                            [Button.inline("۱۰ ویدیوی اول", b"playlist_range_0_10")],
                            [Button.inline("۲۰ ویدیوی اول", b"playlist_range_0_20")],
                            [Button.inline("بازه دلخواه...", b"playlist_ask_custom_range")],
                        ]
                        range_buttons.append([Button.inline("❌ لغو و بستن منو", b"playlist_cancel")])
                        await event.edit("یک بازه از ویدیوهای پلی‌لیست را انتخاب کنید یا بازه دلخواه وارد نمایید:", buttons=range_buttons)
                        return
                    elif data.startswith("playlist_range_"):
                        # انتخاب بازه آماده
                        parts = data.split("_")
                        start = int(parts[2])
                        end = int(parts[3])
                        selected = videos[start:end]
                        self.user_data[user_id]['playlist_selected_videos'] = selected
                        await event.edit(f"{len(selected)} ویدیو انتخاب شد.\nلطفاً فرمت و کیفیت مورد نظر را انتخاب کنید:", buttons=[
                            [Button.inline("MP4 (ویدیو)", b"playlist_format_mp4")],
                            [Button.inline("MP3 (صوتی)", b"playlist_format_mp3")],
                            [Button.inline("❌ لغو و بستن منو", b"playlist_cancel")],
                        ])
                        return
                    elif data == "playlist_ask_custom_range":
                        await event.edit("لطفاً بازه دلخواه را به صورت <b>شروع-پایان</b> (مثلاً 5-10) ارسال کنید:", buttons=None, parse_mode="html")
                        self.user_data[user_id]['awaiting_custom_range'] = True
                        return
                    elif data.startswith("playlist_format_"):
                        # مرحله بعد: انتخاب کیفیت و شروع دانلود
                        fmt = data.replace("playlist_format_", "")
                        self.user_data[user_id]['playlist_selected_format'] = fmt
                        # نمایش دکمه‌های کیفیت متناسب با فرمت
                        if fmt == "mp4":
                            quality_buttons = [
                                [Button.inline("144p", b"playlist_quality_144")],
                                [Button.inline("360p", b"playlist_quality_360")],
                                [Button.inline("480p", b"playlist_quality_480")],
                                [Button.inline("720p", b"playlist_quality_720")],
                                [Button.inline("1080p", b"playlist_quality_1080")],
                            ]
                        else:
                            quality_buttons = [
                                [Button.inline("128kbps", b"playlist_quality_128")],
                                [Button.inline("192kbps", b"playlist_quality_192")],
                                [Button.inline("256kbps", b"playlist_quality_256")],
                                [Button.inline("320kbps", b"playlist_quality_320")],
                            ]
                        quality_buttons.append([Button.inline("❌ لغو و بستن منو", b"playlist_cancel")])
                        await event.edit(f"فرمت {fmt.upper()} انتخاب شد.\nلطفاً کیفیت مورد نظر را انتخاب کنید:", buttons=quality_buttons)
                        return
                    elif data.startswith("playlist_quality_"):
                        # شروع دانلود گروهی با انتخاب نزدیک‌ترین کیفیت موجود
                        fmt = self.user_data[user_id].get('playlist_selected_format')
                        selected = self.user_data[user_id].get('playlist_selected_videos', [])
                        quality = data.replace("playlist_quality_", "")
                        await event.edit(f"دانلود {len(selected)} ویدیو با فرمت {fmt.upper()} و کیفیت {quality} آغاز شد...\nلطفاً منتظر بمانید.")
                        # دانلود گروهی با rate limit و انتخاب نزدیک‌ترین کیفیت
                        limit_reached = False
                        for idx, video in enumerate(selected):
                            if limit_reached:
                                break
                            try:
                                video_id = self.get_video_id(video['url'])
                                # گرفتن فرمت‌های موجود برای هر ویدیو
                                best_audio, video_formats, audio_formats = await asyncio.get_event_loop().run_in_executor(
                                    None, self.get_available_formats, video['url']
                                )
                                # ذخیره فرمت‌ها در user_data برای هر ویدیو
                                if 'playlist_video_formats' not in self.user_data[user_id]:
                                    self.user_data[user_id]['playlist_video_formats'] = {}
                                if 'playlist_audio_formats' not in self.user_data[user_id]:
                                    self.user_data[user_id]['playlist_audio_formats'] = {}
                                self.user_data[user_id]['playlist_video_formats'][video_id] = video_formats
                                self.user_data[user_id]['playlist_audio_formats'][video_id] = audio_formats
                                self.user_data[user_id]['playlist_best_audio'] = self.user_data[user_id].get('playlist_best_audio', {})
                                self.user_data[user_id]['playlist_best_audio'][video_id] = best_audio
                                if fmt == "mp4":
                                    requested = int(quality)
                                    candidates = [f for f in video_formats if f.get('height')]
                                    if not candidates:
                                        await event.respond(f"❌ ویدیوی {video['title']} کیفیت مناسب ندارد.")
                                        continue
                                    closest = min(candidates, key=lambda f: abs((f.get('height') or 0) - requested))
                                    format_id = closest['format_id']
                                    actual_quality = closest.get('height')
                                    await event.respond(f"⬇️ دانلود {idx+1}/{len(selected)}: {video['title']} ({actual_quality}p)")
                                    user_info = {
                                        'video_formats': self.user_data[user_id]['playlist_video_formats'][video_id],
                                        'audio_formats': self.user_data[user_id]['playlist_audio_formats'][video_id],
                                        'best_audio': best_audio['format_id'] if best_audio else None
                                    }
                                    result = await self.send_from_cache_or_download(event, video['url'], format_id, actual_quality, is_audio=False, user=user_info)
                                else:
                                    requested = int(quality)
                                    candidates = [f for f in audio_formats if f.get('abr')]
                                    if not candidates:
                                        await event.respond(f"❌ ویدیوی {video['title']} کیفیت صوتی مناسب ندارد.")
                                        continue
                                    closest = min(candidates, key=lambda f: abs((f.get('abr') or 0) - requested))
                                    format_id = closest['format_id']
                                    actual_quality = closest.get('abr')
                                    await event.respond(f"⬇️ دانلود {idx+1}/{len(selected)}: {video['title']} ({actual_quality}kbps)")
                                    user_info = {
                                        'video_formats': self.user_data[user_id]['playlist_video_formats'][video_id],
                                        'audio_formats': self.user_data[user_id]['playlist_audio_formats'][video_id],
                                        'best_audio': best_audio['format_id'] if best_audio else None
                                    }
                                    result = await self.send_from_cache_or_download(event, video['url'], format_id, 0, is_audio=True, user=user_info, audio_bitrate=actual_quality)
                                if result is None:
                                    # اگر محدودیت خوردیم، حلقه را قطع کن و پیام را فقط یکبار بده
                                    if not limit_reached:
                                        await event.respond(f"🚫 شما به محدودیت روزانه دانلود یا حجم رسیده‌اید. ادامه دانلود متوقف شد.")
                                    limit_reached = True
                                    break
                                
                                await event.respond("تا دانلود بعدی 30 ثانیه صبر کنید.")
                                await asyncio.sleep(30)  # rate limit بین دانلودها
                            except Exception as e:
                                await event.respond(f"❌ خطا در دانلود {video['title']}: {e}")
                        if not limit_reached:
                            await event.respond(f"✅ دانلود گروهی تمام شد!")
                        return
                    elif data == "playlist_cancel":
                        # حذف حالت پلی‌لیست و نمایش پیام لغو
                        self.user_data[user_id].pop('playlist_mode', None)
                        self.user_data[user_id].pop('playlist_info', None)
                        self.user_data[user_id].pop('playlist_selected_videos', None)
                        self.user_data[user_id].pop('playlist_url', None)
                        self.user_data[user_id].pop('awaiting_custom_range', None)
                        self.user_data[user_id].pop('playlist_select_page', None)
                        self.user_data[user_id].pop('playlist_selected_format', None)
                        await event.edit("فرآیند انتخاب لغو شد.")
                        return
                # --- End Playlist menu logic ---
                # منطق قبلی دانلود ویدیو تکی:
                youtube_url = user['youtube_url']
                selected_format = data.replace('format_', '').replace('audio_', '')
                if self.download_status.get(user_id, 0) == 1:
                    await event.answer("Download in progress. Please wait.", alert=True)
                    return
                downloading_message = await event.edit("در حال دانلود... لطفا صبر کنید.", buttons=None)
                self.download_status[user_id] = 1
                try:
                    if data.startswith("youtube_audio_"):
                        format_id = data.replace("youtube_audio_", "")
                        # For audio, get the bitrate from the format
                        audio_bitrate = None
                        selected_audio_format = None
                        for f in user.get('audio_formats', []):
                            if f['format_id'] == format_id:
                                audio_bitrate = f.get('abr', 0)
                                selected_audio_format = f
                                break
                        if selected_audio_format:
                            logger.info(f"User selected audio: format_id={selected_audio_format.get('format_id')}, ext={selected_audio_format.get('ext')}, acodec={selected_audio_format.get('acodec')}, abr={selected_audio_format.get('abr')}")
                        # For audio, quality is not relevant, pass 0
                        await self.send_from_cache_or_download(event, youtube_url, format_id, 0, is_audio=True, user=user, audio_bitrate=audio_bitrate)
                    elif data.startswith("youtube_format_"):
                        format_id = data.replace("youtube_format_", "")
                        # Extract quality from the format
                        quality = 0
                        selected_video_format = None
                        for f in user.get('video_formats', []):
                            if f['format_id'] == format_id:
                                quality = f.get('height', 0)
                                selected_video_format = f
                                break
                        if selected_video_format:
                            logger.info(f"User selected video: format_id={selected_video_format.get('format_id')}, ext={selected_video_format.get('ext')}, vcodec={selected_video_format.get('vcodec')}, acodec={selected_video_format.get('acodec')}, tbr={selected_video_format.get('tbr')}, height={selected_video_format.get('height')}")
                        await self.send_from_cache_or_download(event, youtube_url, format_id, quality, is_audio=False, user=user)
                    await downloading_message.delete()
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

        # هندلر دکمه زیرنویس
        @self.client.on(events.CallbackQuery(pattern=b"youtube_subtitle"))
        async def handle_subtitle_button(event):
            user_id = event.sender_id
            user = self.user_data.get(user_id)
            if not user:
                await event.reply("لینک معتبر پیدا نشد. لطفاً مجدداً تلاش کنید.")
                return
            youtube_url = user.get('youtube_url')
            if not youtube_url:
                await event.reply("آدرس ویدیو یافت نشد. لطفاً دوباره لینک ارسال کنید.")
                return
            await self.present_subtitle_language_menu(event, user_id, youtube_url)
        @self.client.on(events.CallbackQuery(pattern=b"yt_sub_translate\|\|\|"))
        async def handle_subtitle_translate(event):
            data = event.data.decode().split('|||')
            cb_user_id, target_lang = int(data[1]), data[2]
            if event.sender_id != cb_user_id:
                await event.reply("این درخواست متعلق به شما نیست.")
                return
            url = self.user_requests.get(cb_user_id)
            if not url:
                await event.reply("آدرس منقضی شده یا پیدا نشد. لطفاً دوباره تلاش کنید.")
                return
            buttons = [
                [Button.inline(".srt", f"yt_sub_format|||{cb_user_id}|||{target_lang}|||srt".encode())],
                [Button.inline(".txt", f"yt_sub_format|||{cb_user_id}|||{target_lang}|||txt".encode())]
            ]
            await event.reply("فرمت فایل زیرنویس را انتخاب کنید:", buttons=buttons)
        @self.client.on(events.CallbackQuery(pattern=b"yt_sub_format\|\|\|"))
        async def handle_subtitle_format(event):
            data = event.data.decode().split('|||')
            cb_user_id, target_lang, file_format = int(data[1]), data[2], data[3]
            if event.sender_id != cb_user_id:
                await event.reply("این درخواست متعلق به شما نیست.")
                return
            url = self.user_requests.get(cb_user_id)
            if not url:
                await event.reply("آدرس منقضی شده یا پیدا نشد. لطفاً دوباره تلاش کنید.")
                return
            await event.reply("در حال دانلود زیرنویس...")
            import tempfile
            try:
                with tempfile.TemporaryDirectory(dir="downloads") as tempdir:
                    subtitle_file = self.fetch_and_save_subtitles_yt_dlp(url, target_lang, file_format, output_file=None, tempdir=tempdir)
                    self.user_requests.pop(cb_user_id, None)
                    if subtitle_file:
                        await event.reply(file=subtitle_file)
                    else:
                        if target_lang != 'en':
                            # تلاش برای ترجمه زیرنویس انگلیسی
                            en_subtitle_file = self.fetch_and_save_subtitles_yt_dlp(url, 'en', file_format, output_file=None, tempdir=tempdir)
                            if en_subtitle_file:
                                from googletrans import Translator
                                import re
                                translator = Translator()
                                def translate_srt_file(input_file, output_file, dest_lang):
                                    with open(input_file, 'r', encoding='utf-8') as f:
                                        lines = f.readlines()
                                    new_lines = []
                                    buffer = []
                                    for line in lines:
                                        if re.match(r"^\d+$", line.strip()) or "-->" in line or not line.strip():
                                            if buffer:
                                                text = ' '.join(buffer)
                                                try:
                                                    translated = translator.translate(text, dest=dest_lang).text
                                                except Exception:
                                                    translated = text
                                                new_lines.append(translated + '\n')
                                                buffer = []
                                            new_lines.append(line)
                                        else:
                                            buffer.append(line.strip())
                                    if buffer:
                                        text = ' '.join(buffer)
                                        try:
                                            translated = translator.translate(text, dest=dest_lang).text
                                        except Exception:
                                            translated = text
                                        new_lines.append(translated + '\n')
                                    with open(output_file, 'w', encoding='utf-8') as f:
                                        f.writelines(new_lines)
                                translated_file = os.path.join(tempdir, f"translated_{target_lang}.srt" if file_format == 'srt' else f"translated_{target_lang}.txt")
                                translate_srt_file(en_subtitle_file, translated_file, target_lang)
                                await event.reply(file=translated_file)
                                return
                        await event.reply("دریافت زیرنویس با خطا مواجه شد یا زیرنویس برای این زبان موجود نیست.")
            except Exception as e:
                logger.error(f"Error in subtitle extraction: {e}")
                await event.reply("متأسفانه خطایی در دریافت زیرنویس رخ داد. لطفاً دوباره تلاش کنید.")
                try:
                    await self.client.send_message(5019214713, f"[YouTubeDownloader Subtitle Error]\nUser: {cb_user_id}\nURL: {url}\nLang: {target_lang}\nFormat: {file_format}\nError: {str(e)}")
                except Exception as admin_err:
                    logger.error(f"Failed to send error to admin: {admin_err}")
            finally:
                user_state = self.user_data.get(cb_user_id)
                if user_state and user_state.get('subtitle_only'):
                    self.user_data.pop(cb_user_id, None)
                self.awaiting_subtitle_urls.discard(cb_user_id)

    async def handle_video(self, event, youtube_url):
        user_id = event.sender_id
        if config.active_youtube_downloads >= config.YOUTUBE_DOWNLOAD_THRESHOLD:
            if self.youtube_selfbot_downloader:
                await self.youtube_selfbot_downloader.handle_url(event, youtube_url)
            else:
                await event.reply("دانلودر  در دسترس نیست.")
            return
        config.active_youtube_downloads += 1
        print(f"active download : {config.active_youtube_downloads}")
        
        
        # --- VIP logic for file size limit ---
        is_vip = await self.user_db.is_vip(user_id)
        if is_vip:
            user_limit = 1.5 * 1024 * 1024 * 1024  # 1.5GB
        else:
            user_limit = 500 * 1024 * 1024  # 500MB
        if self.download_status.get(user_id, 0) == 1:
            await event.reply("در حال دانلود... لطفا صبر کنید تا دانلود قبلی کامل شود.")
            return
        self.download_status[user_id] = 1
        try:
            search_message = await asyncio.wait_for(event.reply("در حال جستجو... لطفا صبر کنید."), timeout=YTDL_TIMEOUT)
            best_audio, video_formats, audio_formats = await asyncio.get_event_loop().run_in_executor(
                None, self.get_available_formats, youtube_url
            )
            if not video_formats:
                await event.reply("هیچ نتیجه ای یافت نشد.")
                return
            buttons = []
            # Group video formats by resolution (height)
            from collections import defaultdict
            grouped = defaultdict(list)
            for f in video_formats:
                res = f.get('height') or f.get('resolution') or f.get('format_note') or f.get('format_id')
                grouped[res].append(f)
            # For each group, pick the best (highest tbr/bitrate)
            representatives = []
            for res, group in grouped.items():
                # فقط ویدیوهایی که صوت سازگار دارند
                compatible_videos = []
                for v in group:
                    if find_compatible_audio(v, audio_formats):
                        compatible_videos.append(v)
                if compatible_videos:
                    # از بین ویدیوهای سازگار، بهترین را انتخاب کن
                    best = max(compatible_videos, key=lambda x: x.get('tbr', 0) or 0)
                else:
                    # اگر هیچ ویدیوی سازگار نبود، همان منطق قبلی
                    best = max(group, key=lambda x: x.get('tbr', 0) or 0)
                representatives.append(best)
            for f in representatives:
                quality = f.get('format_note') or f.get('resolution') or (f.get('height') and f"{f['height']}p") or f.get('format_id')
                ext = f.get('ext', 'unknown')
                video_size = self.get_format_size(f)
                merged_size = video_size
                estimated = False
                if f.get('acodec') == 'none' and audio_formats:
                    compatible_audio = find_compatible_audio(f, audio_formats)
                    if compatible_audio:
                        audio_size = self.get_format_size(compatible_audio)
                        if audio_size is not None:
                            merged_size = video_size + audio_size if video_size is not None else audio_size
                            if video_size is None:
                                estimated = True
                        elif video_size is None:
                            estimated = True
                    else:
                        estimated = True
                elif video_size is None:
                    estimated = True
                if merged_size is not None:
                    size_str = f"{(merged_size / (1024 * 1024)):.2f} MB{' (تخمینی)' if estimated else ''}"
                else:
                    size_str = "حجم نامشخص"
                label = f"🎥 {quality} ({ext}, {size_str})"
                if merged_size is not None and merged_size > user_limit:
                    label += " 🚫"
                buttons.append([
                    Button.inline(
                        label,
                        f"youtube_format_{f['format_id']}"
                    )
                ])
            for af in audio_formats:
                quality = af.get('format_note') or af.get('abr') or af.get('format_id')
                ext = af.get('ext', 'unknown')
                size = af.get('filesize')
                size_str = f"{(size / (1024 * 1024)):.2f} MB" if size else "Unknown Size"
                abr = af.get('abr')
                label = f"🎵 Audio {abr}kbps ({ext}, {size_str})" if abr else f"🎵 Audio ({ext}, {size_str})"
                merged_size = size
                if merged_size is not None and merged_size > user_limit:
                    label += " 🚫"
                buttons.append([
                    Button.inline(
                        label,
                        f"youtube_audio_{af['format_id']}"
                    )
                ])
            buttons.append([
                Button.inline("📝 دریافت زیرنویس یوتیوب", b"youtube_subtitle")
            ])
            if buttons:
                self.user_data[user_id] = {
                    'youtube_url': youtube_url,
                    'best_audio': best_audio['format_id'] if best_audio else None,
                    'video_formats': video_formats,
                    'audio_formats': audio_formats,
                    'user_limit': int(user_limit),
                    'is_vip': is_vip
                }
                await event.reply("نوع و کیفیت فایل را انتخاب کنید:", buttons=buttons)
                await search_message.delete()
                # --- تایم‌اوت انتخاب کیفیت با پارامتر ---
                async def quality_timeout(user_id, chat_id):
                    await asyncio.sleep(QUALITY_SELECT_TIMEOUT)
                    if user_id in self.user_data:
                        await self.client.send_message(chat_id, "انتخاب کیفیت بیش از حد طول کشید. درخواست شما لغو شد ، دوباره لینک ارسال کنید.⏰")
                        self.download_status[user_id] = 0
                        del self.user_data[user_id]
                        config.active_youtube_downloads -= 1
                task = asyncio.create_task(quality_timeout(user_id, event.chat_id))
                self.quality_timeout_tasks[user_id] = task
            else:
                await event.reply("No formats available.")
                await search_message.delete()
        except asyncio.TimeoutError:
            logger.error(f"[YouTubeDownloader] Timeout in handle_video for user {user_id}")
            await event.reply("⏰ دریافت اطلاعات ویدیو بیش از حد طول کشید. لطفاً دوباره تلاش کنید.")
            if user_id in self.user_data:
                self.download_status[user_id] = 0
                del self.user_data[user_id]
        except DownloadError as e:
            logger.error(f"Download error in handle_video: {e}")
            await event.reply("خطا در دانلود ویدیو. لطفاً لینک را بررسی کنید.")
        except ExtractorError as e:
            logger.error(f"Extractor error in handle_video: {e}")
            await event.reply("خطا در استخراج اطلاعات ویدیو. لطفاً لینک را بررسی کنید.")
        except Exception as e:
            logger.error(f"Error in handle_video: {e}")
            # Send simple error to user
            await event.reply("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            # Send full error to admin
            try:
                await self.client.send_message(5019214713, f"[YouTubeDownloader Error]\nUser: {user_id}\nURL: {youtube_url}\nError: {str(e)}")
            except Exception as admin_err:
                logger.error(f"Failed to send error to admin: {admin_err}")
        finally:
            self.download_status[user_id] = 0

async def register_handlers(client: TelegramClient, youtube_selfbot_downloader=None):
    try:
        downloader = YouTubeDownloader(client, youtube_selfbot_downloader=youtube_selfbot_downloader)
        downloader.register_handlers()
        return downloader
    except Exception as e:
        logger.error(f"Error registering handlers: {e}")
        raise 

async def send_advertisement_message(client, event):
    """
    بعد از ارسال فایل به کاربر، این تابع یک پیام تبلیغاتی به صورت چرخشی با دکمه به کاربر ارسال می‌کند.
    دکمه به آیدی کانال یا ربات هدایت می‌کند.
    """
    from telethon import Button
    global ad_index
    ads = list(zip(AD_MESSAGE_TEXTS, AD_BUTTON_IDS))
    if not ads:
        return
    ad_text, ad_id = ads[ad_index % len(ads)]
    ad_index = (ad_index + 1) % len(ads)
    await client.send_message(
        event.chat_id,
        ad_text,
        buttons=[Button.url('عضویت/رفتن', ad_id)]
    )