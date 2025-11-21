import asyncio
import logging
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError, PhoneCodeInvalidError, FloodWaitError, MessageNotModifiedError

# Import configuration from config.py
from config import  allowed_domains, API_ID, API_HASH, BOT_TOKEN, ADMIN_BOT_TOKEN, CHANNELS,ADMIN_IDS,sessions_dir,accounts_json,group_chat_id,generic_patterns,interval_hours, GENERIC_PATTERNS_FILE, load_generic_patterns, youtube_selfbot_bot_username
from downloaders.user_db import UserDB
from downloaders.SelfManager import SelfBotManager
from downloaders.instagram_downloader import InstagramDownloader
from downloaders.radiojavan_downloader import RadioJavanDownloader
from downloaders.simple_downloader import SimpleDownloader
from downloaders.shazam_downloader import ShazamDownloader


from downloaders.general_downloader import GenericSelfbotDownloader
import math
import os
import time
import aiohttp
from telethon import Button
import re

payment_receipt_waiting = {}

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def backup_databases_periodically(client, admin_id, interval_hours=12):
    """
    هر interval_hours ساعت یکبار دیتابیس‌ها را به ادمین ارسال می‌کند.
    """
    db_files = ["user_db.sqlite3", "video_cache.db"]
    while True:
        for db_file in db_files:
            if os.path.exists(db_file):
                try:
                    await client.send_file(admin_id, db_file, caption=f"📦 بکاپ خودکار فایل: {db_file}")
                except Exception as e:
                    logger.error(f"Failed to send backup {db_file} to admin: {e}")
            else:
                logger.warning(f"Backup file not found: {db_file}")
        await asyncio.sleep(interval_hours * 60 * 60)

async def join_checker(user_id: int, channels, admin_bot_token) -> list:
    not_joined_channels = []
    async with aiohttp.ClientSession() as session:
        for channel in channels:
            try:
                channel_username = channel.lstrip('@')
                url = f"https://api.telegram.org/bot{admin_bot_token}/getChatMember?chat_id=@{channel_username}&user_id={user_id}"
                async with session.get(url, timeout=10, ssl=True) as response:
                    if response.status == 200:
                        data = await response.json()
                        status = data['result']['status']
                        if status not in ["member", "administrator", "creator"]:
                            not_joined_channels.append(channel)
                    else:
                        not_joined_channels.append(channel)
                        logger.warning(f"Failed to check membership for channel {channel}: {response.status}")
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

