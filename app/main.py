"""
Point d'entrée de l'application FastAPI pour la gestion RH de la Bijouterie.
"""
import os
from datetime import timedelta, date as dt_date, datetime
from decimal import Decimal
from typing import Annotated, List, Optional
import json
import enum # Ajout de l'import enum manquant

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status, APIRouter, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, delete, func, case, extract, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.future import select
from . import models, schemas
import io # Importé pour l'export

# --- CORRIGÉ : Import de get_db depuis .deps ---
from .db import engine, Base, AsyncSessionLocal
# --- CORRIGÉ : Import de hash_password ---
from .auth import authenticate_user, create_access_token, hash_password, ACCESS_TOKEN_EXPIRE_MINUTES, api_require_permission

# Importer TOUS les modèles nécessaires
from .models import (
    Attendance, AttendanceType, Branch, Deposit, Employee, Leave, User, Pay, PayType, AuditLog, LeaveType,
    Role, Loan, LoanSchedule, LoanRepayment
)
from .schemas import RoleCreate, RoleUpdate

from .audit import latest, log
from .routers import users, branches, employees as employees_api, attendance as attendance_api, leaves as leaves_api, deposits as deposits_api
# --- MODIFIÉ : Importer les nouvelles dépendances ---
from .deps import get_db, web_require_permission, get_current_session_user
# --- LOANS ---
from app.api import loans as loans_api
# Mettez à jour les imports de models et schemas
from app.models import Employee, Loan, LoanSchedule, LoanRepayment
from app.schemas import LoanCreate, RepaymentCreate
# --- FIN MODIFIÉ ---


APP_NAME = os.getenv("APP_NAME", "Bijouterie Zaher")

app = FastAPI(title=APP_NAME)

# --- 1. API Routers ---
app.include_router(users.router)
app.include_router(branches.router)
app.include_router(employees_api.router)
app.include_router(attendance_api.router)
app.include_router(leaves_api.router)
app.include_router(deposits_api.router)
app.include_router(loans_api.router)


# --- 2. Static/Templates Setup ---
BASE_DIR = os.path.dirname(__file__)
static_path = os.path.join(BASE_DIR, "frontend", "static")
templates_path = os.path.join(BASE_DIR, "frontend", "templates")

os.makedirs(static_path, exist_ok=True)
os.makedirs(templates_path, exist_ok=True)

app.mount(
    "/static",
    StaticFiles(directory=static_path),
    name="static",
)
templates = Jinja2Templates(directory=templates_path)
app.add_middleware(
    SessionMiddleware, 
    secret_key=os.getenv("SECRET_KEY", "une_cle_secrete_tres_longue_et_aleatoire"),
    max_age=int(ACCESS_TOKEN_EXPIRE_MINUTES) * 60
)

# 1. Create a NEW dependency to get the FULL database user
async def get_current_db_user(
    db: AsyncSession = Depends(get_db), 
    user_data: dict = Depends(get_current_session_user)
) -> models.User | None:
    
    if not user_data:
        return None
        
    user_email = user_data.get("email")
    if not user_email:
        return None

    result = await db.execute(
        select(models.User).options(selectinload(models.User.permissions)).where(models.User.email == user_email)
    )
    return result.scalar_one_or_none()


