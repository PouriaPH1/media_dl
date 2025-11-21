import os
import glob
import shutil
from yt_dlp import YoutubeDL

TEST_URLS = {
    "youtube": "https://youtube.com/shorts/UCgAeOxzl_M?si=k7AWPymg-Zxr0izp",
    "instagram": "https://www.instagram.com/reel/DHMeOx7x4VX/?igsh=czZsYmlqdWZ6b3Nl",
    "twitter": "https://x.com/maryismoody/status/1383255227306938368"
}

COOKIE_DIRS = {
    "youtube": "/home/zizo/media_dll/cookies/cookies_youtube",
    # "instagram": "/home/zizo/media_dll/cookies/cookies_instagram",
    # "twitter": "/home/zizo/media_dll/cookies/cookies_twitter"
}

BAD_COOKIES_BASE = "/home/zizo/media_dll/cookies/bad_cookies"

def test_cookie(service, cookie_file, test_url, bad_cookie_dir):
    ydl_opts = {
        "cookiefile": cookie_file,
        "quiet": True,
        "skip_download": True,
        "extract_flat": True,
        "proxy":"socks5h://127.0.0.1:4400"
    }
    try:
        with YoutubeDL(ydl_opts) as ydl:
            ydl.extract_info(test_url, download=False)
        print(f"[OK] {service} - {os.path.basename(cookie_file)}")
        return True
    except Exception as e:
        print(f"[FAIL] {service} - {os.path.basename(cookie_file)}: {e}")
        # Move bad cookie file
        os.makedirs(bad_cookie_dir, exist_ok=True)
        dest = os.path.join(bad_cookie_dir, os.path.basename(cookie_file))
        shutil.move(cookie_file, dest)
        print(f"Moved bad cookie to: {dest}")
        return False

def main():
    for service, cookie_dir in COOKIE_DIRS.items():
        print(f"\nTesting {service} cookies in {cookie_dir} ...")
        if not os.path.exists(cookie_dir):
            print(f"Directory not found: {cookie_dir}")
            continue
        cookie_files = glob.glob(os.path.join(cookie_dir, "*.txt"))
        if not cookie_files:
            print("No cookie files found.")
            continue
        bad_cookie_dir = os.path.join(BAD_COOKIES_BASE, service)
        for cookie_file in cookie_files:
            test_cookie(service, cookie_file, TEST_URLS[service], bad_cookie_dir)

if __name__ == "__main__":
    main() 