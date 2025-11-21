# --- تنظیمات را اینجا قرار دهید ---

# مسیرها و مقادیر را اینجا تغییر دهید
SESSIONS_DIR = ""
ACCOUNTS_JSON = ""
GROUP = 'r'  # لینک یا آیدی گروه/کانال هدف
BOT_USERNAME = ""  # نام کاربری ربات برای استارت (در صورت نیاز)

# انتخاب تابع مورد نظر:
# اگر می‌خواهید همه اکانت‌ها به گروه جوین شوند:
RUN_JOIN_GROUP = False
# اگر می‌خواهید همه اکانت‌ها یک ربات را استارت کنند:
RUN_START_BOT = True

import asyncio
import os
from insta_self import SelfBotManager
from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError, InviteHashInvalidError
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest

async def join_all_to_group(sessions_dir, accounts_json, group, bot_username=None):
    manager = SelfBotManager(sessions_dir, accounts_json, group_chat_id=group)
    await manager.start_all()
    print(f"Loaded {len(manager.clients)} accounts. Joining all to group: {group}")
    for client in manager.clients:
        try:
            if group.startswith('t.me/'):
                group_entity = group.split('t.me/')[-1].strip('/')
                if group_entity.startswith('+'):
                    # Join via invite hash
                    invite_hash = group_entity[1:]
                    await client(ImportChatInviteRequest(invite_hash))
                else:
                    # Join via username
                    await client(JoinChannelRequest(group_entity))
            elif group.startswith('+'):
                # Just invite hash
                await client(ImportChatInviteRequest(group[1:]))
            else:
                # Assume username or ID
                await client(JoinChannelRequest(group))
            print(f"[+] Joined: {client.session.filename}")
        except UserAlreadyParticipantError:
            print(f"[=] Already joined: {client.session.filename}")
        except (InviteHashExpiredError, InviteHashInvalidError) as e:
            print(f"[!] Invalid/expired invite for {client.session.filename}: {e}")
        except Exception as e:
            print(f"[!] Failed to join {client.session.filename}: {e}")

async def start_bot_for_all(sessions_dir, accounts_json, bot_username):
    manager = SelfBotManager(sessions_dir, accounts_json, group_chat_id=None)
    await manager.start_all()
    print(f"Loaded {len(manager.clients)} accounts. Sending /start to bot: {bot_username}")
    for client in manager.clients:
        try:
            await client.send_message(bot_username, "/start")
            print(f"[+] /start sent from: {client.session.filename}")
        except Exception as e:
            print(f"[!] Failed to send /start from {client.session.filename}: {e}")

def main():
    # فقط کافی است RUN_JOIN_GROUP یا RUN_START_BOT را True کنید
    if RUN_JOIN_GROUP:
        asyncio.run(join_all_to_group(SESSIONS_DIR, ACCOUNTS_JSON, GROUP, BOT_USERNAME))
    elif RUN_START_BOT:
        asyncio.run(start_bot_for_all(SESSIONS_DIR, ACCOUNTS_JSON, BOT_USERNAME))
    else:
        print("هیچ عملیاتی انتخاب نشده است. لطفاً یکی از RUN_JOIN_GROUP یا RUN_START_BOT را True قرار دهید.")

if __name__ == '__main__':
    main()
