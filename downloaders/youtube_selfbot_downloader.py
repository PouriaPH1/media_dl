import re
import uuid
import logging
import asyncio
from telethon import events, Button
import config
from config import youtube_selfbot_bot_username

from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError, InviteHashInvalidError
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest
from downloaders.user_db import UserDB

logger = logging.getLogger(__name__)

QUALITY_TIMEOUT = 30  # ثانیه
DOWNLOAD_TIMEOUT = 120  # ثانیه
QUALITY_SELECT_TIMEOUT = 60  # ثانیه

class YouTubeSelfbotDownloader:
    """
    Handles YouTube download requests via selfbot and @TopSaverBot.
    Step-by-step logic:
    1. Detect YouTube links and send them to @TopSaverBot via the selfbot group.
    2. Wait for the quality selection message from @TopSaverBot (via selfbot).
    3. Extract inline buttons (qualities) and forward them to the user, mapping uuid/user.
    4. When the user selects a quality, send the corresponding callback to @TopSaverBot via selfbot.
    5. Wait for the file from @TopSaverBot, then forward it to the user, maintaining file delivery logic.
    """
    async def start_bot_for_all(self):
        """Send /start to the bot from all selfbot accounts after startup."""
        await self.selfbot_manager.start_all()
        logger.info(f"Loaded {len(self.selfbot_manager.clients)} selfbot accounts. Sending /start to bot: {self.bot_username}")
        for client in self.selfbot_manager.clients:
            try:
                await client.send_message(self.bot_username, "/start")
                logger.info(f"[+] /start sent from: {getattr(client.session, 'filename', client)}")
            except Exception as e:
                logger.warning(f"[!] Failed to send /start from {getattr(client.session, 'filename', client)}: {e}")

    def __init__(self, client, selfbot_manager, **kwargs):
        self.client = client
        self.selfbot_manager = selfbot_manager
        self.youtube_pattern = re.compile(r"(https?://)?(www\.|m\.)?(youtube\.com|youtu\.be)/", re.IGNORECASE)
        self.download_status = {}
        self.pending_requests = {}  # uuid -> {user_id, event, ...}
        self.channels = kwargs.get('channels', [])
        self.ADMIN_BOT_TOKEN = kwargs.get('admin_bot_token', None)
        self.bot_username = youtube_selfbot_bot_username
        self.user_db = UserDB()
        # Register callback for button info from selfbot
        self.selfbot_manager.set_button_callback_youtube(self.on_buttons_received)
        # --- Start bot for all selfbots after startup ---
        asyncio.create_task(self.start_bot_for_all())

    def set_bot_username(self, new_username: str):
        try:
            if not isinstance(new_username, str) or not new_username.strip():
                raise ValueError("Invalid bot username")
            self.bot_username = new_username.strip()
            logger.info(f"[YTSelfbot] Updated bot_username to {self.bot_username}")
        except Exception as e:
            logger.error(f"[YTSelfbot] Failed to update bot_username: {e}")
            raise

    async def on_buttons_received(self, request_uuid, buttons, text):
        import logging
        logger = logging.getLogger(__name__)
        req = self.pending_requests.get(request_uuid)
        if not req or req['status'] != 'waiting_quality':
            return
        # --- Filter: Only process if the sender is @TopSaverBot ---
        # Try to get the sender username from the message object
        sender_username = None
        if buttons and buttons[0] and hasattr(buttons[0][0], 'message'):
            msg = buttons[0][0].message
            if hasattr(msg, 'sender') and hasattr(msg.sender, 'username'):
                sender_username = getattr(msg.sender, 'username', None)
            elif hasattr(msg, 'from_id') and hasattr(msg, 'peer_id'):
                # fallback: try to get username from from_id (not always available)
                pass
        # If sender_username is not @TopSaverBot, skip
        if sender_username and sender_username.lower() != self.bot_username.lstrip('@').lower():
            logger.info(f"[YTSelfbot] Skipping button callback for uuid={request_uuid} because sender is not {self.bot_username}: {sender_username}")
            return
        # بررسی وجود دکمه جوین چنل
        import re
        join_channel_urls = []
        for row in buttons:
            for btn in row:
                if hasattr(btn, 'url') and btn.url and re.match(r'https?://t\.me/(joinchat/|\+|[\w\d_]+)', btn.url):
                    join_channel_urls.append(btn.url)
        if join_channel_urls:
            from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError, InviteHashInvalidError
            from telethon.tl.functions.messages import ImportChatInviteRequest
            from telethon.tl.functions.channels import JoinChannelRequest
            for channel_url in join_channel_urls:
                for client in self.selfbot_manager.clients:
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
            await self.client.send_message(req['event'].chat_id, "ا لطفاً دوباره لینک را ارسال کنید.")
            self.download_status[req['user_id']] = 0
            if request_uuid in self.pending_requests:
                del self.pending_requests[request_uuid]
            import config
            if config.active_youtube_downloads > 0:
                config.active_youtube_downloads -= 1
            return
        # --- ادامه منطق قبلی (دکمه کیفیت) ---
        # Extract button texts and callback data
        button_texts_and_data = []
        for row in buttons:
            new_row = []
            for btn in row:
                if hasattr(btn, 'text') and hasattr(btn, 'data') and btn.data:
                    new_row.append((btn.text, btn.data))
            if new_row:
                button_texts_and_data.append(new_row)
        if button_texts_and_data:
            user_event = req['event']
            new_keyboard = [
                [Button.inline(text, data) for (text, data) in row]
                for row in button_texts_and_data
            ]
            await self.client.send_message(
                user_event.chat_id,
                "نوع و کیفیت فایل را انتخاب کنید:",
                buttons=new_keyboard
            )
            logger.info(f"[DEBUG] Sent keyboard to user {user_event.chat_id}")
            # --- حل مشکل تایم‌اوت دریافت کیفیت ---
            future = self.selfbot_manager.pop_future(request_uuid)
            if future and not future.done():
                future.set_result(True)
            req['status'] = 'waiting_user_quality'
            # Store the original selfbot message for callback click
            if buttons and buttons[0] and hasattr(buttons[0][0], 'message'):
                req['selfbot_msg'] = buttons[0][0].message
                req['selfbot_msg_id'] = buttons[0][0].message.id
            # --- پاک‌سازی future مربوط به دریافت کیفیت (برای جلوگیری از تایم‌اوت اشتباه) ---
            self.selfbot_manager.pop_future(request_uuid)
            # --- تایم‌اوت انتخاب کیفیت ---
            async def quality_timeout(request_uuid, user_id, chat_id):
                await asyncio.sleep(QUALITY_SELECT_TIMEOUT)
                req = self.pending_requests.get(request_uuid)
                if req and req['status'] == 'waiting_user_quality':
                    await self.client.send_message(chat_id, "انتخاب کیفیت بیش از حد طول کشید. درخواست شما لغو شد ، دوباره لینک ارسال کنید.⏰")
                    self.download_status[user_id] = 0
                    if request_uuid in self.pending_requests:
                        del self.pending_requests[request_uuid]
                    import config
                    if config.active_youtube_downloads > 0:
                        config.active_youtube_downloads -= 1
            asyncio.create_task(quality_timeout(request_uuid, req['user_id'], user_event.chat_id))

    async def handle_url(self, event, url):
        user_id = event.sender_id
        if self.download_status.get(user_id, 0) == 1:
            await event.reply("در حال دانلود... لطفا صبر کنید تا دانلود قبلی کامل شود.")
            return
        self.download_status[user_id] = 1
        request_uuid = str(uuid.uuid4())
        group_id = self.selfbot_manager.group_chat_id
        msg_text = f"{user_id}|{request_uuid}|{url}"
        # Save mapping for later steps
        self.pending_requests[request_uuid] = {
            'user_id': user_id,
            'event': event,
            'url': url,
            'status': 'waiting_quality',
        }
        await self.client.send_message(group_id, msg_text)
        # Ensure bot_username is set for this uuid in SelfBotManager
        self.selfbot_manager.get_or_create_future(request_uuid, bot_username=self.bot_username)
        await event.reply("در حال جستجو... لطفا صبر کنید.")
        # --- Wait for quality buttons with timeout ---
        try:
            future = self.selfbot_manager.get_or_create_future(request_uuid, bot_username=self.bot_username)
            await asyncio.wait_for(future, timeout=QUALITY_TIMEOUT)
        except asyncio.TimeoutError:
            logger.error(f"[YTSelfbot] Timeout waiting for quality buttons for uuid={request_uuid}")
            await event.reply("متاسفانه دریافت لیست کیفیت‌ها بیش از حد طول کشید. لطفا مجددا تلاش کنید.")
            self.selfbot_manager.pop_future(request_uuid)
            if request_uuid in self.pending_requests:
                del self.pending_requests[request_uuid]
            self.download_status[user_id] = 0
            if config.active_youtube_downloads > 0:
                config.active_youtube_downloads -= 1
            return

    def register_group_handler(self):
        import logging
        logger = logging.getLogger(__name__)
        main_client = self.selfbot_manager.clients[0]
        group_chat_id = self.selfbot_manager.group_chat_id
        def extract_uuid_from_event(event):
            for attr in ['text', 'message', 'caption', 'raw_text']:
                val = getattr(event, attr, None)
                if val and '|' in val:
                    parts = val.strip().split('|')
                    if len(parts) >= 2:
                        logger.info(f"[YTSelfbot] Extracted uuid from {attr}: {parts[1]}")
                        return parts[1]
            logger.warning(f"[YTSelfbot] Could not extract uuid from event: {event}")
            return None
        @main_client.on(events.NewMessage(chats=group_chat_id))
        async def group_handler(event):
            logger.info(f"[YTSelfbot] group_handler triggered! event.id={event.id}, media={type(event.media)}, text={getattr(event, 'text', None)}")
            if event.media:
                uuid = extract_uuid_from_event(event)
                if uuid:
                    from telethon.tl.types import MessageMediaDocument
                    if isinstance(event.media, MessageMediaDocument):
                        doc = event.document
                        if hasattr(doc, 'mime_type') and (doc.mime_type.startswith('video/') or doc.mime_type.startswith('audio/')):
                            entry = self.selfbot_manager.pending_futures.get(uuid)
                            if entry and not entry['future'].done():
                                entry['future'].set_result([event.id])
                                logger.info(f"[YTSelfbot] Set future result for uuid={uuid} with event.id={event.id}")
                        else:
                            logger.info(f"[YTSelfbot] Skipping non-video/audio document for uuid={uuid}: {getattr(doc, 'mime_type', None)}")
                    else:
                        logger.info(f"[YTSelfbot] Skipping non-document media for uuid={uuid}: {type(event.media)}")
            # No direct file sending here

    def register_callback_handler(self):
        @self.client.on(events.CallbackQuery())
        async def handle_user_quality_callback(event):
            user_id = event.sender_id
            data = event.data
            # --- VIP logic ---
            is_vip = await self.user_db.is_vip(user_id)
            if is_vip:
                DEFAULT_DAILY_COUNT = 50
                DEFAULT_DAILY_SIZE = 20 * 1024 * 1024 * 1024
                
                
            else:
                from config import DAILY_COUNT_LIMIT, DAILY_SIZE_LIMIT
                DEFAULT_DAILY_COUNT = DAILY_COUNT_LIMIT
                DEFAULT_DAILY_SIZE = DAILY_SIZE_LIMIT
            
            import datetime
            today = datetime.date.today().strftime('%Y-%m-%d')
            # --- بررسی لیمیت قبل از دانلود ---
            # مقادیر پیش‌فرض مشابه دانلودر اصلی
            
            limits = await self.user_db.get_limits(user_id, today, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)
            remaining_bonus_count = limits['bonus_count']
            remaining_bonus_size = limits['bonus_size']
            remaining_daily_count = DEFAULT_DAILY_COUNT - limits['daily_count']
            remaining_daily_size = DEFAULT_DAILY_SIZE - limits['daily_size']
            # فقط تعداد را چک می‌کنیم (حجم فایل را بعداً می‌دانیم)
            total_count_left = remaining_bonus_count + remaining_daily_count
            if total_count_left <= 0:
                await event.answer("🚫 شما به سقف مجاز تعداد دانلود رسیده‌اید. لطفاً فردا دوباره تلاش کنید یا با دعوت دوستان جایزه بگیرید یا حساب خود را با خرید اشتراک ارتقا دهید.", alert=True)
                return
            # --- چک حجم روزانه (مثلاً اگر کمتر از 1 گیگ باقی مانده باشد، اجازه دانلود نده) ---
            if remaining_daily_size < DEFAULT_DAILY_COUNT and remaining_bonus_size < DEFAULT_DAILY_COUNT:
                await event.answer("🚫 حجم باقی‌مانده روزانه یا بونس شما برای دانلود فایل جدید کافی نیست. لطفاً فردا دوباره تلاش کنید یا با دعوت دوستان جایزه بگیرید یا حساب خود را با خرید اشتراک ارتقا دهید.", alert=True)
                return
            # Find the pending request for this user in 'waiting_user_quality' state
            for uuid, req in list(self.pending_requests.items()):
                if req['user_id'] == user_id and req['status'] == 'waiting_user_quality':
                    group_id = self.selfbot_manager.group_chat_id
                    import base64
                    data_b64 = base64.b64encode(data).decode()
                    await self.client.send_message(
                        group_id,
                        f'CLICK|{uuid}|{data_b64}'
                    )
                    req['status'] = 'waiting_file'
                    req['selected_quality'] = data
                    await event.edit("در حال دانلود... لطفا صبر کنید.")
                    # --- Await the file from the group (future-based) ---
                    try:
                        future = self.selfbot_manager.get_or_create_future(uuid, bot_username=self.bot_username)
                        message_ids = await asyncio.wait_for(future, timeout=DOWNLOAD_TIMEOUT)
                        group_entity = await self.client.get_entity(self.selfbot_manager.group_chat_id)
                        # Only send the first (and only) file
                        message_id = message_ids[0] if isinstance(message_ids, (list, tuple)) and message_ids else message_ids
                        msg = await self.client.get_messages(group_entity, ids=message_id)
                        from telethon.tl.types import MessageMediaDocument
                        if isinstance(msg.media, MessageMediaDocument):
                            doc = msg.document
                            if hasattr(doc, 'mime_type') and (doc.mime_type.startswith('video/') or doc.mime_type.startswith('audio/')):
                                await self.client.send_file(
                                    req['event'].chat_id,
                                    msg.media,
                                    caption="Downloaded by🚀 @media_dlrobot"
                                )
                                # --- بعد از ارسال موفق فایل: آپدیت لیمیت ---
                                # حجم فایل را می‌خوانیم
                                file_size = getattr(msg.document, 'size', 0) if hasattr(msg, 'document') else 0
                                # دوباره لیمیت را می‌خوانیم (برای همزمانی)
                                limits = await self.user_db.get_limits(user_id, today, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)
                                remaining_bonus_count = limits['bonus_count']
                                remaining_bonus_size = limits['bonus_size']
                                remaining_daily_count = DEFAULT_DAILY_COUNT - limits['daily_count']
                                remaining_daily_size = DEFAULT_DAILY_SIZE - limits['daily_size']
                                # اول از بونس کم کن تا جایی که کافی باشد
                                use_bonus_count = min(1, remaining_bonus_count)
                                use_bonus_size = min(file_size, remaining_bonus_size)
                                if use_bonus_count > 0 and use_bonus_size >= file_size:
                                    await self.user_db.consume_bonus(user_id, use_bonus_count, file_size)
                                else:
                                    # اگر بونس کافی نبود، باقی‌مانده را از روزانه کم کن
                                    if use_bonus_count > 0 and use_bonus_size <= 0:
                                        
                                        await self.user_db.consume_bonus(user_id, use_bonus_count, 0)
                                        left_size = file_size
                                        
                                        
                                    elif use_bonus_size > 0 and use_bonus_count <= 0:
                                        await self.user_db.consume_bonus(user_id, use_bonus_count, 0)
                                        left_size = file_size-use_bonus_size
                                        
                                    else:
                                        left_size = file_size
                                    
                                    if left_size > 0 and use_bonus_count ==1:
                                        await self.user_db.update_limits(user_id, today, 0, left_size, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)

                                    else:
                                        await self.user_db.update_limits(user_id, today, 1, left_size, DEFAULT_DAILY_COUNT, DEFAULT_DAILY_SIZE)
                            
                            else:
                                logger.warning(f"[YTSelfbot] Skipping non-video/audio document for uuid={uuid}: {getattr(doc, 'mime_type', None)}")
                        else:
                            logger.warning(f"[YTSelfbot] Skipping non-document media for uuid={uuid}: {type(msg.media)}")
                        logger.info(f"[YTSelfbot] File sent to user {req['event'].chat_id} for uuid={uuid}")
                        req['status'] = 'done'
                    except asyncio.TimeoutError:
                        logger.error(f"[YTSelfbot] Timeout waiting for file for uuid={uuid}")
                        await self.client.send_message(req['event'].chat_id, "متاسفانه دانلود در زمان مناسب انجام نشد.")
                        req['status'] = 'timeout'
                    except Exception as e:
                        logger.error(f"[YTSelfbot] Error sending file to user {req['event'].chat_id} for uuid={uuid}: {e}")
                        await self.client.send_message(req['event'].chat_id, f"خطا در ارسال فایل: {e}")
                        req['status'] = 'error'
                    finally:
                        self.selfbot_manager.pop_future(uuid)
                        if uuid in self.pending_requests:
                            del self.pending_requests[uuid]
                        # Reset download status for this user
                        self.download_status[user_id] = 0
                        if config.active_youtube_downloads > 0:
                            config.active_youtube_downloads -= 1
                    break
