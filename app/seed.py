import asyncio
from sqlalchemy import delete
# Fix 1: Import from 'app.db', not 'app.database'
# Fix 2: Import 'AsyncSessionLocal' and (optionally) rename it
from app.db import AsyncSessionLocal as async_session_maker, engine 
from app.models import Base, User
# Fix 3: Import 'hash_password' and rename it to 'get_password_hash'
from app.auth import hash_password as get_password_hash

async def seed():
    # إنشاء الجداول لو مش موجودة
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        # نحذف كل المستخدمين القدامى (لو تحب)
        await session.execute(delete(User))

        users = [
            User(
                email="zaher@local",
                full_name="Zaher",
                role="admin",
                hashed_password=get_password_hash("zah1405"),
                # is_admin=True,
            ),
            User(
                email="ariana@local",
                full_name="Ariana",
                role="manager",
                hashed_password=get_password_hash("ar123"),
            ),
            User(
                email="nabeul@local",
                full_name="Nabeul",
                role="manager",
                hashed_password=get_password_hash("na123"),
            ),
        ]

        session.add_all(users)
        await session.commit()
        print("✅ Seed done: Zaher/Ariana/Nabeul created.")

if __name__ == "__main__":
    asyncio.run(seed())