# --- 3. Startup Event (MODIFIÉ) ---
@app.on_event("startup")
async def on_startup() -> None:
    """Créer les tables de la base de données et ajouter les rôles/données initiaux."""
    print("Événement de démarrage...")
    async with engine.begin() as conn:
        print("Création de toutes les tables (si elles n'existent pas)...")
        await conn.run_sync(Base.metadata.create_all)
        print("Tables OK.")

    try:
        async with AsyncSessionLocal() as session:
            res_admin_role = await session.execute(select(Role).where(Role.name == "Admin"))
            admin_role = res_admin_role.scalar_one_or_none()
            
            if not admin_role:
                print("Base de données vide, ajout des rôles et utilisateurs initiaux (seed)...")
                
                admin_role = Role(
                    name="Admin", is_admin=True, can_manage_users=True, can_manage_roles=True,
                    can_manage_branches=True, can_view_settings=True, can_clear_logs=True,
                    can_manage_employees=True, can_view_reports=True, can_manage_pay=True,
                    can_manage_absences=True, can_manage_leaves=True, can_manage_deposits=True,
                    can_manage_loans=True
                )
                manager_role = Role(
                    name="Manager", is_admin=False, can_manage_users=False, can_manage_roles=False,
                    can_manage_branches=False, can_view_settings=False, can_clear_logs=False,
                    can_manage_employees=True, can_view_reports=False, can_manage_pay=True,
                    can_manage_absences=True, can_manage_leaves=True, can_manage_deposits=True,
                    can_manage_loans=True
                )
                session.add_all([admin_role, manager_role])
                await session.flush()

                res_branch = await session.execute(select(Branch).where(Branch.name == "Magasin Ariana"))
                branch_ariana = res_branch.scalar_one_or_none()
                
                if not branch_ariana:
                    print("Ajout des magasins par défaut...")
                    branch_ariana = Branch(name="Magasin Ariana", city="Ariana")
                    branch_nabeul = Branch(name="Magasin Nabeul", city="Nabeul")
                    session.add_all([branch_ariana, branch_nabeul])
                    await session.flush()
                else:
                    print("Magasins déjà présents, récupération...")
                    res_nabeul = await session.execute(select(Branch).where(Branch.name == "Magasin Nabeul"))
                    branch_nabeul = res_nabeul.scalar_one()

                res_admin_user = await session.execute(select(User).where(User.email == "zaher@local"))
                
                if res_admin_user.scalar_one_or_none() is None:
                    print("Ajout des utilisateurs initiaux...")
                    users_to_create = [
                        User(
                            email="zaher@local", full_name="Zaher (Admin)", role_id=admin_role.id,
                            hashed_password=hash_password("zah1405"), is_active=True, branch_id=None
                        ),
                        User(
                            email="ariana@local", full_name="Ariana (Manager)", role_id=manager_role.id,
                            hashed_password=hash_password("ar123"), is_active=True, branch_id=branch_ariana.id
                        ),
                        User(
                            email="nabeul@local", full_name="Nabeul (Manager)", role_id=manager_role.id,
                            hashed_password=hash_password("na123"), is_active=True, branch_id=branch_nabeul.id
                        ),
                    ]
                    session.add_all(users_to_create)
                    await session.commit()
                    print(f"✅ Rôles, Magasins et {len(users_to_create)} utilisateurs créés avec succès !")
                else:
                    print("Utilisateur admin déjà présent, commit des rôles/magasins si nécessaire.")
                    await session.commit()
            else:
                print("Données initiales déjà présentes. Seeding ignoré.")
    except Exception as e:
        print(f"Erreur pendant le seeding initial : {e}")
        await session.rollback()


# --- 4. Fonctions d'aide (Helper Functions) ---

def _serialize_permissions(role: Role | None) -> dict:
    """Convertit un objet Role en un dictionnaire de permissions pour la session."""
    if not role:
        return {}
    return role.to_dict()

# --- NOUVEAU : Helper pour l'export JSON ---
class CustomJSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        if isinstance(obj, (dt_date, datetime)):
            return obj.isoformat()
        if isinstance(obj, Base): # Gérer les objets SQLAlchemy
             return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        if isinstance(obj, enum.Enum):
            return obj.value
        return super().default(obj)
# --- FIN NOUVEAU ---


# --- 5. Routes des Pages Web (GET et POST) ---

@app.get("/", response_class=HTMLResponse, name="home")
async def home(
    request: Request,
    current_user: models.User = Depends(get_current_db_user) 
):
    if not current_user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    context = {
        "request": request,
        "user": current_user
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(request: Request, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(User).order_by(User.full_name))
    users = res.scalars().all()
    return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME, "users": users})


@app.post("/login", name="login_action")
async def login_action(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...),
    db: AsyncSession = Depends(get_db) 
):
    user = await authenticate_user(db, username, password)
    
    if not user:
        context = {
            "request": request, 
            "app_name": APP_NAME, 
            "error": "Email ou mot de passe incorrect."
        }
        return templates.TemplateResponse("login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    permissions_dict = _serialize_permissions(user.permissions)

    request.session["user"] = {
        "email": user.email,
        "id": user.id,
        "full_name": user.full_name,
        "branch_id": user.branch_id,
        "permissions": permissions_dict
    }
    
    return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)


@app.get("/logout", name="logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)


# --- Employés ---
@app.get("/employees", response_class=HTMLResponse, name="employees_page")
async def employees_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_employees"))
):
    branches_query = select(Branch)
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    
    manager_branch_id = None
    permissions = user.get("permissions", {})

    if not permissions.get("is_admin"):
        manager_branch_id = user.get("branch_id")
        branches_query = branches_query.where(Branch.id == manager_branch_id)
        employees_query = employees_query.where(Employee.branch_id == manager_branch_id)

    res_branches = await db.execute(branches_query)
    res_employees = await db.execute(employees_query)

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "employees": res_employees.scalars().all(),
        "branches": res_branches.scalars().all(),
        "manager_branch_id": manager_branch_id
    }
    return templates.TemplateResponse("employees.html", context)


