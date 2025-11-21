from telethon import TelegramClient, errors
from telethon.sessions import StringSession
from getpass import getpass
import sys
import asyncio

API_ID = 23024403
API_HASH = "95fa5dfa7f94b82049dc3a78ad47e953"

# Put a single 2FA password here (applies to the account you're creating a session for).
# If empty (""), the script will ask for the password interactively.
PASSWORD = "Fddfdffgfgfdfdf3234"  # e.g. "My2FAPassword"

async def main():
    if API_ID == 1234567 or API_HASH == "your_api_hash_here":
        print("Please set your API_ID and API_HASH first (get them from my.telegram.org).")
        sys.exit(1)

    phone = input("Enter phone number (with country code, e.g. +98XXXXXXXXXX): ").strip()
    if not phone:
        print("No phone number entered. Exiting.")
        return

    safe_name = "".join(ch for ch in phone if ch.isdigit() or ch == '+')
    session_filename = f"{safe_name}.session"

    client = TelegramClient(session_filename, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        print(f"Already authorized. Session file: {session_filename}")
        await client.disconnect()
        return

    try:
        await client.send_code_request(phone)
    except errors.PhoneNumberInvalidError:
        print("Invalid phone number format. Example: +98XXXXXXXXXX")
        await client.disconnect()
        return
    except Exception as e:
        print("Error while sending code:", repr(e))
        await client.disconnect()
        return

    code = input("Enter the login code you received: ").strip()
    if not code:
        print("No code entered. Exiting.")
        await client.disconnect()
        return

    try:
        await client.sign_in(phone=phone, code=code)
    except errors.SessionPasswordNeededError:
        # Use the single in-script password; if empty, ask interactively
        if PASSWORD:
            print("2FA password found in script — attempting automatic sign-in.")
            password = PASSWORD
        else:
            password = getpass("This account has 2FA enabled. Enter the password: ")

        try:
            await client.sign_in(password=password)
        except Exception as e:
            print("Failed to sign in with password:", repr(e))
            await client.disconnect()
            return
    except Exception as e:
        print("Sign-in error:", repr(e))
        await client.disconnect()
        return

    print(f"Session created successfully: {session_filename}")

    show_str = input("Show StringSession (for server use)? [y/N]: ").strip().lower()
    if show_str == 'y':
        session_str = StringSession.save(client.session)
        print("\n=== StringSession ===")
        print(session_str)
        print("=====================")

    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
