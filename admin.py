import sqlite3
from datetime import datetime, timezone, timedelta

DB = "gemin.db"

def get_users():
    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    cur.execute("SELECT username,password,status,plan,price,expiry FROM users")
    rows = cur.fetchall()
    conn.close()
    return rows

def activate_user(username):
    conn = sqlite3.connect(DB)
    cur = conn.cursor()

    expiry = (
        datetime.now(timezone.utc) + timedelta(days=14)
    ).isoformat()

    cur.execute("""
    UPDATE users
    SET status='ACTIVE', expiry=?
    WHERE username=?
    """, (expiry, username))

    conn.commit()
    changed = cur.rowcount
    conn.close()

    return changed > 0


print("\n=== GEMiN ADMIN PANEL ===\n")

users = get_users()

for u in users:
    print(f"User: {u[0]} | Status: {u[2]} | Plan: {u[3]}")

username = input("\nUsername ya ku activate: ")

if activate_user(username):
    print("\n✅ Account imekuwa ACTIVE kwa siku 14")
else:
    print("\n❌ Username haijapatikana")
