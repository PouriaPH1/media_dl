import sqlite3
from config import CACHE_DB_FILE

def view_cache():
    try:
        conn = sqlite3.connect(CACHE_DB_FILE)
        cursor = conn.cursor()
        
        cursor.execute("SELECT video_id, quality, file_id FROM video_cache")
        results = cursor.fetchall()
        
        if not results:
            print("Cache is empty.")
            return
        
        print("\nCache Contents:")
        print("=" * 50)
        for video_id, quality, file_id in results:
            print(f"Video ID: {video_id}")
            print(f"Quality: {quality}")
            print(f"File ID: {file_id}")
            print("=" * 50)
            
    except sqlite3.Error as e:
        print(f"Error reading cache: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == "__main__":
    view_cache() 