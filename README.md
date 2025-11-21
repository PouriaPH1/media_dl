# Telegram Media Downloader Bot

A powerful, modular Telegram bot for downloading media from a wide range of platforms, including YouTube, Spotify, Instagram, Twitter, TikTok, Facebook, Pinterest, SoundCloud, and Castbox. Built with extensibility and ease of use in mind.

---

## Features

- **Multi-platform support:** Download videos, music, and podcasts from YouTube, Spotify, Instagram, Twitter, TikTok, Facebook, Pinterest, SoundCloud, and Castbox.
- **Modular design:** Easily add new platforms by implementing a new downloader class.
- **Asynchronous operation:** Efficient, non-blocking downloads and Telegram interactions using Telethon.
- **Admin tools:** Broadcast messages to all users, user database management.
- **Caching:** Optional caching for faster repeated downloads (YouTube, etc.).
- **Channel membership enforcement:** Optionally require users to join specific Telegram channels before using the bot.
- **Clean codebase:** Well-structured, maintainable, and extensible.

---

## Supported Platforms & Features

- **YouTube:** Download videos and audio in various formats and qualities. Caching for repeated downloads. Handles playlists and thumbnails.
- **Spotify:** Download tracks, albums, and playlists by searching YouTube for the best match. Requires Spotify API credentials.
- **Instagram:** Download posts, reels, stories, and TV videos using a third-party API.
- **Twitter/X:** Download videos and audio from tweets. Format and quality selection.
- **TikTok:** Download TikTok videos with format and quality selection.
- **Facebook:** Download Facebook videos and reels.
- **Pinterest:** Download Pinterest videos and images.
- **SoundCloud:** Download SoundCloud tracks as MP3.
- **Castbox:** Download podcasts from Castbox.

---

## Setup

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd <repo-directory>
   ```
2. **Create a virtual environment**
   ```bash
   python -m venv venv
   # On Windows:
   venv\Scripts\activate
   # On Linux/Mac:
   source venv/bin/activate
   ```
3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
4. **Create a `.env` file** in the root directory with your credentials and configuration:
   ```ini
   API_ID=your_telegram_api_id
   API_HASH=your_telegram_api_hash
   BOT_TOKEN=your_telegram_bot_token
   ADMIN_BOT_TOKEN=your_telegram_bot_token  # For admin checks (can be same as BOT_TOKEN)
   # Optional:
   PROXY=socks5://user:pass@host:port
   COOKIE_DIR=path_to_cookie_dir
   COOKIE_DIR_TWITTER=path_to_twitter_cookies
   CHANNELS=@channel1,@channel2  # Comma-separated list of required channels
   CACHE_ENABLED=true
   CACHE_TYPE=database
   CACHE_DB_FILE=video_cache.db
   SAVE_CHANNEL_NAME=default_channel
   USER_DB_FILE=user_db.sqlite3
   # Spotify API (for Spotify downloads)
   CLIENT_ID=your_spotify_client_id
   CLIENT_SECRET=your_spotify_client_secret
   REDIRECT_URL=your_spotify_redirect_url
   ```
5. **(Optional) Place cookies for YouTube, Twitter, etc.**
   - Place your cookies files (e.g., `cookies_youtube.txt`, `cookies_twitter.txt`) in the specified cookie directories for better download reliability.

---

## Usage

1. **Start the bot**
   ```bash
   python main.py
   ```
2. **Interact with the bot**
   - Send a supported media URL (YouTube, Spotify, Instagram, etc.) to the bot.
   - The bot will process the link, let you select format/quality (if applicable), and send the downloaded file back to you.
   - Use `/start` and `/help` for instructions.
   - Admins can use `/broadcast <message>` to send a message to all users.

---

## Configuration Details

- **API_ID, API_HASH, BOT_TOKEN:** Required for Telegram bot operation. Get from https://my.telegram.org.
- **ADMIN_BOT_TOKEN:** Used for admin operations and channel membership checks.
- **PROXY:** (Optional) SOCKS5 proxy for Telegram and downloads.
- **COOKIE_DIR, COOKIE_DIR_TWITTER:** (Optional) Directories for platform-specific cookies.
- **CHANNELS:** (Optional) Comma-separated list of channels users must join to use the bot.
- **CACHE_ENABLED, CACHE_TYPE, CACHE_DB_FILE:** Enable and configure caching for faster repeated downloads.
- **SAVE_CHANNEL_NAME:** Directory or channel name for saving files.
- **USER_DB_FILE:** Path to the user database file.
- **Spotify API:** Required for Spotify downloads. Register your app at https://developer.spotify.com/dashboard/.

---

## Adding New Downloaders

1. **Create a new file** in the `downloaders` directory (e.g., `myplatform_downloader.py`).
2. **Inherit from `BaseDownloader`** and implement required methods:
   - `get_url_pattern()` — Return a regex pattern for matching URLs.
   - `download_media(url)` — Download the media and return the file path.
   - (Optionally) Override `register_handlers()` for custom event handling.
3. **Register your downloader** in `main.py` by importing and initializing it, then calling `register_handlers()`.

---

## Project Structure

```
.
├── main.py                # Bot initialization and main loop
├── config.py              # Environment variables and configuration
├── requirements.txt       # Project dependencies
├── README.md              # This file
├── downloaders/           # All platform-specific downloaders
│   ├── base_downloader.py
│   ├── youtube_downloader.py
│   ├── spotify_downloader.py
│   ├── instagram_downloader.py
│   ├── twitter_downloader.py
│   ├── tiktok_downloader.py
│   ├── facebook_downloader.py
│   ├── pinterest_downloader.py
│   ├── soundcloud_downloader.py
│   ├── castbox_downloader.py
│   └── ...
├── cookies/               # Place your cookies here (if needed)
├── downloads/             # Downloaded files
├── user_db.sqlite3        # User database
├── video_cache.db         # Video cache database
├── venv/                  # Python virtual environment
└── ...
```

---

## Dependencies

- `telethon` — Telegram API client
- `python-dotenv` — Load environment variables from `.env`
- `yt-dlp` — Download videos from various platforms
- `spotipy` — Spotify API client
- `beautifulsoup4` — HTML parsing (Pinterest, etc.)

Install all dependencies with:
```bash
pip install -r requirements.txt
```

---

## License

MIT License 