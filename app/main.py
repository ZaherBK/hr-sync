"""
Entry point for the HR Sync FastAPI application.

Defines the FastAPI app, includes API routers, mounts static files, sets up
Jinja2 templates, and provides simple HTML-based frontend pages for login,
employee management, attendance, and leave management. Also includes
session middleware for storing user sessions in cookies.
"""
import os
from datetime import timedelta, date as dt_date

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select

from .db import engine, Base, get_session
from .auth import authenticate_user, create_access_token, get_current_user
from .models import Attendance, Branch, Employee, Leave, User, Role
from .audit import latest
from .routers import users, branches, employees, attendance, leaves


APP_NAME = os.getenv("APP_NAME", "HR Sync")

app = FastAPI(title=APP_NAME)

# Include API routers
app.include_router(users.router)
app.include_router(branches.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(leaves.router)

# Set up static files and templates
BASE_DIR = os.path.dirname(__file__)
app.mount(
    "/static",
    StaticFiles(directory=os.path.join(BASE_DIR, "frontend", "static")),
    name="static",
)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "frontend", "templates"))
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "change_me"))


@app.on_event("startup")
async def on_startup() -> None:
    """Create database tables on application startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _get_user_from_session(request: Request) -> dict | None:
    """Retrieve the user dict stored in the session, if any."""
    return request.session.get("user")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    async with get_session() as db:
        activity = await latest(db)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "activity": activity,
            "app_name": APP_NAME,
        },
    )


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": None, "app_name": APP_NAME},
    )


@app.post("/login")
async def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
    async with get_session() as db:
        user = await authenticate_user(db, username, password)
        if not user:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Wrong email or password",
                    "app_name": APP_NAME,
                },
                status_code=400,
            )
        token = create_access_token(
            {"sub": str(user.id), "role": user.role.value, "branch_id": user.branch_id},
            expires_delta=timedelta(days=7),
        )
        request.session["user"] = {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role,
            "branch_id": user.branch_id,
            "token": token,
        }
        return RedirectResponse("/", status_code=302)


@app.get("/employees", response_class=HTMLResponse)
async def employees_page(request: Request):
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    async with get_session() as db:
        res = await db.execute(select(Employee))
        employees = res.scalars().all()
        resb = await db.execute(select(Branch))
        branches = resb.scalars().all()
    return templates.TemplateResponse(
        "employees.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "branches": branches,
            "app_name": APP_NAME,
        },
    )


@app.post("/employees/create")
async def employees_create(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    position: str = Form(...),
    branch_id: int = Form(...),
):
    user = _get_user_from_session(request)
    if not user or user["role"] not in ("admin", "manager"):
        return RedirectResponse("/login")
    async with get_session() as db:
        db.add(
            Employee(
                first_name=first_name,
                last_name=last_name,
                position=position,
                branch_id=int(branch_id),
            )
        )
        await db.commit()
    return RedirectResponse("/employees", status_code=302)


@app.get("/attendance", response_class=HTMLResponse)
async def attendance_page(request: Request):
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    async with get_session() as db:
        res = await db.execute(select(Employee))
        employees = res.scalars().all()
        resa = await db.execute(select(Attendance))
        attendance_records = resa.scalars().all()
    return templates.TemplateResponse(
        "attendance.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "attendance": attendance_records,
            "app_name": APP_NAME,
        },
    )


@app.post("/attendance/create")
async def attendance_create(
    request: Request,
    employee_id: int = Form(...),
    date: str = Form(...),
    atype: str = Form(...),
    note: str | None = Form(None),
):
    user = _get_user_from_session(request)
    if not user or user["role"] not in ("admin", "manager"):
        return RedirectResponse("/login")
    # Convert string date to date object
    day = dt_date.fromisoformat(date)
    async with get_session() as db:
        db.add(
            Attendance(
                employee_id=int(employee_id),
                date=day,
                atype=atype,
                note=note,
                created_by=user["id"],
            )
        )
        await db.commit()
    return RedirectResponse("/attendance", status_code=302)


@app.get("/leaves", response_class=HTMLResponse)
async def leaves_page(request: Request):
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login")
    async with get_session() as db:
        res = await db.execute(select(Employee))
        employees = res.scalars().all()
        resl = await db.execute(select(Leave))
        leaves = resl.scalars().all()
    return templates.TemplateResponse(
        "leaves.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "leaves": leaves,
            "app_name": APP_NAME,
        },
    )


@app.post("/leaves/create")
async def leaves_create(
    request: Request,
    employee_id: int = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    ltype: str = Form(...),
):
    user = _get_user_from_session(request)
    if not user or user["role"] not in ("admin", "manager"):
        return RedirectResponse("/login")
    sd = dt_date.fromisoformat(start_date)
    ed = dt_date.fromisoformat(end_date)
    if ed < sd:
        raise HTTPException(status_code=400, detail="Invalid date range")
    async with get_session() as db:
        db.add(
            Leave(
                employee_id=int(employee_id),
                start_date=sd,
                end_date=ed,
                ltype=ltype,
                created_by=user["id"],
            )
        )
        await db.commit()
    return RedirectResponse("/leaves", status_code=302)


@app.post("/leaves/{leave_id}/approve")
async def leaves_approve(leave_id: int, request: Request):
    user = _get_user_from_session(request)
    if not user or user["role"] not in ("admin", "manager"):
        return RedirectResponse("/login")
    async with get_session() as db:
        res = await db.execute(select(Leave).where(Leave.id == leave_id))
        lv = res.scalar_one_or_none()
        if lv:
            lv.approved = True
            await db.commit()
    return RedirectResponse("/leaves", status_code=302)