@app.post("/employees/create", name="employees_create")
async def employees_create(
    request: Request,
    first_name: Annotated[str, Form()],
    last_name: Annotated[str, Form()],
    position: Annotated[str, Form()],
    branch_id: Annotated[int, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_employees")),
    cin: Annotated[str, Form()] = None,
    salary: Annotated[Decimal, Form()] = None
):
    permissions = user.get("permissions", {})
    
    if not permissions.get("is_admin") and user.get("branch_id") != branch_id:
        return RedirectResponse(request.url_for('employees_page'), status_code=status.HTTP_302_FOUND)
    
    if not permissions.get("is_admin"):
        salary = None

    if cin:
        res_cin = await db.execute(select(Employee).where(Employee.cin == cin))
        if res_cin.scalar_one_or_none():
            return RedirectResponse(request.url_for('employees_page'), status_code=status.HTTP_302_FOUND)

    new_employee = Employee(
        first_name=first_name, last_name=last_name, cin=cin or None,
        position=position, branch_id=branch_id, salary=salary, active=True
    )
    db.add(new_employee)
    await db.commit()
    await db.refresh(new_employee)

    await log(
        db, user['id'], "create", "employee", new_employee.id,
        new_employee.branch_id, f"Employé créé: {first_name} {last_name}"
    )

    return RedirectResponse(request.url_for('employees_page'), status_code=status.HTTP_302_FOUND)


# --- Absences (Attendance) ---
@app.get("/attendance", response_class=HTMLResponse, name="attendance_page")
async def attendance_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_absences"))
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    attendance_query = select(Attendance).order_by(Attendance.date.desc(), Attendance.created_at.desc())

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        branch_id = user.get("branch_id")
        employees_query = employees_query.where(Employee.branch_id == branch_id)
        attendance_query = attendance_query.join(Employee).where(Employee.branch_id == branch_id)

    res_employees = await db.execute(employees_query)
    res_attendance = await db.execute(attendance_query.limit(100))

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "employees": res_employees.scalars().all(),
        "attendance": res_attendance.scalars().all(),
        "today_date": dt_date.today().isoformat()
    }
    return templates.TemplateResponse("attendance.html", context)


@app.post("/attendance/create", name="attendance_create")
async def attendance_create(
    request: Request,
    employee_id: Annotated[int, Form()],
    date: Annotated[dt_date, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_absences")),
    note: Annotated[str, Form()] = None
):
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
        return RedirectResponse(request.url_for('attendance_page'), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('attendance_page'), status_code=status.HTTP_302_FOUND)
    
    new_attendance = Attendance(
        employee_id=employee_id, date=date, atype=AttendanceType.absent,
        note=note or None, created_by=user['id']
    )
    db.add(new_attendance)
    await db.commit()
    await db.refresh(new_attendance)

    await log(
        db, user['id'], "create", "attendance", new_attendance.id,
        employee.branch_id, f"Absence pour Employé ID={employee_id}, Date={date}"
    )

    return RedirectResponse(request.url_for('attendance_page'), status_code=status.HTTP_302_FOUND)


# --- Avances (Deposits) ---
@app.get("/deposits", response_class=HTMLResponse, name="deposits_page")
async def deposits_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_deposits"))
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    deposits_query = select(Deposit).order_by(Deposit.date.desc(), Deposit.created_at.desc())

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        branch_id = user.get("branch_id")
        employees_query = employees_query.where(Employee.branch_id == branch_id)
        deposits_query = deposits_query.join(Employee).where(Employee.branch_id == branch_id)

    res_employees = await db.execute(employees_query)
    res_deposits = await db.execute(deposits_query.limit(100))

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "employees": res_employees.scalars().all(),
        "deposits": res_deposits.scalars().all(),
        "today_date": dt_date.today().isoformat()
    }
    return templates.TemplateResponse("deposits.html", context)


@app.post("/deposits/create", name="deposits_create")
async def deposits_create(
    request: Request,
    employee_id: Annotated[int, Form()],
    amount: Annotated[Decimal, Form()],
    date: Annotated[dt_date, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_deposits")),
    note: Annotated[str, Form()] = None
):
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee or amount <= 0:
        return RedirectResponse(request.url_for('deposits_page'), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('deposits_page'), status_code=status.HTTP_302_FOUND)
    
    new_deposit = Deposit(
        employee_id=employee_id, amount=amount, date=date,
        note=note or None, created_by=user['id']
    )
    db.add(new_deposit)
    await db.commit()
    await db.refresh(new_deposit)

    await log(
        db, user['id'], "create", "deposit", new_deposit.id,
        employee.branch_id, f"Avance pour Employé ID={employee_id}, Montant={amount}"
    )

    return RedirectResponse(request.url_for('deposits_page'), status_code=status.HTTP_302_FOUND)


# --- Congés (Leaves) ---
@app.get("/leaves", response_class=HTMLResponse, name="leaves_page")
async def leaves_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_leaves"))
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    leaves_query = select(Leave).order_by(Leave.start_date.desc())

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        branch_id = user.get("branch_id")
        employees_query = employees_query.where(Employee.branch_id == branch_id)
        leaves_query = leaves_query.join(Employee).where(Employee.branch_id == branch_id)

    res_employees = await db.execute(employees_query)
    res_leaves = await db.execute(leaves_query.limit(100))

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "employees": res_employees.scalars().all(),
        "leaves": res_leaves.scalars().all(),
    }
    return templates.TemplateResponse("leaves.html", context)


