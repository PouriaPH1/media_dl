import os
import re
import uuid
import logging
from telethon import events, Button
import requests
import asyncio

logger = logging.getLogger(__name__)

class GenericSelfbotDownloader:
    def __init__(self, client, selfbot_manager, patterns, **kwargs):
        self.client = client
        if selfbot_manager is None:
            raise ValueError("selfbot_manager is required and must be shared between downloaders.")
        self.selfbot_manager = selfbot_manager
        # patterns: list of dicts: {pattern: str, bot_username: str, media_filter: Optional[str]}
        # media_filter can be: None (no filter) or 'audio_only'
        self.patterns = self._compile_patterns(patterns)
        self.download_status = {}
        self.channels = kwargs.get('channels', [])
        self.ADMIN_BOT_TOKEN = kwargs.get('admin_bot_token', None)
        self.pending_requests = {}  # user_id -> event
        
        
        # --- Start bot for all selfbots after startup ---
        asyncio.create_task(self.start_bot_for_all())
        # # ثبت callback برای دکمه‌ها
        if hasattr(self.selfbot_manager, 'set_button_callback_general'):
            self.selfbot_manager.set_button_callback_general(self.on_buttons_received)
      
    def _compile_patterns(self, patterns):
        compiled = []
        for idx, p in enumerate(patterns):
            try:
                media_filter = p.get('media_filter') or ('audio_only' if p.get('only_audio') else None)
                compiled.append((re.compile(p['pattern'], re.IGNORECASE), p['bot_username'], media_filter))
            except re.error as e:
                logger = logging.getLogger(__name__)
                logger.error(f"Invalid regex at index {idx}: {p.get('pattern')} — {e}")
                raise ValueError(f"Invalid regex at index {idx}: {e}")
        return compiled

    def set_patterns(self, patterns):
        """Update patterns at runtime. patterns is a list of dicts with keys pattern and bot_username."""
        self.patterns = self._compile_patterns(patterns)

    def get_patterns(self):
        """Return current patterns as list of dicts for admin viewing."""
        result = []
        for pattern, bot_username, media_filter in self.patterns:
            try:
                raw = pattern.pattern
            except Exception:
                raw = str(pattern)
            result.append({"pattern": raw, "bot_username": bot_username, "media_filter": media_filter})
        return result

    def get_bot_for_url(self, url):
        for pattern, bot_username, media_filter in self.patterns:
            if pattern.match(url):
                return bot_username, media_filter
        return None, None

    async def handle_url(self, event, url):
        user_id = event.sender_id
        if self.download_status.get(user_id, 0) == 1:
            await event.reply("در حال دانلود... لطفا صبر کنید تا دانلود قبلی کامل شود.")
            return
        self.download_status[user_id] = 1
        self.pending_requests[user_id] = event  # ذخیره event برای پیام بعدی
        try:
            not_joined_channels = self.join_checker(user_id)
            if not_joined_channels:
                await self.send_channel_links(event, not_joined_channels)
                self.download_status[user_id] = 0
                return
            await event.reply("در حال دانلود... لطفا صبر کنید.")
            bot_username, media_filter = self.get_bot_for_url(url)
            if not bot_username:
                await event.reply("این لینک پشتیبانی نمی‌شود.")
                return
            if self.selfbot_manager:
                request_uuid = str(uuid.uuid4())
                group_id = self.selfbot_manager.group_chat_id
                msg_text = f"{user_id}|{request_uuid}|{url}"
                await self.client.send_message(group_id, msg_text)
                # pass media_filter preference to selfbot manager so it can filter received media
                future = self.selfbot_manager.get_or_create_future(request_uuid, bot_username=bot_username, media_filter=media_filter)
                try:
                    message_ids = await asyncio.wait_for(future, timeout=60)
                    group_entity = await self.client.get_entity(self.selfbot_manager.group_chat_id)
                    collected_media = []
                    for message_id in message_ids:
                        msg = await self.client.get_messages(group_entity, ids=message_id)
                        if msg and getattr(msg, 'media', None):
                            collected_media.append(msg.media)
                    if collected_media:
                        ALBUM_LIMIT = 10  # Telegram limits media groups to 10 items
                        caption_text = "Downloaded by🚀 @media_dlrobot"
                        for index in range(0, len(collected_media), ALBUM_LIMIT):
                            batch = collected_media[index:index + ALBUM_LIMIT]
                            caption = caption_text if index == 0 else None
                            await self.client.send_file(
                                event.chat_id,
                                batch,
                                caption=caption
                            )
                except asyncio.TimeoutError:
                    logger.error("Timeout waiting for selfbot download.")
                    await event.reply("متاسفانه ارسال فایل بیش از حد طول کشید. لطفاً دوباره تلاش کنید.")
                    self.download_status[user_id] = 0
                    self.selfbot_manager.pop_future(request_uuid)
                    return
                finally:
                    self.selfbot_manager.pop_future(request_uuid)
        except Exception as e:
            logger.error(f"Error in GenericSelfbotDownloader: {e}")
        finally:
            self.download_status[user_id] = 0

    def join_checker(self, user_id: int) -> list:
        not_joined_channels = []
        for channel in getattr(self, 'channels', []):
            try:
                channel_username = channel.lstrip('@')
                response = requests.get(
                    f"https://api.telegram.org/bot{getattr(self, 'ADMIN_BOT_TOKEN', '')}/getChatMember?chat_id=@{channel_username}&user_id={user_id}",
                    timeout=10,
                    verify=True
                )
                if response.status_code == 200:
                    status = response.json()['result']['status']
                    if status not in ["member", "administrator", "creator"]:
                        not_joined_channels.append(channel)
                else:
                    not_joined_channels.append(channel)
                    if hasattr(self, 'logger'):
                        self.logger.warning(f"Failed to check membership for channel {channel}: {response.status_code}")
                    continue
            except requests.exceptions.SSLError as e:
                if hasattr(self, 'logger'):
                    self.logger.error(f"SSL Error checking channel membership: {e}")
                not_joined_channels.append(channel)
                continue
            except requests.exceptions.RequestException as e:
                if hasattr(self, 'logger'):
                    self.logger.error(f"Request error checking channel membership: {e}")
                not_joined_channels.append(channel)
                continue
            except Exception as e:
                if hasattr(self, 'logger'):
                    self.logger.error(f"Error checking channel membership: {e}")
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
            if hasattr(self, 'logger'):
                self.logger.error(f"Error sending channel links: {e}")
            try:
                await event.respond("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
            except:
                pass
            return True

    async def on_buttons_received(self, request_uuid, buttons, text):
        import re
        from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError, InviteHashInvalidError
        from telethon.tl.functions.messages import ImportChatInviteRequest
        from telethon.tl.functions.channels import JoinChannelRequest
        logger = logging.getLogger(__name__)
        join_channel_urls = []
        for row in buttons:
            for btn in row:
                if hasattr(btn, 'url') and btn.url and re.match(r'https?://t\.me/(joinchat/|\+|[\w\d_]+)', btn.url):
                    join_channel_urls.append(btn.url)
        if join_channel_urls:
            for channel_url in join_channel_urls:
                for client in getattr(self.selfbot_manager, 'clients', []):
                    try:
                        group_entity = channel_url.split('https://t.me/')[-1].strip('/')
                        if group_entity.startswith('+') or 'joinchat/' in group_entity:
                            invite_hash = group_entity.replace('joinchat/', '').replace('+', '')
                            await client(ImportChatInviteRequest(invite_hash))
                        else:
                            await client(JoinChannelRequest(group_entity))
                    except UserAlreadyParticipantError:
                        pass
                    except (InviteHashExpiredError, InviteHashInvalidError):
                        pass
                    except Exception as e:
                        logger.warning(f"[Selfbot Join] Failed to join {channel_url} for {getattr(client.session, 'filename', client)}: {e}")
            # پیام به کاربر برای ارسال مجدد لینک (در صورت وجود event)
            # فرض: request_uuid همان user_id است (در این ساختار)
            user_id = None
            try:
                user_id = int(request_uuid)
            except Exception:
                pass
            if user_id and user_id in self.pending_requests:
                event = self.pending_requests.pop(user_id)
                await self.client.send_message(event.chat_id, " لطفاً لینک را دوباره ارسال کنید.")
            return

    async def start_bot_for_all(self):
        """Send /start to the bot from all selfbot accounts after startup."""
        logger = logging.getLogger(__name__)
        if not hasattr(self.selfbot_manager, 'clients'):
            logger.warning("No selfbot clients found.")
            return
        # جمع‌آوری همه bot_usernameها از patterns
        bot_usernames = set(bot_username for _, bot_username, _ in self.patterns)
        logger.info(f"Loaded {len(self.selfbot_manager.clients)} selfbot accounts. Sending /start to bots: {bot_usernames}")
        for client in self.selfbot_manager.clients:
            for bot_username in bot_usernames:
                try:
                    await client.send_message(bot_username, "/start")
                    logger.info(f"[+] /start sent from: {getattr(client.session, 'filename', client)} to {bot_username}")
                except Exception as e:
                    logger.warning(f"[!] Failed to send /start from {getattr(client.session, 'filename', client)} to {bot_username}: {e}")

    def register_handlers(self):
        @self.client.on(events.NewMessage)
        async def handle_message(event):
            url = event.text.strip()
            for pattern, _, _ in self.patterns:
                if pattern.match(url):
                    await self.handle_url(event, url)
                    break

        # تحویل فایل‌های مربوط به مسیر شازم از گروه سلف‌بات‌ها به کاربر
        try:
            group_chat_id = getattr(self.selfbot_manager, 'group_chat_id', None)
            if group_chat_id:
                @self.client.on(events.NewMessage(chats=group_chat_id))
                async def handle_group_delivery(ev):
                    try:
                        caption = (getattr(ev, 'raw_text', '') or '').strip()
                        if not caption or '|' not in caption:
                            return
                        user_id_part, uuid_part = caption.split('|', 1)
                        uuid_part = uuid_part.strip()
                        # فقط درخواست‌های شازم را هندل کن
                        if not uuid_part.lower().startswith('shazam-'):
                            return
                        # Forward/send file to the original user
                        if ev.media:
                            try:
                                target_id = int(user_id_part)
                            except Exception:
                                return
                            await self.client.send_file(
                                target_id,
                                ev.media,
                                caption="Downloaded by🚀 @media_dlrobot"
                            )
                    except Exception as e:
                        logger.error(f"[GeneralDownloader] Error delivering shazam file: {e}")
        except Exception:
            # اگر به هر دلیل نتوانستیم لیسنر گروه را ثبت کنیم، ادامه می‌دهیم
            pass
