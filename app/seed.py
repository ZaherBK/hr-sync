"""
Database seeding script.

This script creates the database schema and inserts some initial data such as
branches, an admin user, managers, and a couple of employees. Run this
module as a script to populate the database. It relies on the same
environment variables as the main application to locate the database.
"""
import asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from .db import engine, AsyncSessionLocal, Base
from .auth import hash_password
from .models import Branch, Employee, Role, User


async def run() -> None:
    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        # Create branches
        nabeul = Branch(name="Nabeul", city="Nabeul")
        tunis = Branch(name="Tunis", city="Tunis")
        session.add_all([nabeul, tunis])
        await session.flush()

        # Admin (global)
        admin = User(
            email="admin@example.com",
            full_name="Super Admin",
            role=Role.admin,
            branch_id=None,
            hashed_password=hash_password("admin123"),
        )
        # Managers
        manager_nabeul = User(
            email="manager.nabeul@example.com",
            full_name="Manager Nabeul",
            role=Role.manager,
            branch_id=nabeul.id,
            hashed_password=hash_password("manager123"),
        )
        manager_tunis = User(
            email="manager.tunis@example.com",
            full_name="Manager Tunis",
            role=Role.manager,
            branch_id=tunis.id,
            hashed_password=hash_password("manager123"),
        )
        session.add_all([admin, manager_nabeul, manager_tunis])

        # Employees
        session.add_all([
            Employee(first_name="Ali", last_name="Ben Salah", position="Sales", branch_id=nabeul.id),
            Employee(first_name="Sana", last_name="Trabelsi", position="Cashier", branch_id=tunis.id),
        ])

        await session.commit()


if __name__ == "__main__":
    asyncio.run(run())