@app.post("/leaves/create", name="leaves_create")
async def leaves_create(
    request: Request,
    employee_id: Annotated[int, Form()],
    start_date: Annotated[dt_date, Form()],
    end_date: Annotated[dt_date, Form()],
    ltype: Annotated[LeaveType, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_leaves"))
):
    if start_date > end_date:
        return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)

    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
        return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)

    new_leave = Leave(
        employee_id=employee_id, start_date=start_date, end_date=end_date,
        ltype=ltype, approved=False, created_by=user['id']
    )
    db.add(new_leave)
    await db.commit()
    await db.refresh(new_leave)

    await log(
        db, user['id'], "create", "leave", new_leave.id,
        employee.branch_id, f"Congé pour Employé ID={employee_id}, Type={ltype.value}"
    )

    return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)


@app.post("/leaves/{leave_id}/approve", name="leaves_approve")
async def leaves_approve(
    request: Request,
    leave_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_leaves"))
):
    res_leave = await db.execute(
        select(Leave).options(selectinload(Leave.employee)).where(Leave.id == leave_id)
    )
    leave = res_leave.scalar_one_or_none()

    if not leave or leave.approved:
        return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)
    
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != leave.employee.branch_id:
        return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)

    leave.approved = True
    await db.commit()

    await log(
        db, user['id'], "approve", "leave", leave.id,
        leave.employee.branch_id, f"Congé approuvé pour Employé ID={leave.employee_id}"
    )
    
    return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)


# --- Rapport Employé ---
@app.get("/employee-report", response_class=HTMLResponse, name="employee_report_index")
async def employee_report_index(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_view_reports")),
    employee_id: int | None = None
):
    res_employees = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.first_name))
    employees_list = res_employees.scalars().all()

    selected_employee = None
    pay_history = []
    deposits = []
    absences = []
    leaves = []
    loans = [] # Ajout des prêts au rapport

    if employee_id:
        res_selected = await db.execute(select(Employee).where(Employee.id == employee_id))
        selected_employee = res_selected.scalar_one_or_none()
        
        if selected_employee:
            res_pay = await db.execute(select(Pay).where(Pay.employee_id == employee_id).order_by(Pay.date.desc()))
            pay_history = res_pay.scalars().all()
            res_dep = await db.execute(select(Deposit).where(Deposit.employee_id == employee_id).order_by(Deposit.date.desc()))
            deposits = res_dep.scalars().all()
            res_abs = await db.execute(select(Attendance).where(Attendance.employee_id == employee_id).order_by(Attendance.date.desc()))
            absences = res_abs.scalars().all()
            res_lea = await db.execute(select(Leave).where(Leave.employee_id == employee_id).order_by(Leave.start_date.desc()))
            leaves = res_lea.scalars().all()
            res_loans = await db.execute(select(Loan).where(Loan.employee_id == employee_id).order_by(Loan.start_date.desc()))
            loans = res_loans.scalars().all()

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "employees": employees_list, "selected_employee": selected_employee,
        "pay_history": pay_history, "deposits": deposits,
        "absences": absences, "leaves": leaves, "loans": loans
    }
    return templates.TemplateResponse("employee_report.html", context)


# --- Payer Employé ---
@app.get("/pay-employee", response_class=HTMLResponse, name="pay_employee_page")
async def pay_employee_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_pay"))
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        employees_query = employees_query.where(Employee.branch_id == user.get("branch_id"))
    
    res_employees = await db.execute(employees_query)
    
    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "employees": res_employees.scalars().all(),
        "today_date": dt_date.today().isoformat()
    }
    return templates.TemplateResponse("pay_employee.html", context)


