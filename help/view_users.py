import sqlite3
from tabulate import tabulate

def view_all_tables(db_path='user_db.sqlite3'):
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # گرفتن لیست همه جدول‌ها
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]

        if not tables:
            print("No tables found in database.")
            return

        for table in tables:
            print(f"\n=== Table: {table} ===")
            # گرفتن ستون‌ها
            cursor.execute(f"PRAGMA table_info({table})")
            columns = [column[1] for column in cursor.fetchall()]
            # گرفتن داده‌ها
            cursor.execute(f"SELECT * FROM {table}")
            rows = cursor.fetchall()
            if rows:
                print(tabulate(rows, headers=columns, tablefmt="grid"))
                print(f"Total rows: {len(rows)}")
            else:
                print("No data in this table.")

    except sqlite3.Error as e:
        print(f"Database error: {e}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    db_path="/home/leon/telegram_bots/media_dll/user_db.sqlite3"
    view_all_tables(db_path)