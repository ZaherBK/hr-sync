import asyncio
from sqlalchemy import delete

# FIX 1: Import from 'app.db', not 'app.database'
# FIX 2: Import 'AsyncSessionLocal' and rename it
from app.db import AsyncSessionLocal as async_session_maker, engine

# FIX 3: Your model file is 'app/models.py'
from app.models import Base, User, Role

# FIX 4: Your auth file has 'hash_password', not 'get_password_hash'
from app.auth import hash_password as get_password_hash

async def seed():
    async with engine.begin() as conn:
        # إنشاء الجداول لو مش موجودة
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as session:
        # حذف المستخدمين القدامى
        await session.execute(delete(User))

        # إنشاء المستخدمين الجدد
        users = [
            User(
                email="zaher@local",
                full_name="Zaher",
                # FIX 5: "owner" is not a valid role. Use "admin"
                role=Role.admin,
                hashed_password=get_password_hash("zah1405")
            ),
            User(
                email="ariana@local",
                full_name="Ariana",
                role=Role.manager,
                hashed_password=get_password_hash("ar123")
            ),
            User(
                email="nabeul@local",
                full_name="Nabeul",
                role=Role.manager,
                hashed_password=get_password_hash("na123")
            ),
        ]

        session.add_all(users)
        await session.commit()
        print("✅ 3 users created successfully!")

if __name__ == "__main__":
    # NOTE: This script must be moved to the ROOT folder (outside 'app/')
    # to run correctly with 'python seed.py'
    # If you keep it inside 'app/', you must run it with 'python -m app.seed'
    # from the root folder.
    asyncio.run(seed())