@app.post("/pay-employee", name="pay_employee_action")
async def pay_employee_action(
    request: Request,
    employee_id: Annotated[int, Form()],
    amount: Annotated[Decimal, Form()],
    date: Annotated[dt_date, Form()],
    pay_type: Annotated[PayType, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_pay")),
    note: Annotated[str, Form()] = None
):
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    
    if not employee or amount <= 0:
        return RedirectResponse(request.url_for('pay_employee_page'), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('pay_employee_page'), status_code=status.HTTP_302_FOUND)

    new_pay = Pay(
        employee_id=employee_id, amount=amount, date=date,
        pay_type=pay_type, note=note or None, created_by=user['id']
    )
    db.add(new_pay)
    await db.commit()
    await db.refresh(new_pay)

    await log(
        db, user['id'], "create", "pay", new_pay.id,
        employee.branch_id, f"Paiement pour Employé ID={employee_id}, Montant={amount}, Type={pay_type.value}"
    )

    return RedirectResponse(
        str(request.url_for('employee_report_index')) + f"?employee_id={employee_id}", 
        status_code=status.HTTP_302_FOUND
    )


# --- Gestion des Rôles ---
@app.get("/roles", response_class=HTMLResponse, name="roles_page")
async def roles_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_roles"))
):
    res_roles = await db.execute(
        select(Role).options(selectinload(Role.users)).order_by(Role.name)
    )
    
    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "roles": res_roles.scalars().unique().all()
    }
    return templates.TemplateResponse("roles.html", context)


@app.post("/roles/create", name="roles_create")
async def roles_create(
    request: Request,
    name: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_roles"))
):
    res_exist = await db.execute(select(Role).where(Role.name == name))
    if res_exist.scalar_one_or_none():
        return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)
        
    new_role = Role(name=name)
    db.add(new_role)
    
    await db.commit()
    await db.refresh(new_role)
    
    await log(
        db, user['id'], "create", "role", new_role.id,
        None, f"Rôle créé: {new_role.name}"
    )
    
    return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)


@app.post("/roles/{role_id}/update", name="roles_update")
async def roles_update(
    request: Request,
    role_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_roles"))
):
    res_role = await db.execute(select(Role).where(Role.id == role_id))
    role_to_update = res_role.scalar_one_or_none()
    
    if not role_to_update or role_to_update.is_admin:
        return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)
        
    form_data = await request.form()
    
    role_to_update.can_manage_users = "can_manage_users" in form_data
    role_to_update.can_manage_roles = "can_manage_roles" in form_data
    role_to_update.can_manage_branches = "can_manage_branches" in form_data
    role_to_update.can_view_settings = "can_view_settings" in form_data
    role_to_update.can_clear_logs = "can_clear_logs" in form_data
    role_to_update.can_manage_employees = "can_manage_employees" in form_data
    role_to_update.can_view_reports = "can_view_reports" in form_data
    role_to_update.can_manage_pay = "can_manage_pay" in form_data
    role_to_update.can_manage_absences = "can_manage_absences" in form_data
    role_to_update.can_manage_leaves = "can_manage_leaves" in form_data
    role_to_update.can_manage_deposits = "can_manage_deposits" in form_data
    role_to_update.can_manage_loans = "can_manage_loans" in form_data
    
    await db.commit()
    
    await log(
        db, user['id'], "update", "role", role_to_update.id,
        None, f"Permissions mises à jour pour le rôle: {role_to_update.name}"
    )
    
    return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)


@app.post("/roles/{role_id}/delete", name="roles_delete")
async def roles_delete(
    request: Request,
    role_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_roles"))
):
    res_role = await db.execute(
        select(Role).options(selectinload(Role.users)).where(Role.id == role_id)
    )
    role_to_delete = res_role.scalar_one_or_none()
    
    if not role_to_delete or role_to_delete.is_admin or len(role_to_delete.users) > 0:
        return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)
        
    role_name = role_to_delete.name
    await db.delete(role_to_delete)
    await db.commit()
    
    await log(
        db, user['id'], "delete", "role", role_id,
        None, f"Rôle supprimé: {role_name}"
    )
    
    return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)


# --- Gestion des Utilisateurs ---
@app.get("/users", response_class=HTMLResponse, name="users_page")
async def users_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users"))
):
    res_users = await db.execute(
        select(User).options(selectinload(User.branch), selectinload(User.permissions)).order_by(User.full_name)
    )
    res_branches = await db.execute(select(Branch).order_by(Branch.name))
    res_roles = await db.execute(select(Role).order_by(Role.name))

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "users": res_users.scalars().unique().all(),
        "branches": res_branches.scalars().all(),
        "roles": res_roles.scalars().all(),
    }
    return templates.TemplateResponse("users.html", context)


@app.post("/users/create", name="users_create")
async def users_create(
    request: Request,
    full_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role_id: Annotated[int, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users")),
    branch_id: Annotated[int, Form()] = None,
):
    res_exist = await db.execute(select(User).where(User.email == email))
    if res_exist.scalar_one_or_none():
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)
        
    res_role = await db.execute(select(Role).where(Role.id == role_id))
    role = res_role.scalar_one_or_none()
    if not role:
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)
        
    final_branch_id = branch_id
    if role.is_admin:
        final_branch_id = None

    new_user = User(
        full_name=full_name, email=email,
        hashed_password=hash_password(password),
        role_id=role_id, branch_id=final_branch_id, is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    await log(
        db, user['id'], "create", "user", new_user.id,
        new_user.branch_id, f"Utilisateur créé: {new_user.email} (Rôle: {role.name})"
    )
    
    return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)


