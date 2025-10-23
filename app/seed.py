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
        # Rename the second branch to Ariana to match the manager
        ariana_branch = Branch(name="Ariana", city="Ariana")
        session.add_all([nabeul, ariana_branch])
        await session.flush()

        # Owner (Zaher)
        zaher = User(
            email="zaher",
            full_name="Zaher",
            role=Role.admin,
            branch_id=None,
            hashed_password=hash_password("zah1405"),
        )
        # Managers with custom usernames and passwords
        manager_nabeul = User(
            email="nabeul",
            full_name="Manager Nabeul",
            role=Role.manager,
            branch_id=nabeul.id,
            hashed_password=hash_password("na123"),
        )
        manager_ariana = User(
            email="ariana",
            full_name="Manager Ariana",
            role=Role.manager,
            branch_id=ariana_branch.id,
            hashed_password=hash_password("ar123"),
        )
        session.add_all([zaher, manager_nabeul, manager_ariana])

        # Employees
        session.add_all([
            Employee(first_name="Ali", last_name="Ben Salah", position="Sales", branch_id=nabeul.id),
            Employee(first_name="Sana", last_name="Trabelsi", position="Cashier", branch_id=tunis.id),
        ])

        await session.commit()


if __name__ == "__main__":
    asyncio.run(run())