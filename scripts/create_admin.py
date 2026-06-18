import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from database import init_db, create_admin, get_all_admins
from auth import hash_password


async def main():
    print("=== Create Superadmin ===\n")

    await init_db()

    admins = await get_all_admins()
    if len(admins) > 0:
        print(f"Warning: There are already {len(admins)} admin(s) in the database.")

    username = input("Username: ").strip()
    if not username:
        print("Username cannot be empty.")
        return

    display_name = input("Display name: ").strip()
    if not display_name:
        print("Display name cannot be empty.")
        return

    password = input("Password: ").strip()
    if not password or len(password) < 6:
        print("Password must be at least 6 characters.")
        return

    confirm = input("Confirm password: ").strip()
    if password != confirm:
        print("Passwords do not match.")
        return

    hashed = hash_password(password)
    admin = await create_admin(username, hashed, display_name, "superadmin")
    print(f"\nSuperadmin created successfully!")
    print(f"  ID: {admin['id']}")
    print(f"  Username: {admin['username']}")
    print(f"  Display name: {admin['display_name']}")
    print(f"  Role: {admin['role']}")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
