import asyncio
from app.database import async_session_maker, engine
from app.models import Base, User
from app.auth import get_password_hash
from sqlalchemy import delete

async def seed():
    async with engine.begin() as conn:
        # إنشاء الجداول لو مش موجودة
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        # حذف المستخدمين القدامى
        await session.execute(delete(User))

        # إنشاء المستخدمين الجدد
        users = [
            User(email="zaher@local", full_name="Zaher", role="owner", hashed_password=get_password_hash("zah1405")),
            User(email="ariana@local", full_name="Ariana", role="manager", hashed_password=get_password_hash("ar123")),
            User(email="nabeul@local", full_name="Nabeul", role="manager", hashed_password=get_password_hash("na123")),
        ]

        session.add_all(users)
        await session.commit()
        print("✅ 3 users created successfully!")

if __name__ == "__main__":
    asyncio.run(seed())