@app.post("/users/{user_id}/update", name="users_update")
async def users_update(
    request: Request,
    user_id: int,
    full_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    role_id: Annotated[int, Form()],
    is_active: Annotated[bool, Form()] = False,
    branch_id: Annotated[int, Form()] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users")),
):
    res_user = await db.execute(select(User).options(selectinload(User.permissions)).where(User.id == user_id))
    user_to_update = res_user.scalar_one_or_none()
    if not user_to_update:
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)

    if user_to_update.email != email:
        res_exist = await db.execute(select(User).where(User.email == email, User.id != user_id))
        if res_exist.scalar_one_or_none():
            return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)
    
    res_role = await db.execute(select(Role).where(Role.id == role_id))
    role = res_role.scalar_one_or_none()
    if not role:
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)
        
    final_branch_id = branch_id
    if role.is_admin:
        final_branch_id = None
        
    if user_to_update.permissions.is_admin:
        if user_to_update.id == user['id'] and (not is_active or not role.is_admin):
             return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)

    user_to_update.full_name = full_name
    user_to_update.email = email
    user_to_update.role_id = role_id
    user_to_update.branch_id = final_branch_id
    user_to_update.is_active = is_active
    
    await db.commit()

    await log(
        db, user['id'], "update", "user", user_to_update.id,
        user_to_update.branch_id, f"Utilisateur mis à jour: {user_to_update.email}"
    )

    return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)


@app.post("/users/{user_id}/password", name="users_password")
async def users_password(
    request: Request,
    user_id: int,
    password: Annotated[str, Form()],
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users")),
):
    res_user = await db.execute(select(User).options(selectinload(User.permissions)).where(User.id == user_id))
    user_to_update = res_user.scalar_one_or_none()
    
    if not user_to_update or len(password) < 6:
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)

    user_to_update.hashed_password = hash_password(password)
    await db.commit()

    await log(
        db, user['id'], "update_password", "user", user_to_update.id,
        user_to_update.branch_id, f"Mot de passe réinitialisé pour: {user_to_update.email}"
    )

    return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)


@app.post("/users/{user_id}/delete", name="users_delete")
async def users_delete(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users")),
):
    if user['id'] == user_id:
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)

    res_user = await db.execute(select(User).where(User.id == user_id))
    user_to_delete = res_user.scalar_one_or_none()
    
    if not user_to_delete:
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)

    user_email = user_to_delete.email
    user_branch_id = user_to_delete.branch_id
    
    await db.delete(user_to_delete)
    await db.commit()

    await log(
        db, user['id'], "delete", "user", user_id,
        user_branch_id, f"Utilisateur supprimé: {user_email}"
    )

    return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)


# --- Page Paramètres ---
@app.get("/settings", response_class=HTMLResponse, name="settings_page")
async def settings_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_view_settings"))
):
    permissions = user.get("permissions", {})
    filtered_logs = await latest(
        db,
        user_is_admin=permissions.get("is_admin", False),
        branch_id=user.get("branch_id"),
        entity_types=["leave", "attendance", "deposit", "pay"]
    )

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "logs": filtered_logs
    }
    return templates.TemplateResponse("settings.html", context)


# --- 6. Route de Nettoyage (Corrigée) ---
@app.post("/settings/clear-logs", name="clear_logs")
async def clear_transaction_logs(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_clear_logs"))
):
    print(f"ACTION ADMIN (user {user['id']}): Nettoyage des journaux...")

    try:
        await db.execute(delete(AuditLog))
        await db.execute(delete(Attendance))
        await db.execute(delete(Leave))
        await db.execute(delete(Deposit))
        await db.execute(delete(Pay))
        
        await db.commit()
        print("✅ Nettoyage des journaux terminé avec succès.")
        
        await log(
            db, user['id'], "delete", "all_logs", None,
            None, "Toutes les données transactionnelles ont été supprimées."
        )
        await db.commit()
        
    except Exception as e:
        await db.rollback()
        print(f"ERREUR lors du nettoyage des journaux: {e}")

    return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)

