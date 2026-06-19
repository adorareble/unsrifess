import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from database import init_db, create_admin, get_admin_by_username, close_pool
from auth import hash_password


async def main():
    print("=== Create Root Admin ===\n")

    await init_db()

    existing = await get_admin_by_username("root", tenant_id=None)
    if existing:
        print("Root admin 'root' already exists.")
        yn = input("Create another root admin? (y/N): ").strip().lower()
        if yn != "y":
            print("Aborted.")
            await close_pool()
            return

    username = input("Username: ").strip()
    if not username:
        print("Username cannot be empty.")
        await close_pool()
        return

    check = await get_admin_by_username(username, tenant_id=None)
    if check:
        print(f"Admin '{username}' already exists.")
        await close_pool()
        return

    display_name = input("Display name: ").strip()
    if not display_name:
        print("Display name cannot be empty.")
        await close_pool()
        return

    password = input("Password: ").strip()
    if not password or len(password) < 6:
        print("Password must be at least 6 characters.")
        await close_pool()
        return

    confirm = input("Confirm password: ").strip()
    if password != confirm:
        print("Passwords do not match.")
        await close_pool()
        return

    hashed = hash_password(password)
    admin = await create_admin(username, hashed, display_name, "superadmin", tenant_id=None, is_root=True)
    print(f"\nRoot admin created successfully!")
    print(f"  ID: {admin['id']}")
    print(f"  Username: {admin['username']}")
    print(f"  Display name: {admin['display_name']}")
    print(f"  Role: {admin['role']}")
    print(f"  Root: {admin['is_root']}")
    print(f"\nLogin at: http://127.0.0.1:8001/panel/admin")

    await close_pool()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
