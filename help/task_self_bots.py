# --- تنظیمات را اینجا قرار دهید ---

SESSIONS_DIR = ""
ACCOUNTS_JSON = ""

# می‌توانید چندین لینک کانال یا گروه را اینجا وارد کنید
GROUPS = [
    
]

BOT_USERNAME = ""  # نام کاربری ربات برای استارت (در صورت نیاز)

# انتخاب تابع مورد نظر:
RUN_JOIN_GROUP = True   # همه اکانت‌ها به گروه/کانال جوین شوند؟
RUN_START_BOT = False   # همه اکانت‌ها ربات را استارت کنند؟

import asyncio
from downloaders.insta_self import SelfBotManager
from telethon.errors import UserAlreadyParticipantError, InviteHashExpiredError, InviteHashInvalidError
from telethon.tl.functions.messages import ImportChatInviteRequest
from telethon.tl.functions.channels import JoinChannelRequest

async def join_all_to_groups(sessions_dir, accounts_json, groups, bot_username=None):
    manager = SelfBotManager(sessions_dir, accounts_json, group_chat_id=None)
    await manager.start_all()
    print(f"Loaded {len(manager.clients)} accounts. Joining all to {len(groups)} groups/channels...")

    for client in manager.clients:
        for group in groups:
            try:
                if group.startswith('https://t.me/'):
                    group_entity = group.split('https://t.me/')[-1].strip('/')
                    if group_entity.startswith('+'):
                        invite_hash = group_entity[1:]
                        await client(ImportChatInviteRequest(invite_hash))
                    else:
                        await client(JoinChannelRequest(group_entity))
                elif group.startswith('+'):
                    await client(ImportChatInviteRequest(group[1:]))
                else:
                    await client(JoinChannelRequest(group))

                print(f"[+] {client.session.filename} joined -> {group}")
            except UserAlreadyParticipantError:
                print(f"[=] {client.session.filename} already joined -> {group}")
            except (InviteHashExpiredError, InviteHashInvalidError) as e:
                print(f"[!] Invalid/expired invite for {client.session.filename} -> {group}: {e}")
            except Exception as e:
                print(f"[!] Failed to join {client.session.filename} -> {group}: {e}")

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
    if RUN_JOIN_GROUP:
        asyncio.run(join_all_to_groups(SESSIONS_DIR, ACCOUNTS_JSON, GROUPS, BOT_USERNAME))
    elif RUN_START_BOT:
        asyncio.run(start_bot_for_all(SESSIONS_DIR, ACCOUNTS_JSON, BOT_USERNAME))
    else:
        print("هیچ عملیاتی انتخاب نشده است. لطفاً یکی از RUN_JOIN_GROUP یا RUN_START_BOT را True قرار دهید.")

if __name__ == '__main__':
    main()