#
# --- NOUVEAU : FONCTIONNALITÉS DE BACKUP / RESTORE ---
#
@app.get("/settings/export", name="export_data")
async def export_data(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("is_admin")) # Admin seulement
):
    """Exporte toutes les données de la base de données en JSON."""
    
    data_to_export = {}
    
    try:
        # Exporter chaque table
        data_to_export["branches"] = (await db.execute(select(Branch))).scalars().all()
        # Ne pas exporter les mots de passe hashés
        users_raw = (await db.execute(select(User))).scalars().all()
        data_to_export["users"] = [{col.name: getattr(u, col.name) for col in User.__table__.columns if col.name != 'hashed_password'} for u in users_raw]
        
        data_to_export["employees"] = (await db.execute(select(Employee))).scalars().all()
        data_to_export["attendance"] = (await db.execute(select(Attendance))).scalars().all()
        data_to_export["leaves"] = (await db.execute(select(Leave))).scalars().all()
        data_to_export["deposits"] = (await db.execute(select(Deposit))).scalars().all()
        data_to_export["pay_history"] = (await db.execute(select(Pay))).scalars().all()
        data_to_export["loans"] = (await db.execute(select(Loan))).scalars().all()
        data_to_export["loan_schedules"] = (await db.execute(select(LoanSchedule))).scalars().all()
        data_to_export["loan_repayments"] = (await db.execute(select(LoanRepayment))).scalars().all()
        
        # Les Rôles sont gérés par le seed, mais exportons-les pour référence
        data_to_export["roles"] = (await db.execute(select(Role))).scalars().all()

        
    except Exception as e:
        print(f"Erreur pendant l'export: {e}")
        return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)

    # Créer un fichier JSON en mémoire
    json_data = json.dumps(data_to_export, cls=CustomJSONEncoder, indent=2)
    file_stream = io.BytesIO(json_data.encode("utf-8"))
    
    filename = f"backup_bijouterie_zaher_{dt_date.today().isoformat()}.json"
    
    return StreamingResponse(
        file_stream, 
        media_type="application/json", 
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.post("/settings/import", name="import_data")
async def import_data(
    request: Request,
    backup_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("is_admin")) # Admin seulement
):
    """Importe et restaure les données depuis un fichier JSON."""
    
    if not backup_file.filename.endswith(".json"):
        # Gérer l'erreur de type de fichier
        return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)

    try:
        contents = await backup_file.read()
        data = json.loads(contents.decode("utf-8"))

        # --- DANGER : SUPPRESSION DES DONNÉES ---
        # Supprimer dans l'ordre inverse des dépendances
        await db.execute(delete(AuditLog))
        await db.execute(delete(LoanRepayment))
        await db.execute(delete(LoanSchedule))
        await db.execute(delete(Loan))
        await db.execute(delete(Pay))
        await db.execute(delete(Deposit))
        await db.execute(delete(Leave))
        await db.execute(delete(Attendance))
        await db.execute(delete(Employee))
        await db.execute(delete(User))
        await db.execute(delete(Branch))
        # Ne pas supprimer les Rôles, car ils sont fondamentaux
        
        # --- RÉINSERTION DES DONNÉES ---
        # (Note : ceci ne gère pas les conflits d'ID, suppose une base vide)
        
        if "branches" in data:
            for item in data["branches"]:
                db.add(Branch(**item))
        await db.flush() # Pour que les ID de Branch soient dispo

        # --- FIX: Gérer le mot de passe manquant ---
        if "users" in data:
            # Créer un mot de passe par défaut pour tous les utilisateurs importés.
            # Ils devront utiliser "password123" pour se connecter.
            default_hashed_password = hash_password("password123")

            for user_data in data["users"]:
                
                # Le fichier JSON n'a pas de 'hashed_password' car il a été exporté sans.
                # Nous devons l'ajouter manuellement.
                user_data['hashed_password'] = default_hashed_password
                
                # Maintenant, nous pouvons créer l'utilisateur
                db.add(User(**user_data))
        # --- FIN DU FIX ---
        
        if "employees" in data:
            for item in data["employees"]:
                db.add(Employee(**item))
        
        await db.flush() # IDs d'employé dispo

        if "attendance" in data:
            for item in data["attendance"]:
                db.add(Attendance(**item))
        if "leaves" in data:
            for item in data["leaves"]:
                db.add(Leave(**item))
        if "deposits" in data:
            for item in data["deposits"]:
                db.add(Deposit(**item))
        if "pay_history" in data:
            for item in data["pay_history"]:
                db.add(Pay(**item))
        
        if "loans" in data:
            for item in data["loans"]:
                db.add(Loan(**item))
        await db.flush() # IDs de prêt dispo
        
        if "loan_schedules" in data:
            for item in data["loan_schedules"]:
                db.add(LoanSchedule(**item))
        
        if "loan_repayments" in data:
            for item in data["loan_repayments"]:
                db.add(LoanRepayment(**item))

        await db.commit()
        
    except Exception as e:
        await db.rollback()
        print(f"ERREUR lors de l'import: {e}")
        # Idéalement : ajouter un message d'erreur flash
    
    return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)


#
# --- SECTION DES PRÊTS (WEB) ---
#