async def main():
    # Initialize user database
    try:
        user_db = UserDB()
        await user_db._create_table()
    except Exception as e:
        logger.error(f"Failed to initialize user database: {e}")
        raise

    # Broadcast settings
    BATCH_SIZE = 30  # Number of messages to send in each batch
    BATCH_DELAY = 1  # Delay between batches in seconds

    # Initialize the Telegram client
    try:
        client = TelegramClient('bot_session', API_ID, API_HASH)
    except Exception as e:
        logger.error(f"Failed to initialize Telegram client: {e}")
        raise

    try:
        # Start the client
        await client.start(bot_token=BOT_TOKEN)
        logger.info("Bot started successfully!")

        # Start periodic backup task
        asyncio.create_task(backup_databases_periodically(client, ADMIN_IDS[0], interval_hours=interval_hours))

        youtube_downloader = None

       
        # Register /start and /help command handlers
        
        
        # فرض: register_youtube در ابتدای main.py ایمپورت و اینشیالایز شده و instance آن (مثلاً youtube_downloader) در دسترس است
        # اگر نیست، باید instance آن را global یا به صورت مناسب در دسترس قرار دهی
        @client.on(events.CallbackQuery(pattern=b"check_membership_referral"))
        async def check_membership_referral_handler(event):
            try:
                user_id = event.sender_id
                from config import CHANNELS, ADMIN_BOT_TOKEN
                not_joined_channels = await join_checker(user_id, CHANNELS, ADMIN_BOT_TOKEN)
                if not_joined_channels:
                    await event.answer("هنوز در همه چنل‌ها عضو نشدی!", alert=True)
                    return
                # بررسی رفرال معلق
                referrer_id = await user_db.get_pending_referrer(user_id)
                if referrer_id:
                    await user_db.complete_referral_and_give_bonus(referrer_id, user_id)
                    await event.respond("🎉 تبریک! شما و معرف هر دو جایزه دریافت کردید.")
                    try:
                        await client.send_message(referrer_id, f"🎉 کاربر {user_id} با دعوت شما عضو شد و هر دو جایزه گرفتید!")
                    except Exception as e:
                        logger.warning(f"Failed to notify referrer after completion: {e}")
                else:
                    await event.respond("رفرال معتبر پیدا نشد یا قبلاً جایزه داده شده است.")
            except Exception as e:
                logger.error(f"Error in check_membership_referral_handler: {e}")
                try:
                    await event.respond("خطا در بررسی عضویت یا جایزه. لطفاً دوباره تلاش کنید.")
                except:
                    pass
        
        @client.on(events.NewMessage(pattern=r"/start(.*)"))
        async def start_handler(event):
            try:
                user_id = event.sender_id
                sender = await event.get_sender()
                username = getattr(sender, 'username', None)
                import re
                m = re.match(r"/start ref_([a-fA-F0-9]{32,})", event.raw_text)
                is_referral = bool(m)
                referrer_token = m.group(1) if m else None
                referrer_id = None
                if is_referral and referrer_token:
                    referrer_id = await user_db.get_user_id_by_referral_token(referrer_token)
                # بررسی وجود کاربر در جدول users
                user_record = await user_db.get_user(user_id)
                if is_referral and referrer_id and user_id != referrer_id:
                    if user_record is None:
                        # کاربر جدید است، ثبت در جدول users و ثبت رفرال
                        await user_db.add_or_update_user(user_id, username)
                        try:
                            await client.send_message(referrer_id, f"یک کاربر با لینک دعوت شما وارد ربات شد. برای دریافت جایزه، باید در چنل‌ها عضو شود و روی دکمه 'عضو شدم' بزند.")
                        except Exception as e:
                            logger.warning(f"Failed to notify referrer: {e}")
                        # پیام به کاربر جدید با دکمه‌های چنل و عضو شدم✅
                        from telethon import Button
                        from config import CHANNELS
                        channel_buttons = [[Button.url(channel[1:], f"https://t.me/{channel[1:]}")] for channel in CHANNELS]
                        channel_buttons.append([Button.inline("عضو شدم✅", b"check_membership_referral")])
                        await event.reply(
                            "<b>برای دریافت جایزه دعوت، حتماً باید در چنل‌های زیر عضو شوی و سپس روی دکمه <u>عضو شدم✅</u> بزنی.</b>",
                            buttons=channel_buttons,
                            parse_mode="html"
                        )
                        # ثبت رفرال معلق
                        await user_db.add_pending_referral(referrer_id, user_id)
                        # نمایش پنل کاربری بعد از پیام عضویت
                        await user_panel_handler(event)
                        return
                    else:
                        # کاربر قبلاً عضو بوده است
                        await event.reply("شما قبلاً عضو ربات بودید و امکان دریافت جایزه رفرال ندارید.")
                        await user_panel_handler(event)
                        return
                # اگر رفرال نبود یا کاربر جدید نبود، ثبت یا آپدیت معمولی
                await user_db.add_or_update_user(user_id, username)
                start_text = (
    "سلام! 👋 به <b>مدیا دانلودر</b> خوش آمدید. 🎉\n\n"
    "این ربات از پلتفرم‌های مختلفی پشتیبانی می‌کند و به شما امکان دانلود 🎬 ویدیو، 🎵 موزیک و 🎙️ پادکست را می‌دهد.\n\n"
    "<b>پلتفرم‌های پشتیبانی‌شده:</b>\n"
    "📺 یوتیوب: دانلود ویدیو و صوت  و پلی‌لیست و زیر نویس \n"
    "تلگرام:دانلود استوری افراد و فایل از چنل هایی که قابلیت فوروارد و کپی رو بستن \n"
    "📸 اینستاگرام: دانلود پست، استوری و IGTV\n"
    "📸 دانلود ویدیو و عکس از تردز\n"
    "🎧 اسپاتیفای: دانلود موزیک و پلی‌لیست\n"
    "📌 پینترست: دانلود تصاویر و ویدیوها\n"
    "📘 فیسبوک: دانلود ویدیو\n"
    "🎙️ کست‌باکس: دانلود پادکست\n"
    "☁️ ساندکلاد: دانلود موزیک\n"
    "🐦 توییتر: دانلود ویدیو و تصاویر\n"
    "🎵 تیک‌تاک: دانلود ویدیو\n"
    "👻 اسنپ‌چت: دانلود ویدیوهای اسنپ‌چت با لینک مستقیم\n"
    "🎶 رادیو جوان: دانلود موزیک   (لینک کوتاه )\n\n"
    "📥 تشخیص و دانلود آهنگ با Shazam : فایل صوتی یا ویدیویی که بخشی از آهنگ داخلش هست رو برای ربات ارسال کنید. ربات آهنگ رو تشخیص میده و دانلود می‌کنه."
    
                )
                await event.reply(start_text, parse_mode="html")
                # نمایش پنل کاربری بعد از پیام خوش‌آمدگویی
                await user_panel_handler(event)
            except Exception as e:
                logger.error(f"Error in start handler: {e}")
                try:
                    await event.reply("متأسفانه خطایی رخ داد. لطفاً دوباره تلاش کنید.")
                except:
                    pass

        # Admin command to broadcast message to all users
        @client.on(events.NewMessage(pattern=r"/broadcast"))
        async def broadcast_handler(event):
            try:
                if event.sender_id not in ADMIN_IDS:
                    await event.reply("You are not authorized to use this command.")
                    return

                # Get the message to broadcast
                message = event.raw_text.replace('/broadcast', '').strip()
                if not message:
                    await event.reply("Please provide a message to broadcast.\nExample: /broadcast Hello everyone!")
                    return

                # Get all users
                try:
                    users = await user_db.get_all_users()
                except Exception as e:
                    logger.error(f"Failed to get users from database: {e}")
                    await event.reply("Error accessing user database. Please try again later.")
                    return

                if not users:
                    await event.reply("No users in database to broadcast to.")
                    return

                # Calculate total time needed
                total_users = len(users)
                total_batches = math.ceil(total_users / BATCH_SIZE)
                estimated_time = total_batches * BATCH_DELAY

                # Send message to all users
                success_count = 0
                fail_count = 0
                try:
                    status_message = await event.reply(
                        f"Starting broadcast to {total_users} users...\n"
                        f"Estimated time: {estimated_time} seconds"
                    )
                except Exception as e:
                    logger.error(f"Failed to send initial status message: {e}")
                    return

                # Process users in batches
                for i in range(0, total_users, BATCH_SIZE):
                    try:
                        batch = users[i:i + BATCH_SIZE]
                        batch_tasks = []
                        
                        # Create tasks for current batch
                        for user in batch:
                            task = asyncio.create_task(
                                client.send_message(user['user_id'], message)
                            )
                            batch_tasks.append((user['user_id'], task))

                        # Wait for all tasks in batch to complete
                        for user_id, task in batch_tasks:
                            try:
                                await task
                                success_count += 1
                            except FloodWaitError as e:
                                logger.warning(f"Rate limit hit, waiting {e.seconds} seconds")
                                await asyncio.sleep(e.seconds)
                                fail_count += 1
                            except Exception as e:
                                logger.error(f"Failed to send message to user {user_id}: {e}")
                                fail_count += 1

                        # Update progress
                        progress = min(100, (i + len(batch)) / total_users * 100)
                        try:
                            await status_message.edit(
                                f"Broadcasting in progress...\n"
                                f"Progress: {progress:.1f}%\n"
                                f"✅ Successfully sent: {success_count}\n"
                                f"❌ Failed: {fail_count}\n"
                                f"📝 Remaining users: {total_users - (i + len(batch))}"
                            )
                        except Exception as e:
                            logger.error(f"Failed to update status message: {e}")

                        # Wait before next batch
                        if i + BATCH_SIZE < total_users:
                            await asyncio.sleep(BATCH_DELAY)

                    except Exception as e:
                        logger.error(f"Error processing batch {i}: {e}")
                        continue

                # Final status update
                try:
                    await status_message.edit(
                        f"Broadcast completed!\n"
                        f"✅ Successfully sent: {success_count}\n"
                        f"❌ Failed: {fail_count}\n"
                        f"📝 Total users: {total_users}"
                    )
                except Exception as e:
                    logger.error(f"Failed to send final status message: {e}")

            except Exception as e:
                logger.error(f"Error in broadcast handler: {e}")
                try:
                    await event.reply("An error occurred during broadcast. Please try again later.")
                except:
                    pass

        @client.on(events.NewMessage(pattern=r"/usercount"))
        async def usercount_handler(event):
            try:
                if event.sender_id not in ADMIN_IDS:
                    await event.reply("You are not authorized to use this command.")
                    return
                try:
                    users = await user_db.get_all_users()
                    count = len(users)
                except Exception as e:
                    logger.error(f"Failed to get users from database: {e}")
                    await event.reply("Error accessing user database. Please try again later.")
                    return
                await event.reply(f"Total users in database: {count}")
            except Exception as e:
                logger.error(f"Error in usercount handler: {e}")
                try:
                    await event.reply("An error occurred while counting users. Please try again later.")
                except:
                    pass

        

        @client.on(events.NewMessage(pattern=r"/panel"))
        async def user_panel_handler(event):
            panel_text = (
                "<b>دستورات پنل کاربری:</b>\n"
                "/account - مشاهده اطلاعات حساب\n"
                "/referral_bonus - دریافت لینک رفرال و جایزه\n"
                "/referrals - نمایش زیرمجموعه‌ها\n"
                "/platforms - پلتفرم‌های پشتیبانی‌شده\n"
                "/plans - مقایسه پلن‌ها\n"
                "/guide - راهنمای استفاده از ربات\n"
                "\n"
            )
            buttons = [
                [Button.text('📝 دانلود زیرنویس یوتیوب', resize=True)],
                [Button.text('💳 خرید اشتراک', resize=True)]
            ]
            await event.respond(panel_text, buttons=buttons, parse_mode="html")

        @client.on(events.NewMessage(pattern=r"^/account(?:@\w+)?$"))
        async def account_info_command(event):
            user_id = event.sender_id
            import datetime
            today = datetime.date.today().strftime('%Y-%m-%d')
            limits = await user_db.get_limits(user_id, today, 10, 1073741824)
            is_vip = limits.get('is_vip') == 1 and limits.get('vip_expiry')
            vip_expiry = limits.get('vip_expiry')
            days_left = None
            if is_vip and vip_expiry:
                expiry_date = datetime.datetime.strptime(vip_expiry, '%Y-%m-%d')
                now = datetime.datetime.now()
                days_left = (expiry_date - now).days
                if days_left < 0:
                    days_left = 0
            if is_vip:
                account_type = 'ویژه'
                plan = 'ویژه (VIP)'
                max_count = 50
                max_size = 20 * 1024 * 1024 * 1024
                max_size_str = '20 گیگابایت'
            else:
                from config import DAILY_COUNT_LIMIT, DAILY_SIZE_LIMIT
                account_type = 'عادی'
                plan = 'عادی'
                max_count = DAILY_COUNT_LIMIT
                max_size = DAILY_SIZE_LIMIT
                max_size_str = f"{int(max_size/(1024*1024*1024))} گیگابایت" if max_size >= 1024*1024*1024 else f"{int(max_size/(1024*1024))} مگابایت"
            count = limits['daily_count']
            size = limits['daily_size']
            bonus_count = limits['bonus_count']
            bonus_size = limits['bonus_size']
            size_mb = size / (1024*1024)
            bonus_size_mb = bonus_size / (1024*1024)
            referral_count = await user_db.get_successful_referral_count(user_id)
            msg = (
                f"<b>💎 نوع حساب:</b> {account_type}\n"
                f"<b>📦 پلن:</b> {plan}\n"
                f"<b>🔢 تعداد دانلود امروز:</b> {max_count} /{count} \n"
                f"<b>💾 حجم دانلود امروز:</b> {size_mb:.2f} MB\n"
                f"<b>💾 سقف دانلود امروز</b> {max_size_str}\n"
                f"<b>👥 تعداد دعوت موفق:</b> {referral_count}\n"
                f"<b>🎁 دانلود جایزه باقی‌مانده:</b> {bonus_count}\n"
                f"<b>🎁 حجم جایزه باقی‌مانده:</b> {bonus_size_mb:.2f} MB\n"
            )
            if is_vip and vip_expiry:
                msg += f"<b>⏳ روز باقی‌مانده از اشتراک:</b> {days_left} روز\n"
            msg += (
                "-----------------------------\n"
                "(سهم عادی هر روز ریست می‌شود. جایزه‌ها تا مصرف کامل باقی می‌مانند.)"
            )
            
            
            await event.respond(msg, parse_mode="html")

        @client.on(events.NewMessage(pattern=r"^/referral_bonus(?:@\w+)?$"))
        async def referral_bonus_command(event):
            user_id = event.sender_id
            bot_username = (await client.get_me()).username
            referral_token = await user_db.get_or_create_referral_token(user_id)
            referral_link = f"https://t.me/{bot_username}?start=ref_{referral_token}"
            banner = (
          "🎉 <b>سیستم دعوت دوستان و دریافت جایزه</b> 🎉\n"
                "-----------------------------\n"
          "به ازای دعوت موفق هر فرد به ربات مدیا دانلودر ، 3 گیگ به حجم  و 5 تا به تعداد دانلود روزانه شما اضافه  خواهد شد (محدودیت دانلود فقط روی یوتیوب و اسپاتیفای ست شده و با این جایزه ها میتوانید محدودیت دانلود خود را کمتر کنید.)"

                "<b>شرایط دریافت جایزه:</b>\n"
                "1️⃣ دوست شما باید با لینک دعوت اختصاصی زیر ربات را استارت کند.\n"
                "2️⃣ حتماً در چنل‌های ربات عضو شود.\n"
                "3️⃣ پس از عضویت، دکمه <b>عضو شدم✅</b> را بزند.\n"
                "4️⃣ پس از تایید عضویت، هم شما و هم دوستتان جایزه دانلود دریافت می‌کنید!\n\n"
                "<b>لینک دعوت اختصاصی شما:</b>\n"
                f"<code>{referral_link}</code>\n\n"
                "هرچه بیشتر دعوت کنید، بیشتر جایزه بگیرید!\n"
                "-----------------------------\n"
                "برای مشاهده وضعیت جایزه‌ها و زیرمجموعه‌ها، از پنل کاربری استفاده کنید."
            )
            await event.respond(banner, parse_mode="html")

        @client.on(events.NewMessage(pattern=r"^/referrals(?:@\w+)?$"))
        async def referrals_list_command(event):
            user_id = event.sender_id
            rows = await user_db.get_successful_referrals(user_id)
            if rows:
                lines = []
                for row in rows:
                    referred_id, date = row
                    user_info = await user_db.get_user(referred_id)
                    if user_info and user_info.get('username'):
                        display = f"@{user_info['username']}"
                    else:
                        display = 'بدون نام کاربری'
                    lines.append(f"- {display}")
                msg = "👥 زیرمجموعه‌های شما:\n" + "\n".join(lines)
            else:
                msg = "شما هیچ زیرمجموعه‌ای ندارید. با لینک رفرال دوستان خود را دعوت کنید!"
            await event.respond(msg, parse_mode="html")

        @client.on(events.NewMessage(pattern=r"^/platforms(?:@\w+)?$"))
        async def platforms_command(event):
            platforms_text = (
                "<b>🧩 پلتفرم‌های پشتیبانی‌شده و قابلیت های ربات</b>\n"
                "-----------------------------\n"
                "<b>📱 پلتفرم‌های پشتیبانی‌شده:</b>\n"
                "📺 <b>یوتیوب (YouTube)</b>: دانلود ویدیو ، زیرنویس ویدیو ها،  صوت، پلی‌لیست با انتخاب کیفیت.\n"
                "📸 <b>اینستاگرام (Instagram)</b>: پست، استوری، ریلز، مولتی‌پست.\n"
                "🧵 <b>تردز (Threads)</b>: ویدیو .\n"
                "📌 <b>پینترست (Pinterest)</b>: تصاویر و ویدیوها.\n"
                "📘 <b>فیسبوک (Facebook)</b>: ویدیو.\n"
                "☁️ <b>ساندکلاد (SoundCloud)</b>: موزیک.\n"
                "🎧 <b>اسپاتیفای (Spotify)</b>: دانلود موزیک تکی، آلبوم و پلی‌لیست با تبدیل به MP3.\n"
                "🐦 <b>توییتر (Twitter)</b>: ویدیو \n"
                "🎵 <b>تیک‌تاک (TikTok)</b>: ویدیو.\n"
                "👻 <b>اسنپ‌چت (Snapchat)</b>:ویدیو\n"
                "🎶 <b>رادیو جوان (RadioJavan)</b>:موزیک\n"
                "-----------------------------\n"
                "<b>🎵 قابلیت‌های ویژه:</b>\n"
                "- تلگرام:  میتونین لینک پست‌های کانال ها و گروه های عمومی که محدودیت فوروارد دارن و استوری افراد ارسال کنید تا ربات فایل شون رو براتون بفرسته.\n"
                "- 🔍 <b>تشخیص موسیقی (Shazam)</b>: برای استفاده از این قابلیت، باید فایل صوتی یا ویدیویی که بخشی از آهنگ داخلش هست رو برای ربات ارسال کنید. ربات آهنگ رو تشخیص میده و دانلود می‌کنه.\n"
                
            )
            await event.respond(platforms_text, parse_mode="html")

        @client.on(events.NewMessage(pattern=r"^/plans(?:@\w+)?$"))
        async def plans_command(event):
            compare_text = (
                "<b>📊 مقایسه پلن رایگان و ویژه (VIP)</b>\n"
                "-----------------------------\n"
                "<b>پلن رایگان:</b>\n"
                "• سقف تعداد و حجم دانلود روزانه: 5 فایل و 1 گیگابایت (فقط برای یوتیوب و اسپاتیفای)\n"
                "• حداکثر حجم هر فایل: 500 مگابایت\n"
                "• نمایش تبلیغات\n"
                "• کیفیت و امکانات پایه\n"
                "• دانلود از سایر پلتفرم‌ها (اینستاگرام، فیسبوک، تیک‌تاک و...) بدون محدودیت\n"
                "-----------------------------\n"
                "<b>پلن ویژه (VIP):</b>\n"
                "• سقف تعداد و حجم دانلود روزانه: 50 فایل و 20 گیگابایت (فقط برای یوتیوب و اسپاتیفای)\n"
                "• حداکثر حجم هر فایل: 2 گیگابایت\n"
                "• بدون تبلیغات\n"
                "• کیفیت‌های بالاتر و امکانات ویژه\n"
                "• پشتیبانی سریع‌تر\n"
                "• دانلود از سایر پلتفرم‌ها (اینستاگرام، فیسبوک، تیک‌تاک و...) بدون محدودیت\n"
                "-----------------------------\n"
                "برای خرید اشتراک ویژه، از دکمه خرید اشتراک استفاده کنید."
            )
            await event.respond(compare_text, parse_mode="html")

        @client.on(events.NewMessage(pattern=r"^/guide(?:@\w+)?$"))
        async def guide_command(event):
            help_text = (
                "<b>📖 راهنمای استفاده از ربات مدیا دانلودر</b>\n"
                "-----------------------------\n"
                "1️⃣ لینک ویدیوی یا موزیک مورد نظر را از هر پلتفرم پشتیبانی‌شده ارسال کنید.\n"
                "2️⃣ اگر نیاز به انتخاب کیفیت یا فرمت باشد، گزینه‌ها نمایش داده می‌شود.\n"
                "3️⃣ پس از انتخاب، فایل دانلودی برای شما ارسال خواهد شد.\n"
                "4️⃣ برای دریافت جایزه و افزایش سقف دانلود روزانه ، از بخش رفرال لینک اختصاصی خود را بگیرید و به دوستانتان بدهید.\n"
                "5️⃣ برای خرید اشتراک ویژه و امکانات بیشتر و افزایش سقف دانلود روزانه ، از دکمه خرید اشتراک استفاده کنید.\n"
                "-----------------------------\n"
               
                "<b>🎵 قابلیت‌های ویژه:</b>\n"
                "- تلگرام:  میتونین لینک پست‌های کانال ها و گروه های عمومی که محدودیت فوروارد دارن و استوری افراد ارسال کنید تا ربات فایل شون رو براتون بفرسته.\n"
                "- 🔍 <b>تشخیص موسیقی (Shazam)</b>: برای استفاده از این قابلیت، باید فایل صوتی یا ویدیویی که بخشی از آهنگ داخلش هست رو برای ربات ارسال کنید. ربات آهنگ رو تشخیص میده و دانلود می‌کنه.\n"
                
            )
            await event.respond(help_text, parse_mode="html")
        @client.on(events.NewMessage)
        async def handle_user_panel_buttons(event):
            text = event.raw_text.strip()
            user_id = event.sender_id
            if not text or text.startswith('/'):
                return
            if text not in {'📝 دانلود زیرنویس یوتیوب', '💳 خرید اشتراک'}:
                return
            
            
            elif text == '📝 دانلود زیرنویس یوتیوب':
                if youtube_downloader:
                    youtube_downloader.request_subtitle_only(user_id)
                    await event.respond("لطفاً لینک ویدیوی یوتیوب را ارسال کنید تا فقط زیرنویس آن برای شما استخراج شود. ✅", parse_mode="html")
                else:
                    await event.respond("سیستم دریافت زیرنویس در حال حاضر در دسترس نیست. لطفاً بعداً دوباره تلاش کنید.")
                return
            
            elif text == '💳 خرید اشتراک':
                payment_info = (
                   "<b>💎 خرید اشتراک ویژه (VIP)</b>\n"
                    "-----------------------------\n"
                    "با خرید اشتراک ویژه یک ماهه، از امکانات زیر بهره‌مند می‌شوید:\n"
                    "• افزایش سقف دانلود روزانه به 50 فایل و 20 گیگابایت (فقط برای یوتیوب و اسپاتیفای)\n"
                    "• دانلود فایل تا حجم 1.5 گیگابایت\n"
                    "• عدم نمایش تبلیغات\n"
                    "• دسترسی به کیفیت‌های بالاتر و امکانات ویژه\n"
                    "• پشتیبانی سریع‌تر\n"
                    "-----------------------------\n"
                    "<b>نکته مهم:</b> در پلن رایگان فقط دانلود از یوتیوب و اسپاتیفای محدودیت دارد و سایر پلتفرم‌ها (اینستاگرام، فیسبوک، تیک‌تاک و...) بدون محدودیت هستند.\n"
                    "در پلن ویژه (VIP) نیز فقط یوتیوب و اسپاتیفای محدودیت ویژه دارند و سایر پلتفرم‌ها همچنان بدون محدودیت هستند.\n"
                    "-----------------------------\n"
                    "برای خرید اشتراک، مبلغ 90 هزار تومان به شماره کارت زیر واریز کنید و سپس عکس رسید را ارسال نمایید:\n"
                    "\n"
                    "شماره کارت:\n"
                     "<code>6219861867615725</code>\n"
                    "به نام: <b>پوریا حقدادی</b>\n"
                    "\n"
                    "پس از واریز، دکمه زیر را بزنید و عکس رسید را ارسال کنید. پس از تایید ادمین، اشتراک شما فعال خواهد شد."
                )
                buttons = [
                    [Button.inline('📤 ارسال رسید پرداخت', b'send_receipt')]
                ]
                await event.respond(payment_info, parse_mode="html", buttons=buttons)
                return
            

        @client.on(events.CallbackQuery(pattern=rb'send_receipt'))
        async def send_receipt_callback(event):
            global payment_receipt_waiting
            user_id = event.sender_id
            payment_receipt_waiting[user_id] = True
            await event.respond("لطفاً عکس رسید پرداخت خود را ارسال کنید.")
            await event.answer()

        # هندلر عکس رسید پرداخت
        @client.on(events.NewMessage(func=lambda e: e.photo))
        async def handle_payment_receipt(event):
            global payment_receipt_waiting
            user_id = event.sender_id
            if not payment_receipt_waiting.get(user_id):
                # اگر کاربر در حالت پرداخت نیست، عکس را نادیده بگیر
                return
            payment_receipt_waiting.pop(user_id, None)
            sender = await event.get_sender()
            username = getattr(sender, 'username', None)
            first_name = getattr(sender, 'first_name', '')
            last_name = getattr(sender, 'last_name', '')
            caption = (
                f"رسید پرداخت جدید برای خرید اشتراک\n"
                f"User ID: <code>{user_id}</code>\n"
                f"Username: @{username if username else 'ندارد'}\n"
                f"Name: {first_name} {last_name}"
            )
            ADMIN_ID = ADMIN_IDS[0]  # عدد ادمین را از تنظیمات یا config بگیرید
            buttons = [
                [Button.inline("✅ تایید تراکنش و فعال‌سازی اشتراک", f"approve_vip|{user_id}")]
            ]
            await event.client.send_file(
                ADMIN_ID,
                file=event.photo,
                caption=caption,
                buttons=buttons,
                parse_mode="html"
            )
            await event.respond("رسید شما دریافت شد و برای بررسی به ادمین ارسال شد. پس از تایید، اشتراک شما فعال می‌شود.")

        @client.on(events.CallbackQuery(pattern=rb"^approve_vip\|"))
        async def approve_vip_callback(event):
            if event.sender_id not in ADMIN_IDS:
                await event.answer("دسترسی فقط برای ادمین!", alert=True)
                return
            data = event.data.decode()
            parts = data.split("|")
            if len(parts) != 2:
                await event.answer("داده نامعتبر!", alert=True)
                return
            user_id = int(parts[1])
            import datetime
            today = datetime.date.today()
            expiry = today + datetime.timedelta(days=30)
            expiry_str = expiry.strftime('%Y-%m-%d')
            await user_db.set_vip(user_id, expiry_str)
            await event.edit("اشتراک VIP برای کاربر فعال شد.")
            try:
                await client.send_message(user_id, f"🎉 اشتراک VIP شما تا تاریخ {expiry_str} فعال شد! اکنون می‌توانید با محدودیت‌های ویژه دانلود کنید.")
            except Exception as e:
                logger.warning(f"Failed to notify user {user_id} about VIP activation: {e}")

        # Import and register 
        
        try:
            
            
            # Initialize  SelfBotManager 
            shared_selfbot_manager = SelfBotManager(
                sessions_dir=sessions_dir,
                accounts_json=accounts_json,
                group_chat_id=group_chat_id
            )
            await shared_selfbot_manager.start_all()
            shared_selfbot_manager.register_group_handler()


            
            # Initialize and register general downloader 
            
            generic_downloader = GenericSelfbotDownloader(
                client,
                selfbot_manager=shared_selfbot_manager,
                patterns=generic_patterns,
                channels=CHANNELS,
                admin_bot_token=ADMIN_BOT_TOKEN
            )
            generic_downloader.register_handlers()

            # --- Inline Admin Panel inside bot ---
            admin_states = {}
            # Runtime-editable settings
            admin_ids_current = set(ADMIN_IDS)
            allowed_domains_current = list(allowed_domains)
            youtube_bot_username_current = youtube_selfbot_bot_username

            def save_patterns_to_file(patterns_list):
                try:
                    import json
                    with open(GENERIC_PATTERNS_FILE, 'w', encoding='utf-8') as f:
                        json.dump(patterns_list, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error(f"Failed to save patterns: {e}")

            def save_admin_settings():
                try:
                    import json
                    from config import ADMIN_SETTINGS_FILE
                    data = {
                        'ADMIN_IDS': list(admin_ids_current),
                        'allowed_domains': list(allowed_domains_current),
                        'youtube_selfbot_bot_username': youtube_bot_username_current,
                    }
                    with open(ADMIN_SETTINGS_FILE, 'w', encoding='utf-8') as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.error(f"Failed to save admin settings: {e}")

            def render_patterns_text(patterns_list):
                lines = []
                for i, p in enumerate(patterns_list):
                    lines.append(f"{i}. bot: {p.get('bot_username','')}\n   rx: {p.get('pattern','')}")
                if not lines:
                    return "⚠️ لیستی وجود ندارد."
                return "\n\n".join(lines)

            def admin_menu_buttons():
                return [
                    [Button.inline("📜 لیست", b"adm_list"), Button.inline("➕ افزودن", b"adm_add")],
                    [Button.inline("🔁 بارگذاری از فایل", b"adm_reload")],
                    [Button.inline("👤 ادمین‌ها", b"adm_admins"), Button.inline("🌐 دامنه‌ها", b"adm_domains")],
                    [Button.inline("🤖 یوتیوب سلف‌بات", b"adm_ytsb")],
                ]

            @client.on(events.NewMessage(pattern=r"/admin"))
            async def admin_entry(event):
                if event.sender_id not in admin_ids_current:
                    return
                await event.respond("پنل مدیریت:", buttons=admin_menu_buttons())

            @client.on(events.CallbackQuery(pattern=b"adm_list"))
            async def adm_list_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                patterns = generic_downloader.get_patterns()
                buttons = []
                for i, _ in enumerate(patterns):
                    buttons.append([Button.inline(f"✏️ ویرایش {i}", f"adm_edit_{i}".encode()), Button.inline(f"🗑 حذف {i}", f"adm_del_{i}".encode())])
                buttons.append([Button.inline("⬅️ بازگشت", b"adm_back")])
                await event.edit(render_patterns_text(patterns), buttons=buttons)

            @client.on(events.CallbackQuery(pattern=b"adm_back"))
            async def adm_back_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                await event.edit("پنل مدیریت الگوها:", buttons=admin_menu_buttons())

            @client.on(events.CallbackQuery(pattern=b"adm_reload"))
            async def adm_reload_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                patterns = load_generic_patterns()
                try:
                    generic_downloader.set_patterns(patterns)
                except Exception as e:
                    await event.edit(f"❌ فایل شامل الگوی نامعتبر است: {e}", buttons=admin_menu_buttons())
                    return
                save_patterns_to_file(patterns)
                await event.edit("✅ از فایل بارگذاری شد.", buttons=admin_menu_buttons())

            @client.on(events.CallbackQuery(pattern=re.compile(br"adm_edit_(\d+)")))
            async def adm_edit_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                idx = int(event.pattern_match.group(1).decode())
                admin_states[event.sender_id] = {"state": "await_bot_username", "index": idx}
                await event.respond(f"برای آیتم {idx} نام کاربری بات جدید را بفرستید (مثلاً @mybot):")

            @client.on(events.CallbackQuery(pattern=re.compile(br"adm_del_(\d+)")))
            async def adm_del_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                idx = int(event.pattern_match.group(1).decode())
                patterns = generic_downloader.get_patterns()
                if 0 <= idx < len(patterns):
                    del patterns[idx]
                    save_patterns_to_file(patterns)
                    generic_downloader.set_patterns(patterns)
                    await event.respond("🗑 حذف شد.")
                else:
                    await event.respond("شاخص معتبر نیست.")

            @client.on(events.CallbackQuery(pattern=b"adm_add"))
            async def adm_add_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                admin_states[event.sender_id] = {"state": "await_new_pattern"}
                await event.respond("الگوی regex را ارسال کنید:")

            @client.on(events.NewMessage(from_users=ADMIN_IDS))
            async def admin_text_flow(event):
                # Route only admin messages in flow
                if event.sender_id not in admin_ids_current:
                    return
                state = admin_states.get(event.sender_id)
                if not state:
                    return
                text = event.raw_text.strip()
                if state["state"] == "await_bot_username":
                    try:
                        idx = state["index"]
                        patterns = generic_downloader.get_patterns()
                        if not (0 <= idx < len(patterns)):
                            await event.reply("شاخص معتبر نیست.")
                        else:
                            patterns[idx]["bot_username"] = text
                            try:
                                generic_downloader.set_patterns(patterns)
                            except Exception as e:
                                await event.reply(f"❌ به‌روزرسانی ناموفق: {e}")
                                return
                            save_patterns_to_file(patterns)
                            await event.reply("✅ به‌روزرسانی شد.")
                    finally:
                        admin_states.pop(event.sender_id, None)
                elif state["state"] == "await_new_pattern":
                    # Validate regex early
                    try:
                        re.compile(text, re.IGNORECASE)
                    except re.error as e:
                        await event.reply(f"❌ الگوی نامعتبر: {e}")
                        admin_states.pop(event.sender_id, None)
                        return
                    admin_states[event.sender_id] = {"state": "await_new_bot", "pattern": text}
                    await event.reply("نام کاربری بات مربوط به این الگو را ارسال کنید:")
                elif state["state"] == "await_new_bot":
                    try:
                        pattern_text = state.get("pattern")
                        new_item = {"pattern": pattern_text, "bot_username": text}
                        patterns = generic_downloader.get_patterns()
                        # Validate full set
                        test_list = list(patterns) + [new_item]
                        try:
                            generic_downloader.set_patterns(test_list)
                        except Exception as e:
                            await event.reply(f"❌ الگوی نامعتبر: {e}")
                            return
                        patterns.append(new_item)
                        save_patterns_to_file(patterns)
                        await event.reply("✅ اضافه شد.")
                    finally:
                        admin_states.pop(event.sender_id, None)

                elif state["state"] == "await_admin_add":
                    try:
                        new_id = int(text)
                        admin_ids_current.add(new_id)
                        save_admin_settings()
                        await event.reply("✅ ادمین اضافه شد.")
                    except Exception:
                        await event.reply("❌ فرمت نامعتبر. یک عدد ارسال کنید.")
                    finally:
                        admin_states.pop(event.sender_id, None)

                elif state["state"] == "await_domain_add":
                    allowed_domains_current.append(text)
                    try:
                        # Try live update if available
                        Simple_Downloader.set_allowed_domains(allowed_domains_current)
                    except Exception:
                        pass
                    save_admin_settings()
                    await event.reply("✅ دامنه اضافه شد.")
                    admin_states.pop(event.sender_id, None)

                elif state["state"] == "await_ytsb_set":
                    try:
                        nonlocal youtube_bot_username_current
                    except SyntaxError:
                        pass
                    youtube_bot_username_current = text
                    try:
                        youtube_selfbot_downloader.set_bot_username(text)
                        asyncio.create_task(youtube_selfbot_downloader.start_bot_for_all())
                        import config as _cfg
                        _cfg.youtube_selfbot_bot_username = text
                    except Exception as e:
                        await event.reply(f"❌ تنظیم لحظه‌ای ناموفق: {e}")
                        admin_states.pop(event.sender_id, None)
                        return
                    save_admin_settings()
                    await event.reply("✅ نام کاربری یوتیوب سلف‌بات تنظیم شد.")
                    admin_states.pop(event.sender_id, None)

            @client.on(events.CallbackQuery(pattern=b"adm_admins"))
            async def adm_admins_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                lines = ["ادمین‌ها:"] + [f"{i}. {uid}" for i, uid in enumerate(sorted(admin_ids_current))]
                buttons = [[Button.inline("➕ افزودن", b"adm_admins_add")]]
                for i, _ in enumerate(sorted(admin_ids_current)):
                    buttons.append([Button.inline(f"🗑 حذف {i}", f"adm_admins_del_{i}".encode())])
                buttons.append([Button.inline("⬅️ بازگشت", b"adm_back")])
                await event.edit("\n".join(lines), buttons=buttons)

            @client.on(events.CallbackQuery(pattern=b"adm_admins_add"))
            async def adm_admins_add_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                admin_states[event.sender_id] = {"state": "await_admin_add"}
                await event.respond("آیدی عددی ادمین را ارسال کنید:")

            @client.on(events.CallbackQuery(pattern=re.compile(br"adm_admins_del_(\d+)")))
            async def adm_admins_del_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                idx = int(event.pattern_match.group(1).decode())
                ids_sorted = sorted(admin_ids_current)
                if 0 <= idx < len(ids_sorted):
                    admin_ids_current.discard(ids_sorted[idx])
                    save_admin_settings()
                    await event.respond("🗑 حذف شد.")
                else:
                    await event.respond("شاخص معتبر نیست.")

            @client.on(events.CallbackQuery(pattern=b"adm_domains"))
            async def adm_domains_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                lines = ["دامنه‌های مجاز:"] + [f"{i}. {d}" for i, d in enumerate(allowed_domains_current)]
                buttons = [[Button.inline("➕ افزودن", b"adm_domains_add")]]
                for i, _ in enumerate(allowed_domains_current):
                    buttons.append([Button.inline(f"🗑 حذف {i}", f"adm_domains_del_{i}".encode())])
                buttons.append([Button.inline("⬅️ بازگشت", b"adm_back")])
                try:
                    await event.edit("\n".join(lines), buttons=buttons)
                except MessageNotModifiedError:
                    # محتوا تغییری نکرده؛ اخطار نده
                    await event.answer("بدون تغییر", alert=False)

            @client.on(events.CallbackQuery(pattern=b"adm_domains_add"))
            async def adm_domains_add_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                admin_states[event.sender_id] = {"state": "await_domain_add"}
                await event.respond("دامنه را ارسال کنید (مثلاً facebook.com):")

            @client.on(events.CallbackQuery(pattern=re.compile(br"adm_domains_del_(\d+)")))
            async def adm_domains_del_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                idx = int(event.pattern_match.group(1).decode())
                if 0 <= idx < len(allowed_domains_current):
                    del allowed_domains_current[idx]
                    try:
                        Simple_Downloader.set_allowed_domains(allowed_domains_current)
                    except Exception:
                        pass
                    save_admin_settings()
                    await event.respond("🗑 حذف شد.")
                else:
                    await event.respond("شاخص معتبر نیست.")

            @client.on(events.CallbackQuery(pattern=b"adm_ytsb"))
            async def adm_ytsb_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                await event.edit(f"نام فعلی: {youtube_bot_username_current}\n\nبرای تنظیم، پیام بفرستید.", buttons=[[Button.inline("✏️ تنظیم", b"adm_ytsb_set")],[Button.inline("⬅️ بازگشت", b"adm_back")]])

            @client.on(events.CallbackQuery(pattern=b"adm_ytsb_set"))
            async def adm_ytsb_set_cb(event):
                if event.sender_id not in admin_ids_current:
                    await event.answer("Unauthorized", alert=True)
                    return
                admin_states[event.sender_id] = {"state": "await_ytsb_set"}
                await event.respond("نام کاربری جدید را ارسال کنید (مثلاً @TopSaverBot):")
            
            
            
             # --- Register YouTubeSelfbotDownloader for YouTube links via @TopSaverBot ---
            from downloaders.youtube_selfbot_downloader import YouTubeSelfbotDownloader
            youtube_selfbot_downloader = YouTubeSelfbotDownloader(
                client,
                selfbot_manager=shared_selfbot_manager,
                channels=CHANNELS,
                admin_bot_token=ADMIN_BOT_TOKEN
            )
            # youtube_selfbot_downloader.register_handlers()
            # youtube_selfbot_downloader.register_group_handler()
            youtube_selfbot_downloader.register_callback_handler()
            
            
            
            # register main youtube downloader
            
            from downloaders.youtube_downloader import register_handlers as register_youtube
            # Register handlers for each downloader
            youtube_downloader = await register_youtube(client, youtube_selfbot_downloader=youtube_selfbot_downloader)
            
            
            
            
            # # initialize instagram downloader
            
            # instagram_downloader = InstagramDownloader(
            #     client,
            #     selfbot_manager=shared_selfbot_manager,
            #     bot_username="@IgSavesBot",
            #     handle_url_genreic=generic_downloader.handle_url
            # )
            # instagram_downloader.register_handlers()
            
            
            
            # Initialize and register Spotify downloader
            from downloaders.spotify_downloader import SpotifyDownloader
            spotify_downloader = SpotifyDownloader(client)
            spotify_downloader.register_handlers()
            
            # Initialize and register Pinterest downloader
            from downloaders.pinterest_downloader import PinterestDownloader
            pinterest_downloader = PinterestDownloader(client)
            pinterest_downloader.register_handlers()

           
            # # Initialize and register Twitter downloader
            # from downloaders.twitter_downloader import TwitterDownloader
            # twitter_downloader = TwitterDownloader(client)
            # twitter_downloader.register_handlers()
            
            
            # # Initialize and register RadioJavan downloader
            # radiojavan_downloader = RadioJavanDownloader(client)
            # radiojavan_downloader.register_handlers()
            
           
           
        # --- SimpleDownloader for Snapchat, Facebook, SoundCloud, Castbox ---
                        
            Simple_Downloader = SimpleDownloader(
                client,
                url_pattern=allowed_domains,
                channels=CHANNELS,
                admin_bot_token=ADMIN_BOT_TOKEN
            )
            Simple_Downloader.register_handlers()
            # --- پایان SimpleDownloader ---

            # Initialize and register Shazam downloader
            shazam_downloader = ShazamDownloader(client)
            shazam_downloader.register_handlers()



            
            
            logger.info("All downloaders registered successfully!")
        except Exception as e:
            logger.error(f"Failed to register downloaders: {e}")
            raise
        
        # Keep the bot running
        await client.run_until_disconnected()
        
    except SessionPasswordNeededError:
        logger.error("Two-factor authentication required")
    except PhoneCodeInvalidError:
        logger.error("Invalid phone code")
    except FloodWaitError as e:
        logger.error(f"Rate limit exceeded. Wait {e.seconds} seconds")
    except Exception as e:
        logger.error(f"An error occurred: {e}")
    finally:
        try:
            await client.disconnect()
        except Exception as e:
            logger.error(f"Error during client disconnect: {e}")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}") 