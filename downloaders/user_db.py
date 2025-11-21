import aiosqlite
from typing import Optional, Dict, List
from config import USER_DB_FILE
import uuid

class UserDB:
    def __init__(self, db_file: str = USER_DB_FILE):
        self.db_file = db_file

    async def _create_table(self):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            await conn.execute("PRAGMA journal_mode=WAL;")
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS referrals (
                        referrer_id INTEGER,
                        referred_id INTEGER,
                        date TEXT,
                        status TEXT DEFAULT 'pending',
                        PRIMARY KEY (referrer_id, referred_id)
                    )
                ''')
                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS users (
                        user_id INTEGER PRIMARY KEY,
                        username TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS user_limits (
                        user_id INTEGER PRIMARY KEY,
                        last_update_date TEXT,
                        daily_count INTEGER DEFAULT 0,
                        daily_size INTEGER DEFAULT 0,
                        bonus_count INTEGER DEFAULT 0,
                        bonus_size INTEGER DEFAULT 0,
                        is_vip INTEGER DEFAULT 0,
                        vip_expiry TEXT
                    )
                ''')
                await cursor.execute('''
                    CREATE TABLE IF NOT EXISTS referral_tokens (
                        user_id INTEGER PRIMARY KEY,
                        token TEXT UNIQUE
                    )
                ''')
                await conn.commit()

    async def add_or_update_user(self, user_id: int, username: Optional[str]):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    INSERT INTO users (user_id, username)
                    VALUES (?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET 
                        username=excluded.username
                ''', (user_id, username))
                await conn.commit()

    async def get_user(self, user_id: int) -> Optional[Dict]:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT user_id, username, created_at FROM users WHERE user_id = ?', (user_id,))
                row = await cursor.fetchone()
                if row:
                    return {
                        'user_id': row[0],
                        'username': row[1],
                        'created_at': row[2]
                    }
                return None

    async def get_all_users(self) -> List[Dict]:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT user_id, username, created_at FROM users')
                rows = await cursor.fetchall()
                return [{
                    'user_id': row[0],
                    'username': row[1],
                    'created_at': row[2]
                } for row in rows]

    async def get_user_id_by_referral_token(self, token: str) -> int:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT user_id FROM referral_tokens WHERE token = ?', (token,))
                row = await cursor.fetchone()
                return row[0] if row else None

    async def get_or_create_referral_token(self, user_id: int) -> str:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT token FROM referral_tokens WHERE user_id = ?', (user_id,))
                row = await cursor.fetchone()
                if row and row[0]:
                    return row[0]
                token = uuid.uuid4().hex
                await cursor.execute('INSERT INTO referral_tokens (user_id, token) VALUES (?, ?)', (user_id, token))
                await conn.commit()
                return token

    async def get_limits(self, user_id: int, today: str, default_count: int, default_size: int):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT last_update_date, daily_count, daily_size, bonus_count, bonus_size, is_vip, vip_expiry FROM user_limits WHERE user_id = ?', (user_id,))
                row = await cursor.fetchone()
                if row:
                    last_update_date, daily_count, daily_size, bonus_count, bonus_size, is_vip, vip_expiry = row
                    # Reset daily limits if date changed
                    if last_update_date != today:
                        daily_count = 0
                        daily_size = 0
                        await cursor.execute('''
                            UPDATE user_limits SET last_update_date = ?, daily_count = 0, daily_size = 0 WHERE user_id = ?
                        ''', (today, user_id))
                        await conn.commit()
                    return {
                        'daily_count': daily_count,
                        'daily_size': daily_size,
                        'bonus_count': bonus_count,
                        'bonus_size': bonus_size,
                        'is_vip': is_vip,
                        'vip_expiry': vip_expiry
                    }
                else:
                    # Insert new user_limits row
                    await cursor.execute('''
                        INSERT INTO user_limits (user_id, last_update_date, daily_count, daily_size, bonus_count, bonus_size, is_vip, vip_expiry)
                        VALUES (?, ?, 0, 0, 0, 0, 0, NULL)
                    ''', (user_id, today))
                    await conn.commit()
                    return {
                        'daily_count': 0,
                        'daily_size': 0,
                        'bonus_count': 0,
                        'bonus_size': 0,
                        'is_vip': 0,
                        'vip_expiry': None
                    }

    async def is_vip(self, user_id: int) -> bool:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT is_vip, vip_expiry FROM user_limits WHERE user_id = ?', (user_id,))
                row = await cursor.fetchone()
                if row:
                    is_vip, vip_expiry = row
                    if is_vip and vip_expiry:
                        import datetime
                        try:
                            expiry = datetime.datetime.strptime(vip_expiry, '%Y-%m-%d')
                            if expiry >= datetime.datetime.now():
                                return True
                        except Exception:
                            pass
                return False

    async def set_vip(self, user_id: int, expiry_date: str):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT 1 FROM user_limits WHERE user_id = ?', (user_id,))
                exists = await cursor.fetchone()
                if not exists:
                    await cursor.execute('''
                        INSERT INTO user_limits (user_id, last_update_date, daily_count, daily_size, bonus_count, bonus_size, is_vip, vip_expiry)
                        VALUES (?, DATE('now'), 0, 0, 0, 0, 1, ?)
                    ''', (user_id, expiry_date))
                else:
                    await cursor.execute('UPDATE user_limits SET is_vip = 1, vip_expiry = ? WHERE user_id = ?', (expiry_date, user_id))
                await conn.commit()

    async def remove_vip(self, user_id: int):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('UPDATE user_limits SET is_vip = 0, vip_expiry = NULL WHERE user_id = ?', (user_id,))
                await conn.commit()

    async def update_limits(self, user_id: int, today: str, count_inc: int, size_inc: int, default_count: int, default_size: int):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT last_update_date, daily_count, daily_size FROM user_limits WHERE user_id = ?', (user_id,))
                row = await cursor.fetchone()
                if row:
                    last_update_date, daily_count, daily_size = row
                    if last_update_date != today:
                        daily_count = 0
                        daily_size = 0
                        await cursor.execute('''
                            UPDATE user_limits SET last_update_date = ?, daily_count = 0, daily_size = 0 WHERE user_id = ?
                        ''', (today, user_id))
                await cursor.execute('''
                    UPDATE user_limits SET daily_count = daily_count + ?, daily_size = daily_size + ? WHERE user_id = ?
                ''', (count_inc, size_inc, user_id))
                await conn.commit()

    async def consume_bonus(self, user_id: int, count: int, size: int):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    UPDATE user_limits SET bonus_count = bonus_count - ?, bonus_size = bonus_size - ? WHERE user_id = ?
                ''', (count, size, user_id))
                await conn.commit()

    async def add_referral_bonus(self, user_id: int, bonus_count: int, bonus_size: int):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    UPDATE user_limits SET bonus_count = bonus_count + ?, bonus_size = bonus_size + ? WHERE user_id = ?
                ''', (bonus_count, bonus_size, user_id))
                await conn.commit()

    async def get_pending_referrer(self, referred_id: int) -> Optional[int]:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    SELECT referrer_id FROM referrals WHERE referred_id = ? AND status = 'pending'
                ''', (referred_id,))
                row = await cursor.fetchone()
                return row[0] if row else None

    async def add_pending_referral(self, referrer_id: int, referred_id: int):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    INSERT OR IGNORE INTO referrals (referrer_id, referred_id, date, status)
                    VALUES (?, ?, DATE('now'), 'pending')
                ''', (referrer_id, referred_id))
                await conn.commit()

    async def complete_referral_and_give_bonus(self, referrer_id: int, referred_id: int):
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('''
                    SELECT status FROM referrals WHERE referrer_id = ? AND referred_id = ?
                ''', (referrer_id, referred_id))
                row = await cursor.fetchone()
                if not row or row[0] == 'complete':
                    return
                for uid in [referrer_id, referred_id]:
                    await cursor.execute('''
                        INSERT OR IGNORE INTO user_limits (user_id, last_update_date, daily_count, daily_size, bonus_count, bonus_size)
                        VALUES (?, DATE('now'), 0, 0, 0, 0)
                    ''', (uid,))
                await cursor.execute('''
                    UPDATE referrals SET status = 'complete' WHERE referrer_id = ? AND referred_id = ?
                ''', (referrer_id, referred_id))
                BONUS_COUNT = 5
                BONUS_SIZE = 524288000  # 500 مگابایت
                await cursor.execute('''
                    UPDATE user_limits SET bonus_count = bonus_count + ?, bonus_size = bonus_size + ? WHERE user_id = ?
                ''', (BONUS_COUNT, BONUS_SIZE, referrer_id))
                await cursor.execute('''
                    UPDATE user_limits SET bonus_count = bonus_count + ?, bonus_size = bonus_size + ? WHERE user_id = ?
                ''', (BONUS_COUNT, BONUS_SIZE, referred_id))
                await conn.commit() 

    async def get_successful_referral_count(self, user_id: int) -> int:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ? AND status = "complete"', (user_id,))
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_successful_referrals(self, user_id: int) -> list:
        async with aiosqlite.connect(self.db_file, timeout=30) as conn:
            async with conn.cursor() as cursor:
                await cursor.execute('SELECT referred_id, date FROM referrals WHERE referrer_id = ? AND status = "complete"', (user_id,))
                return await cursor.fetchall() 