@app.get("/loans", name="loans_page")
async def loans_page(request: Request, db: AsyncSession = Depends(get_db), user: dict = Depends(web_require_permission("can_manage_loans"))):
    employees = (await db.execute(select(Employee).where(Employee.active==True).order_by(Employee.first_name))).scalars().all()
    
    loans = (await db.execute(
        select(Loan).options(selectinload(Loan.employee))
        .order_by(Loan.created_at.desc()).limit(200)
    )).scalars().all()
    
    return templates.TemplateResponse("loans.html", {"request": request, "user": user, "app_name": APP_NAME, "employees": employees, "loans": loans})

@app.post("/loans/create", name="loans_create_web")
async def loans_create_web(
    request: Request,
    employee_id: Annotated[int, Form()],
    principal: Annotated[Decimal, Form()],
    # --- FIX LOGIQUE PRÊT: Termes non pertinents, mais gardés pour compatibilité API ---
    term_count: Annotated[int, Form()] = 1,
    term_unit: Annotated[str, Form()] = "month",
    start_date: Annotated[dt_date, Form()] = dt_date.today(),
    first_due_date: Annotated[dt_date | None, Form()] = None,
    # --- NOUVEAU: Ajout du champ notes ---
    notes: Annotated[str, Form()] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_loans")),
):
    payload = LoanCreate(
        employee_id=employee_id, principal=principal, interest_type="none", 
        annual_interest_rate=None, term_count=term_count, term_unit=term_unit,
        start_date=start_date, first_due_date=first_due_date, fee=None
        # Note: Le payload de l'API n'a pas de champ 'notes'
    )
    from app.api.loans import create_loan
    
    # --- FIX: L'API create_loan ne gère pas 'notes', l'ajouter manuellement ---
    new_loan = await create_loan(payload, db, user)
    if new_loan and notes:
        new_loan.notes = notes
        await db.commit()
    # --- FIN DU FIX ---
    
    return RedirectResponse(request.url_for("loans_page"), status_code=status.HTTP_302_FOUND)


@app.get("/loan/{loan_id}", response_class=HTMLResponse, name="loan_detail_page")
async def loan_detail_page(
    request: Request,
    loan_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_loans"))
):
    """Affiche la page de détails d'un prêt."""
    
    # --- CORRECTION ---
    # Le tri est maintenant géré par les modèles (app/models.py).
    # Nous avons juste besoin de charger les relations avec 'selectinload'.
    loan = (await db.execute(
        select(Loan)
        .options(
            selectinload(Loan.employee), 
            selectinload(Loan.schedules),  # Simplifié
            selectinload(Loan.repayments)   # Simplifié
        )
        .where(Loan.id == loan_id)
    )).scalar_one_or_none()

    if not loan:
        return RedirectResponse(request.url_for("loans_page"), status_code=status.HTTP_302_FOUND)
        
    today_date = dt_date.today().isoformat()

    return templates.TemplateResponse(
        "loan_detail.html", 
        {
            "request": request, 
            "user": user, 
            "app_name": APP_NAME, 
            "loan": loan,
            "today_date": today_date
        }
    )

# --- NOUVEAU : Route pour supprimer un prêt ---
@app.post("/loan/{loan_id}/delete", name="loan_delete_web")
async def loan_delete_web(
    request: Request,
    loan_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_loans")) # Ou 'is_admin'
):
    """Supprime un prêt, ses échéances et ses remboursements."""
    
    loan = (await db.execute(
        select(Loan).options(selectinload(Loan.employee)).where(Loan.id == loan_id)
    )).scalar_one_or_none()

    if loan:
        try:
            # La suppression en cascade est gérée par app/models.py
            await db.delete(loan)
            await db.commit()
            
            await log(
                db, user['id'], "delete", "loan", loan_id,
                loan.employee.branch_id, f"Prêt supprimé pour l'employé ID={loan.employee_id}"
            )
        except Exception as e:
            await db.rollback()
            print(f"Erreur lors de la suppression du prêt: {e}")

    return RedirectResponse(request.url_for("loans_page"), status_code=status.HTTP_302_FOUND)
# --- FIN NOUVEAU ---
    
@app.post("/loan/{loan_id}/repay", name="loan_repay_web")
async def loan_repay_web(
    request: Request,
    loan_id: int,
    amount: Annotated[Decimal, Form()],
    paid_on: Annotated[dt_date, Form()],
    notes: Annotated[str, Form()] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_loans"))
):
    """Traite le formulaire de remboursement depuis la page web."""

    payload = schemas.RepaymentCreate(
        amount=amount, paid_on=paid_on, source="cash", 
        notes=notes, schedule_id=None 
    )
    
    try:
        # L'API (repay) gère déjà la logique de paiement flexible/partiel
        await loans_api.repay(loan_id=loan_id, payload=payload, db=db, user=user)
    except HTTPException as e:
        print(f"Erreur lors du remboursement: {e.detail}")
    
    return RedirectResponse(
        request.url_for("loan_detail_page", loan_id=loan_id), 
        status_code=status.HTTP_302_FOUND
    )

