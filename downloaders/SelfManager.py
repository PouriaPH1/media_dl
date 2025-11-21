import os
import json
from telethon import TelegramClient, events
import asyncio
import logging
import tempfile

import config
"""
    مدیریت اکانت های سلف و تخصیص ان ها به درستی
    مدیریت صف اکانت ها و باز و بسته کردن اون ها
    شنود کردن برای درخواست اولیه ربات اصلی که شامل اطلاعات درخواست هست و اضافش میکنه به صف درخواست ها
    """

logger = logging.getLogger(__name__)


class SelfBotManager:
    MAX_CLIENT_BUSY_SECS = 300  # 5 دقیقه (بر حسب ثانیه)

    def __init__(self, sessions_dir, accounts_json, group_chat_id):
        self.sessions_dir = sessions_dir
        self.accounts_json = accounts_json
        self.group_chat_id = group_chat_id
        self.clients = []
        self.idx = 0
        self.phone_to_api = {}
        self.started = False
        self._lock = asyncio.Lock()  # قفل برای مدیریت همزمانی
        self.queue = asyncio.Queue()  # صف درخواست‌ها
        self.busy_clients = set()  # اکانت‌های درگیر
        self.pending_futures = {}  # uuid -> {'future': Future, 'files': list, 'timer': Handle, 'bot_username': str}
        self.uuid_to_message = {}  # uuid -> selfbot message with buttons
        self.button_callback_general = None
        self.button_callback_youtube = None
        self._busy_timers = {}  # client -> timer task جهت fail-safe release
        self._client_active_handlers = {}  # client -> list[(fn, ev_filter)] برای پاکسازی هندلرهای ثبت شده هر درخواست
        self.group_client = None  # کلاینتی که لیسنر گروه روی آن رجیستر است
        self._group_handler_fn = None  # رفرنس تابع group_handler برای remove
        self._group_ev = None  # فیلتر رویداد گروه برای remove
        
        
        
        
        
        
    def set_button_callback_general(self, callback):
        self.button_callback_general = callback

    def set_button_callback_youtube(self, callback):
        self.button_callback_youtube = callback

    def load_accounts(self):
        with open(self.accounts_json, 'r', encoding='utf-8') as f:
            self.phone_to_api = json.load(f)

    async def start_all(self):
        if self.started:
            return
        self.load_accounts()
        session_files = [f for f in os.listdir(self.sessions_dir) if f.endswith('.session')]
        for session_file in session_files:
            phone = os.path.splitext(session_file)[0]
            api_info = self.phone_to_api.get(phone)
            if not api_info:
                logger.warning(f"No API info for {phone}, skipping...")
                continue

            api_id = api_info["api_id"]
            api_hash = api_info["api_hash"]
            session_path = os.path.join(self.sessions_dir, session_file)
            client = TelegramClient(session_path, api_id, api_hash)
            await client.start()
            self.clients.append(client)
            logger.info(f"SelfBot started: {phone} (session: {session_path})")

        print(f"Started {len(self.clients)} self accounts.")
        self.started = True
        # راه‌اندازی نگهبان برای مانیتور وضعیت لیسنر گروه و کانکشن
        asyncio.create_task(self._group_watchdog())

    async def get_next_client(self):
        """انتخاب سلف‌بات بعدی به صورت راند رابین در صورت آزاد بودن یا صبر کردن برای آزاد شدن"""
        while True:
            async with self._lock:
                available_clients = [client for client in self.clients if client not in self.busy_clients]
                if available_clients:
                    # انتخاب به صورت چرخشی (راند رابین)
                    client = available_clients[self.idx % len(available_clients)]
                    self.idx = (self.idx + 1) % len(self.clients)
                    self.busy_clients.add(client)
                    logger.info(f"Selected selfbot for request: {client.session.filename}")
                    # Fail-safe timer: after max busy duration, force release
                    self._set_busy_timer(client)
                    return client
            # اگر هیچ کلاینت آزادی وجود نداشت، کمی صبر می‌کنیم و دوباره تلاش می‌کنیم
            await asyncio.sleep(0.3)

    async def release_client(self, client):
        """آزاد کردن سلف‌بات بعد از اتمام کار یا تایم‌اوت"""
        async with self._lock:
            if client in self.busy_clients:
                self.busy_clients.remove(client)
            # Cancel/cleanup any existing busy-timer for this client
            timer = self._busy_timers.pop(client, None)
            if timer:
                timer.cancel()
            # پاکسازی تمام هندلرهای رویدادی ثبت شده برای این کلاینت (درخواست جاری)
            self._cleanup_handlers_for_client(client)

    def _set_busy_timer(self, client):
        # اگر تایمر قبلی هست، کنسل کن (نشتی نشه)
        old_timer = self._busy_timers.pop(client, None)
        if old_timer:
            old_timer.cancel()
        # بساز تایمر جدید
        loop = asyncio.get_event_loop()
        timer_task = loop.create_task(self._force_release_after_timeout(client))
        self._busy_timers[client] = timer_task

    async def _force_release_after_timeout(self, client):
        try:
            await asyncio.sleep(self.MAX_CLIENT_BUSY_SECS)
            async with self._lock:
                if client in self.busy_clients:
                    logger.warning(f"[FAILSAFE-TIMER] Client {getattr(client.session, 'filename', client)} forcibly released after {self.MAX_CLIENT_BUSY_SECS} seconds!")
                    # قبل از آزادسازی، هندلرهای ثبت شده این کلاینت را حذف کن تا لیک نکند
                    self._cleanup_handlers_for_client(client)
                    self.busy_clients.remove(client)
            # Clean up timer entry
            self._busy_timers.pop(client, None)
        except asyncio.CancelledError:
            pass

    def _cleanup_handlers_for_client(self, client):
        handlers = self._client_active_handlers.pop(client, None)
        if handlers:
            for fn, ev in handlers:
                try:
                    client.remove_event_handler(fn, ev)
                except Exception as e:
                    logger.debug(f"[CLEANUP] remove_event_handler failed: {e}")









    def register_group_handler(self):
        if not self.clients:
            logger.error("No selfbot clients available to register group handler!")
            raise Exception("Clients are not started yet!")

        # اولین کلاینت را برای لیسنر گروه انتخاب و به صورت دستی رجیستر می‌کنیم تا قابلیت rebind داشته باشیم
        main_client = self.clients[0]
        logger.info(f"[SelfBotManager] Registering group handler for group {self.group_chat_id} on main_client {getattr(main_client.session, 'filename', main_client)}")

        async def group_handler(event):
            logger.info(f"[SelfBotManager] group_handler triggered! event.text={event.text}")
            try:
                # Handle click messages from main bot
                if event.text and event.text.startswith('CLICK|'):
                    import base64
                    parts = event.text.split('|', 2)
                    if len(parts) == 3:
                        uuid, data_b64 = parts[1], parts[2]
                        data = base64.b64decode(data_b64)
                        msg = self.uuid_to_message.get(uuid)
                        if msg:
                            logger.info(f"[SelfBotManager] Performing click for uuid={uuid}")
                            await msg.click(data=data)
                        else:
                            logger.warning(f"[SelfBotManager] No message found for uuid={uuid} to click.")
                    return
                # Old logic for new download requests
                parts = event.text.strip().split('|')
                if len(parts) >= 3:
                    user_id = parts[0]
                    request_uuid = parts[1]
                    url = '|'.join(parts[2:])
                    # اگر bot_username از pending_futures موجود است، آن را اضافه کن
                    entry = self.pending_futures.get(request_uuid)
                    bot_username = entry['bot_username'] if entry and 'bot_username' in entry else None
                    media_filter = entry.get('media_filter') if entry else None

                    # شناسایی درخواست‌های شازم از روی پیشوند uuid و ست‌کردن bot و فیلتر
                    try:
                        from config import shazam_route_bot_username, SHAZAM_ROUTE_MEDIA_FILTER
                    except Exception:
                        shazam_route_bot_username, SHAZAM_ROUTE_MEDIA_FILTER = (None, None)
                    if isinstance(request_uuid, str) and request_uuid.lower().startswith('shazam-'):
                        if shazam_route_bot_username:
                            # ایجاد/به‌روزرسانی entry برای استفاده در process_queue
                            pend = self.pending_futures.setdefault(request_uuid, {
                                'future': asyncio.get_event_loop().create_future(),
                                'files': [],
                                'timer': None
                            })
                            pend['bot_username'] = shazam_route_bot_username
                            if SHAZAM_ROUTE_MEDIA_FILTER:
                                pend['media_filter'] = SHAZAM_ROUTE_MEDIA_FILTER
                            else:
                                pend['media_filter'] = 'audio_only'
                            bot_username = shazam_route_bot_username
                            media_filter = pend['media_filter']
                    await self.queue.put((user_id, request_uuid, url, bot_username))
                    asyncio.create_task(self.process_queue())
            except Exception as e:
                logger.error(f'Error in group_handler: {e}')

        # ذخیره رفرنس‌ها برای امکان حذف و rebind
        self._group_handler_fn = group_handler
        self._group_ev = events.NewMessage(chats=self.group_chat_id)
        self.group_client = main_client
        self.group_client.add_event_handler(self._group_handler_fn, self._group_ev)

    async def _group_watchdog(self):
        # به صورت دوره‌ای بررسی می‌کند لیسنر گروه روی یک کلاینت متصل رجیستر باشد
        while True:
            try:
                if self.started and self.clients:
                    client = self.group_client if self.group_client else (self.clients[0] if self.clients else None)
                    needs_rebind = False
                    if client is None:
                        needs_rebind = True
                    else:
                        try:
                            is_connected = client.is_connected() if hasattr(client, 'is_connected') else True
                        except Exception:
                            is_connected = False
                        if not is_connected:
                            needs_rebind = True

                    if needs_rebind:
                        # انتخاب یک کلاینت متصل دیگر
                        new_client = None
                        for c in self.clients:
                            try:
                                if hasattr(c, 'is_connected'):
                                    if c.is_connected():
                                        new_client = c
                                        break
                                else:
                                    new_client = c
                                    break
                            except Exception:
                                continue

                        if new_client:
                            # حذف لیسنر قبلی اگر وجود دارد
                            if self.group_client and self._group_handler_fn and self._group_ev:
                                try:
                                    self.group_client.remove_event_handler(self._group_handler_fn, self._group_ev)
                                except Exception:
                                    pass
                            # رجیستر روی کلاینت جدید
                            self.group_client = new_client
                            if self._group_handler_fn and self._group_ev:
                                self.group_client.add_event_handler(self._group_handler_fn, self._group_ev)
                            logger.info(f"[WATCHDOG] Group handler rebound to {getattr(self.group_client.session, 'filename', self.group_client)}")
            except Exception as e:
                logger.debug(f"[WATCHDOG] error: {e}")
            await asyncio.sleep(5)

    async def process_queue(self):
        """مدیریت اجرای درخواست‌ها از صف"""
        while not self.queue.empty():
            queue_item = await self.queue.get()
            if len(queue_item) == 4:
                user_id, request_uuid, url, bot_username = queue_item
            else:
                # backward compatibility
                user_id, request_uuid, url = queue_item
                bot_username = None
            logger.info(f"[QUEUE] Got request: user_id={user_id}, uuid={request_uuid}, url={url}, bot_username={bot_username}")

            had_exception = False
            client = None
            try:
                client = await self.get_next_client()
                # اگر bot_username از صف نیامده، از pending_futures بخوانیم
                if not bot_username:
                    entry = self.pending_futures.get(request_uuid)
                    bot_username = entry['bot_username'] if entry and 'bot_username' in entry else None
                if not bot_username:
                    logger.error(f"No bot_username found for request_uuid={request_uuid}")
                    await self.release_client(client)
                    continue
                logger.info(f"[SEND] Selfbot {client.session.filename} sending url to bot: {url} (bot_username={bot_username})")

                # ارسال لینک به ربات دانلودر
                bot_msg = await client.send_message(bot_username, url)
                logger.info(f"[SEND] Selfbot {client.session.filename} sent url to bot {bot_username}. Waiting for file...")

                # Set برای track کردن message IDs که هندل شده‌اند (جلوگیری از duplicate handling)
                handled_message_ids = set()

                # هندلر دکمه‌ها و media (برای پیام‌هایی که هم text و هم media دارند)
                async def bot_button_handler(bot_event):
                    try:
                        logger.info(f"[DEBUG] bot_event: text={bot_event.text}, buttons={bot_event.buttons}, media={bot_event.media}")
                        
                        # اول چک می‌کنیم: اگر buttons وجود دارد، باید دکمه‌ها را هندل کنیم (حتی اگر media هم وجود داشته باشد)
                        # این برای ربات‌هایی مثل یوتیوب است که دکمه‌های کیفیت به همراه thumbnail ارسال می‌کنند
                        if bot_event.buttons:
                            msg_id = bot_event.id
                            # اگر قبلاً هندل شده، skip می‌کنیم
                            if msg_id in handled_message_ids:
                                logger.info(f"[BUTTON_HANDLER] Message {msg_id} with buttons already handled, skipping")
                                return
                            # اضافه کردن message ID به set تا اگر thumbnail بعداً trigger شود، skip شود
                            handled_message_ids.add(msg_id)
                            
                            logger.info(f"[RECEIVE] Selfbot {client.session.filename} received buttons for uuid={request_uuid}, user_id={user_id}")
                            # Store the message for later click
                            self.uuid_to_message[request_uuid] = bot_event
                            try:
                                if hasattr(self, 'button_callback_general') and self.button_callback_general:
                                    logger.info(f"[DEBUG] Calling button_callback_general for uuid={request_uuid}")
                                    await self.button_callback_general(request_uuid, bot_event.buttons, bot_event.text)
                                if hasattr(self, 'button_callback_youtube') and self.button_callback_youtube:
                                    logger.info(f"[DEBUG] Calling button_callback_youtube for uuid={request_uuid}")
                                    await self.button_callback_youtube(request_uuid, bot_event.buttons, bot_event.text)
                            except Exception as e:
                                logger.error(f"[BUTTON_CALLBACK] Error: {e}")
                            # بعد از هندل کردن دکمه‌ها، هندلر را حذف می‌کنیم
                            # اگر media (thumbnail) هم وجود دارد و بعداً trigger شود، از handled_message_ids skip می‌شود
                            try:
                                client.remove_event_handler(bot_button_handler, button_ev)
                            except Exception:
                                pass
                            return
                        
                        # اگر buttons وجود ندارد اما media وجود دارد، آن را به bot_file_handler بسپاریم
                        if bot_event.media:
                            msg_id = bot_event.id
                            if msg_id in handled_message_ids:
                                logger.info(f"[BUTTON_HANDLER] Message {msg_id} already handled, skipping delegation")
                                return
                            # چک می‌کنیم که آیا قبلاً هندل شده یا نه، اما اضافه کردن message ID را به bot_file_handler می‌سپاریم
                            logger.info(f"[BUTTON_HANDLER] Message has media (no buttons), delegating to file_handler for uuid={request_uuid}, msg_id={msg_id}")
                            # تریگر کردن bot_file_handler به صورت دستی
                            asyncio.create_task(bot_file_handler(bot_event))
                            # حذف هندلر دکمه چون media handler مسئولیت را به عهده می‌گیرد
                            try:
                                client.remove_event_handler(bot_button_handler, button_ev)
                            except Exception:
                                pass
                            return
                    except Exception as e:
                        logger.error(f"[BUTTON_HANDLER] Exception: {e}")
                    finally:
                        # اگر هیچ buttons یا media وجود نداشت، هندلر را حذف می‌کنیم
                        if not getattr(bot_event, 'buttons', None) and not getattr(bot_event, 'media', None):
                            try:
                                client.remove_event_handler(bot_button_handler, button_ev)
                            except Exception:
                                pass

                # هندلر فایل (media)
                async def bot_file_handler(bot_event):
                    try:
                        if bot_event.media:
                            # چک duplicate handling
                            msg_id = bot_event.id
                            if msg_id in handled_message_ids:
                                logger.info(f"[FILE_HANDLER] Message {msg_id} already handled, skipping")
                                return
                            handled_message_ids.add(msg_id)
                            
                            # --- فیلتر نوع فایل بر اساس bot_username و media_filter سفارشی ---
                            entry = self.pending_futures.get(request_uuid)
                            bot_username_check = entry['bot_username'] if entry and 'bot_username' in entry else None
                            media_filter = entry.get('media_filter') if entry else None
                            
                            
                            # اگر ربات یوتیوب  بود فقط ویدیو و صوت مجاز است
                            ytb = getattr(config, 'youtube_selfbot_bot_username', '')
                            if bot_username_check and bot_username_check.lower() in [ytb, ytb.replace("@", "")]:
                                # چک document (برای ویدیو/صوت)
                                doc = getattr(bot_event, 'document', None)
                                mime_type = getattr(doc, 'mime_type', None) if doc else None
                                
                                # چک photo (thumbnail) - برای یوتیوب باید skip شود
                                photo = getattr(bot_event, 'photo', None)
                                
                                # فقط اگر ویدیو یا صوت بود ادامه بده (نه عکس/thumbnail)
                                if photo:
                                    logger.info(f"[FILTER] Skipping photo/thumbnail for YouTube bot")
                                    # حذف message ID از set چون skip کردیم و ادامه انتظار برای پیام بعدی (مثلاً صوت/ویدیو)
                                    handled_message_ids.discard(msg_id)
                                    return
                                
                                if not (mime_type and (mime_type.startswith('video/') or mime_type.startswith('audio/'))):
                                    logger.info(f"[FILTER] Skipping non-video/audio file for YouTube bot: {mime_type}")
                                    # حذف message ID از set چون skip کردیم و ادامه انتظار
                                    handled_message_ids.discard(msg_id)
                                    return
                            # اگر فیلتر سفارشی audio_only تنظیم شده باشد، فقط صوت را بپذیر
                            if media_filter == 'audio_only':
                                doc = getattr(bot_event, 'document', None)
                                mime_type = getattr(doc, 'mime_type', None) if doc else None
                                is_voice = bool(getattr(bot_event, 'voice', None))
                                has_audio_doc = bool(doc and mime_type and mime_type.startswith('audio/'))
                                if is_voice or has_audio_doc:
                                    pass  # اجازه ادامه
                                else:
                                    # اگر صرفا photo یا هر مدیای دیگر بود، نادیده بگیر
                                    if getattr(bot_event, 'photo', None):
                                        logger.info("[FILTER] Skipping photo due to audio_only filter")
                                    else:
                                        logger.info(f"[FILTER] Skipping non-audio due to audio_only filter: {mime_type}")
                                    handled_message_ids.discard(msg_id)
                                    return

                            # برای سایر حالات همه نوع فایل مجاز است
                            logger.info(f"[RECEIVE] Selfbot {client.session.filename} received file for uuid={request_uuid}, user_id={user_id}, msg_id={msg_id}")
                            try:
                                group_entity = await client.get_entity(self.group_chat_id)
                                sent_msg = await client.send_file(
                                    group_entity,
                                    bot_event.media,
                                    caption=f"{user_id}|{request_uuid}",
                                    force_document=False if getattr(bot_event, 'video', None) or getattr(bot_event, 'photo', None) else True
                                )
                                logger.info(f"[GROUP] Selfbot {client.session.filename} sent media to group {self.group_chat_id} for uuid={request_uuid}, user_id={user_id}")
                                entry = self.pending_futures.setdefault(request_uuid, {'future': asyncio.get_event_loop().create_future(), 'files': [], 'timer': None, 'bot_username': bot_username, 'media_filter': media_filter})
                                entry['files'].append(sent_msg.id)

                                # اگر تایمر قبلی هست کنسل کن
                                if entry.get('timer'):
                                    entry['timer'].cancel()

                                # تایمر جدید برای جمع‌آوری همه فایل‌ها
                                async def finish_files():
                                    await asyncio.sleep(2)  # اگر ۲ ثانیه فایل جدید نیامد، همه را ارسال کن
                                    if not entry['future'].done():
                                        entry['future'].set_result(entry['files'])
                                    # حذف future و cleanup
                                    self.pending_futures.pop(request_uuid, None)

                                entry['timer'] = asyncio.create_task(finish_files())

                                try:
                                    client.remove_event_handler(bot_file_handler, file_ev)
                                except Exception:
                                    pass
                            except Exception as e:
                                logger.error(f"[SEND_FILE] Error downloading or sending file: {e}")
                            await self.release_client(client)
                            logger.info(f"[RELEASE] Selfbot {client.session.filename} released for new requests.")
                    except Exception as e:
                        logger.error(f"[FILE_HANDLER] Exception: {e}")
                        # در صورت بروز خطا، پاکسازی و آزادسازی برای جلوگیری از گیر کردن
                        try:
                            client.remove_event_handler(bot_file_handler, file_ev)
                        except Exception:
                            pass
                        await self.release_client(client)

                # ثبت هندلرها
                button_ev = events.NewMessage(from_users=bot_username)
                file_ev = events.NewMessage(from_users=bot_username)
                client.add_event_handler(bot_button_handler, button_ev)
                client.add_event_handler(bot_file_handler, file_ev)
                # برای پاکسازی در زمان release/timeout نگه‌داری رفرنس هندلرها
                handlers = self._client_active_handlers.setdefault(client, [])
                handlers.append((bot_button_handler, button_ev))
                handlers.append((bot_file_handler, file_ev))

            except Exception as e:
                had_exception = True
                logger.error(f"Error in process_queue: {e}")
            finally:
                if had_exception and client is not None:
                    # حذف هندلرهای ثبت‌شده و آزادسازی کلاینت در صورت وقوع خطا
                    self._cleanup_handlers_for_client(client)
                    await self.release_client(client)
                    try:
                        logger.info(f"[RELEASE] Selfbot {client.session.filename} released after error.")
                    except Exception:
                        pass

    def get_or_create_future(self, uuid, bot_username, media_filter=None):
        entry = self.pending_futures.setdefault(uuid, {'future': asyncio.get_event_loop().create_future(), 'files': [], 'timer': None, 'bot_username': bot_username, 'media_filter': media_filter})
        return entry['future']

    def pop_future(self, uuid):
        entry = self.pending_futures.pop(uuid, None)
        if entry and entry['timer']:
            entry['timer'].cancel()
        return entry['future'] if entry else None
