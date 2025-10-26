"""
Point d'entrée de l'application FastAPI pour la gestion RH de la Bijouterie.
"""
import os
from datetime import timedelta, date as dt_date, datetime
from decimal import Decimal
from typing import Annotated, List, Optional
import json

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status, APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, delete, func, case, extract, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, subqueryload
from sqlalchemy.future import select
from . import models, schemas

# --- CORRIGÉ : Import de get_db depuis .deps ---
from .db import engine, Base, AsyncSessionLocal
from .auth import authenticate_user, create_access_token, hash_password, ACCESS_TOKEN_EXPIRE_MINUTES, api_require_permission

# Importer TOUS les modèles nécessaires
from .models import (
    Attendance, AttendanceType, Branch, Deposit, Employee, Leave, User, Pay, PayType, AuditLog, LeaveType,
    Role
)
from .schemas import RoleCreate, RoleUpdate

from .audit import latest, log
from .routers import users, branches, employees as employees_api, attendance as attendance_api, leaves as leaves_api, deposits as deposits_api
# --- MODIFIÉ : Importer les nouvelles dépendances ---
from .deps import get_db, web_require_permission, get_current_session_user
# --- LOANS ---
from app.api import loans as loans_api
from app.models import Employee, Loan, LoanSchedule, LoanRepayment # <--- Imports Loan
from app.schemas import LoanCreate, RepaymentCreate # <--- Imports Schema
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
    user_data: dict = Depends(get_current_session_user) # <--- FIX 1
) -> models.User | None:
    
    if not user_data:
        return None
        
    # Get the email from the session dict
    user_email = user_data.get("email") # <--- FIX 2
    if not user_email:
        return None

    # This is the query that fetches the User AND its related Role
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
        print("!!! ATTENTION : Suppression de toutes les tables... !!!")
        # await conn.run_sync(Base.metadata.drop_all)      # <--- Commentez ou supprimez ceci après le premier test
        print("Création de toutes les tables (si elles n'existent pas)...")
        await conn.run_sync(Base.metadata.create_all)
        print("Tables OK.")

    # --- Logique de Seeding (Fortement modifiée) ---
    try:
        async with AsyncSessionLocal() as session:
            # --- 1. Vérifier si le rôle Admin existe ---
            res_admin_role = await session.execute(select(Role).where(Role.name == "Admin"))
            admin_role = res_admin_role.scalar_one_or_none()
            
            if not admin_role:
                print("Base de données vide, ajout des rôles et utilisateurs initiaux (seed)...")
                
                # --- 2. Créer les Rôles par défaut ---
                admin_role = Role(
                    name="Admin",
                    is_admin=True, # God Mode
                    can_manage_users=True,
                    can_manage_roles=True,
                    can_manage_branches=True,
                    can_view_settings=True,
                    can_clear_logs=True,
                    can_manage_employees=True,
                    can_view_reports=True,
                    can_manage_pay=True,
                    can_manage_absences=True,
                    can_manage_leaves=True,
                    can_manage_deposits=True,
                    can_manage_loans=True
                )
                manager_role = Role(
                    name="Manager",
                    is_admin=False,
                    can_manage_users=False, 
                    can_manage_roles=False,
                    can_manage_branches=False,
                    can_view_settings=False,
                    can_clear_logs=False,
                    can_manage_employees=True,
                    can_view_reports=False,
                    can_manage_pay=True,
                    can_manage_absences=True,
                    can_manage_leaves=True,
                    can_manage_deposits=True,
                    can_manage_loans=True
                )
                session.add_all([admin_role, manager_role])
                await session.flush() # Pour obtenir les IDs

                #
                # --- 3. VÉRIFIER ET CRÉER LES MAGASINS (C'EST LA CORRECTION) ---
                #
                res_branch = await session.execute(select(Branch).where(Branch.name == "Magasin Ariana"))
                branch_ariana = res_branch.scalar_one_or_none()
                
                if not branch_ariana:
                    print("Ajout des magasins par défaut...")
                    branch_ariana = Branch(name="Magasin Ariana", city="Ariana")
                    branch_nabeul = Branch(name="Magasin Nabeul", city="Nabeul")
                    session.add_all([branch_ariana, branch_nabeul])
                    await session.flush() # Pour obtenir les IDs
                else:
                    print("Magasins déjà présents, récupération...")
                    # Récupérer l'autre magasin pour être sûr
                    res_nabeul = await session.execute(select(Branch).where(Branch.name == "Magasin Nabeul"))
                    branch_nabeul = res_nabeul.scalar_one()
                #
                # --- FIN DE LA CORRECTION ---
                #

                # --- 4. Créer les Utilisateurs initiaux ---
                # Vérifier si l'admin existe
                res_admin_user = await session.execute(select(User).where(User.email == "zaher@local"))
                
                if res_admin_user.scalar_one_or_none() is None:
                    print("Ajout des utilisateurs initiaux...")
                    users_to_create = [
                        User(
                            email="zaher@local",
                            full_name="Zaher (Admin)",
                            role_id=admin_role.id, # Assigner l'ID du rôle
                            hashed_password=hash_password("zah1405"),
                            is_active=True,
                            branch_id=None
                        ),
                        User(
                            email="ariana@local",
                            full_name="Ariana (Manager)",
                            role_id=manager_role.id, # Assigner l'ID du rôle
                            hashed_password=hash_password("ar123"),
                            is_active=True,
                            branch_id=branch_ariana.id
                        ),
                        User(
                            email="nabeul@local",
                            full_name="Nabeul (Manager)",
                            role_id=manager_role.id, # Assigner l'ID du rôle
                            hashed_password=hash_password("na123"),
                            is_active=True,
                            branch_id=branch_nabeul.id
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
        await session.rollback() # Annuler les changements en cas d'erreur


# --- 4. Fonctions d'aide (Helper Functions) ---

# --- NOUVELLE FONCTION D'AIDE ---
def _serialize_permissions(role: Role | None) -> dict:
    """Convertit un objet Role en un dictionnaire de permissions pour la session."""
    if not role:
        return {} # Pas de permissions si pas de rôle
    return role.to_dict()
# --- FIN NOUVELLE FONCTION ---


# --- 5. Routes des Pages Web (GET et POST) ---

# --- CORRIGÉ : Utilise @app.get au lieu de @router.get ---
@app.get("/", response_class=HTMLResponse, name="home")
async def home(
    request: Request,
    # Use the NEW dependency here instead of the old one
    current_user: models.User = Depends(get_current_db_user) 
):
    if not current_user:
        # If no user, redirect to login
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    # 'current_user' is now the FULL SQLAlchemy object, not a dict.
    # 'current_user.permissions' will exist.
    context = {
        "request": request,
        "user": current_user  # Pass the full object to the template
    }
    
    # This line (from your error log) will now work
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
    """Traite la soumission du formulaire de connexion."""
    
    # --- MODIFIÉ : authenticate_user charge maintenant le rôle (défini dans auth.py) ---
    user = await authenticate_user(db, username, password)
    # --- FIN MODIFIÉ ---
    
    if not user:
        context = {
            "request": request, 
            "app_name": APP_NAME, 
            "error": "Email ou mot de passe incorrect."
        }
        return templates.TemplateResponse("login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    # --- MODIFIÉ : Création de la session avec permissions ---
    # NOTE: Assurez-vous que votre modèle User a 'permissions' comme nom de relation
    permissions_dict = _serialize_permissions(user.permissions) # <--- CHANGÉ de user.role à user.permissions

    request.session["user"] = {
        "email": user.email,
        "id": user.id,
        "full_name": user.full_name,
        "branch_id": user.branch_id,
        "permissions": permissions_dict # Stocke toutes les permissions
    }
    # --- FIN MODIFIÉ ---
    
    return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)


@app.get("/logout", name="logout")
async def logout(request: Request):
    """Déconnecte l'utilisateur en vidant la session."""
    request.session.clear()
    return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)


# --- 
# --- Routes principales (Employés, Absences, etc.) Mises à Jour
# ---

# --- Employés ---
@app.get("/employees", response_class=HTMLResponse, name="employees_page")
async def employees_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_employees"))
):
    """Affiche la page de gestion des employés."""
    # --- FIN MODIFIÉ ---

    branches_query = select(Branch)
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    
    manager_branch_id = None
    permissions = user.get("permissions", {})

    # Si ce n'est pas un admin, filtre par son magasin
    if not permissions.get("is_admin"):
        manager_branch_id = user.get("branch_id")
        branches_query = branches_query.where(Branch.id == manager_branch_id)
        employees_query = employees_query.where(Employee.branch_id == manager_branch_id)

    res_branches = await db.execute(branches_query)
    res_employees = await db.execute(employees_query)

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_employees")),
    # --- FIN MODIFIÉ ---
    cin: Annotated[str, Form()] = None,
    salary: Annotated[Decimal, Form()] = None
):
    """Crée un nouvel employé."""
    
    permissions = user.get("permissions", {})
    
    # Sécurité : Seul un admin (ou permission équivalente) peut assigner à un autre magasin
    if not permissions.get("is_admin") and user.get("branch_id") != branch_id:
         return RedirectResponse(request.url_for('employees_page'), status_code=status.HTTP_302_FOUND)
    
    # Sécurité : Seul un admin peut définir un salaire (ou nouvelle permission)
    if not permissions.get("is_admin"): # A l'avenir: 'can_set_salary'
        salary = None

    if cin:
        res_cin = await db.execute(select(Employee).where(Employee.cin == cin))
        if res_cin.scalar_one_or_none():
            return RedirectResponse(request.url_for('employees_page'), status_code=status.HTTP_302_FOUND)

    new_employee = Employee(
        first_name=first_name,
        last_name=last_name,
        cin=cin or None,
        position=position,
        branch_id=branch_id,
        salary=salary,
        active=True
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_absences"))
):
    """Affiche la page des absences."""
    # --- FIN MODIFIÉ ---

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
        "request": request,
        "user": user,
        "app_name": APP_NAME,
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_absences")),
    # --- FIN MODIFIÉ ---
    note: Annotated[str, Form()] = None
):
    """Enregistre une nouvelle absence."""
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
        return RedirectResponse(request.url_for('attendance_page'), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('attendance_page'), status_code=status.HTTP_302_FOUND)
    
    new_attendance = Attendance(
        employee_id=employee_id,
        date=date,
        atype=AttendanceType.absent,
        note=note or None,
        created_by=user['id']
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_deposits"))
):
    """Affiche la page des avances."""
    # --- FIN MODIFIÉ ---

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
        "request": request,
        "user": user,
        "app_name": APP_NAME,
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_deposits")),
    # --- FIN MODIFIÉ ---
    note: Annotated[str, Form()] = None
):
    """Enregistre une nouvelle avance."""
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee or amount <= 0:
        return RedirectResponse(request.url_for('deposits_page'), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('deposits_page'), status_code=status.HTTP_302_FOUND)
    
    new_deposit = Deposit(
        employee_id=employee_id,
        amount=amount,
        date=date,
        note=note or None,
        created_by=user['id']
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_leaves"))
):
    """Affiche la page des congés."""
    # --- FIN MODIFIÉ ---

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
        "request": request,
        "user": user,
        "app_name": APP_NAME,
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_leaves"))
):
    """Crée une demande de congé."""
    # --- FIN MODIFIÉ ---

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
        employee_id=employee_id,
        start_date=start_date,
        end_date=end_date,
        ltype=ltype,
        approved=False, 
        created_by=user['id']
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
    # --- MODIFIÉ : Nouvelle sécurité (on pourrait créer 'can_approve_leaves') ---
    user: dict = Depends(web_require_permission("can_manage_leaves"))
):
    """Approuve un congé."""
    # --- FIN MODIFIÉ ---

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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_view_reports")),
    # --- FIN MODIFIÉ ---
    employee_id: int | None = None
):
    """Affiche la page de rapport d'un employé."""

    res_employees = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.first_name))
    employees_list = res_employees.scalars().all()

    selected_employee = None
    pay_history = []
    deposits = []
    absences = []
    leaves = []

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

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
        "employees": employees_list,
        "selected_employee": selected_employee,
        "pay_history": pay_history,
        "deposits": deposits,
        "absences": absences,
        "leaves": leaves
    }
    return templates.TemplateResponse("employee_report.html", context)


# --- Payer Employé ---
@app.get("/pay-employee", response_class=HTMLResponse, name="pay_employee_page")
async def pay_employee_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_pay"))
):
    """Affiche la page pour enregistrer un paiement."""
    # --- FIN MODIFIÉ ---

    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        employees_query = employees_query.where(Employee.branch_id == user.get("branch_id"))
    
    res_employees = await db.execute(employees_query)
    
    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
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
    # --- MODIFIÉ : Nouvelle sécurité ---
    user: dict = Depends(web_require_permission("can_manage_pay")),
    # --- FIN MODIFIÉ ---
    note: Annotated[str, Form()] = None
):
    """Enregistre un nouveau paiement."""
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    
    if not employee or amount <= 0:
        return RedirectResponse(request.url_for('pay_employee_page'), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('pay_employee_page'), status_code=status.HTTP_302_FOUND)

    new_pay = Pay(
        employee_id=employee_id,
        amount=amount,
        date=date,
        pay_type=pay_type,
        note=note or None,
        created_by=user['id']
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


# --- 
# --- ❗️❗️ DÉBUT DES ROUTES DE GESTION (Rôles & Utilisateurs) ❗️❗️
# ---

# --- Gestion des Rôles (NOUVEAU) ---
@app.get("/roles", response_class=HTMLResponse, name="roles_page")
async def roles_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_roles"))
):
    """Affiche la page de gestion des Rôles (Admin seulement)."""
    
    res_roles = await db.execute(
        select(Role).options(selectinload(Role.users)).order_by(Role.name)
    )
    
    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
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
    """Crée un nouveau rôle."""
    
    res_exist = await db.execute(select(Role).where(Role.name == name))
    if res_exist.scalar_one_or_none():
        # Gérer erreur
        return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)
        
    new_role = Role(name=name) # Crée avec les permissions par défaut (False)
    db.add(new_role)
    
    await db.commit()
    await db.refresh(new_role)
    
    await log(
        db, user['id'], "create", "role", new_role.id,
        None, f"Rôle créé: {new_role.name}"
    )
    
    # Redirige vers la page des rôles pour qu'ils puissent modifier le nouveau rôle
    return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)


@app.post("/roles/{role_id}/update", name="roles_update")
async def roles_update(
    request: Request,
    role_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_roles"))
):
    """Met à jour les permissions d'un rôle."""
    
    res_role = await db.execute(select(Role).where(Role.id == role_id))
    role_to_update = res_role.scalar_one_or_none()
    
    if not role_to_update or role_to_update.is_admin: # Ne peut pas modifier le rôle Admin
        return RedirectResponse(request.url_for('roles_page'), status_code=status.HTTP_302_FOUND)
        
    # Récupérer les données du formulaire
    form_data = await request.form()
    
    # Mettre à jour chaque permission basée sur la présence de la checkbox
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
    """Supprime un rôle (si aucun utilisateur ne l'utilise)."""
    
    res_role = await db.execute(
        select(Role).options(selectinload(Role.users)).where(Role.id == role_id)
    )
    role_to_delete = res_role.scalar_one_or_none()
    
    # Sécurité : Ne peut pas supprimer le rôle Admin ou un rôle utilisé
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


# --- Gestion des Utilisateurs (MODIFIÉ) ---
@app.get("/users", response_class=HTMLResponse, name="users_page")
async def users_page(
    request: Request, 
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users"))
):
    """Affiche la page de gestion des utilisateurs (Admin seulement)."""
    
    res_users = await db.execute(
        select(User).options(selectinload(User.branch), selectinload(User.permissions)).order_by(User.full_name) # <--- Utilise 'permissions'
    )
    res_branches = await db.execute(select(Branch).order_by(Branch.name))
    res_roles = await db.execute(select(Role).order_by(Role.name))

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
        "users": res_users.scalars().unique().all(),
        "branches": res_branches.scalars().all(),
        "roles": res_roles.scalars().all(), # Passer les rôles au template
    }
    return templates.TemplateResponse("users.html", context)


@app.post("/users/create", name="users_create")
async def users_create(
    request: Request,
    full_name: Annotated[str, Form()],
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    role_id: Annotated[int, Form()], # MODIFIÉ
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users")),
    branch_id: Annotated[int, Form()] = None,
):
    """Crée un nouvel utilisateur."""
    res_exist = await db.execute(select(User).where(User.email == email))
    if res_exist.scalar_one_or_none():
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)
        
    # Logique de validation du rôle (un admin ne devrait pas avoir de magasin)
    res_role = await db.execute(select(Role).where(Role.id == role_id))
    role = res_role.scalar_one_or_none()
    if not role:
        return RedirectResponse(request.url_for('users_page'), status_code=status.HTTP_302_FOUND)
        
    final_branch_id = branch_id
    if role.is_admin:
        final_branch_id = None # Les Admins ne sont pas liés à un magasin

    new_user = User(
        full_name=full_name,
        email=email,
        hashed_password=hash_password(password),
        role_id=role_id,
        branch_id=final_branch_id,
        is_active=True
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
    role_id: Annotated[int, Form()], # MODIFIÉ
    is_active: Annotated[bool, Form()] = False,
    branch_id: Annotated[int, Form()] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_users")),
):
    """Met à jour un utilisateur."""
    res_user = await db.execute(select(User).options(selectinload(User.permissions)).where(User.id == user_id)) # <--- Utilise 'permissions'
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
        final_branch_id = None # Les Admins ne sont pas liés à un magasin
        
    # Empêcher le dernier admin de se désactiver ou de changer son rôle
    if user_to_update.permissions.is_admin: # <--- Utilise 'permissions'
        if user_to_update.id == user['id'] and (not is_active or not role.is_admin):
             # L'admin essaie de se désactiver ou de s'enlever le rôle admin
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
    """Réinitialise le mot de passe d'un utilisateur."""
    res_user = await db.execute(select(User).options(selectinload(User.permissions)).where(User.id == user_id)) # <--- Utilise 'permissions'
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
    """Supprime un utilisateur."""
    if user['id'] == user_id: # Ne peut pas se supprimer soi-même
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
    """Affiche la page des paramètres."""
    
    permissions = user.get("permissions", {})
    filtered_logs = await latest(
        db,
        user_is_admin=permissions.get("is_admin", False),
        branch_id=user.get("branch_id"),
        entity_types=["leave", "attendance", "deposit", "pay"]
    )

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
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
    """Supprime toutes les données transactionnelles."""
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
#
# --- TOUT LE SYSTÈME DE PRÊTS (WEB) EST CORRIGÉ CI-DESSOUS ---
#
#

@app.get("/loans", name="loans_page")
async def loans_page(request: Request, db: AsyncSession = Depends(get_db), user: dict = Depends(web_require_permission("can_manage_loans"))):
    employees = (await db.execute(select(Employee).where(Employee.active==True).order_by(Employee.first_name))).scalars().all()
    
    # --- CORRECTION ---
    # (Ajout de .options(selectinload(Loan.employee)) pour éviter les erreurs dans le template)
    loans = (await db.execute(
        select(Loan).options(selectinload(Loan.employee))
        .order_by(Loan.created_at.desc()).limit(200)
    )).scalars().all()
    # --- FIN CORRECTION ---
    
    return templates.TemplateResponse("loans.html", {"request": request, "user": user, "app_name": APP_NAME, "employees": employees, "loans": loans})

@app.post("/loans/create", name="loans_create_web")
async def loans_create_web(
    request: Request,
    employee_id: Annotated[int, Form()],
    principal: Annotated[Decimal, Form()],
    term_count: Annotated[int, Form()] = 1,
    term_unit: Annotated[str, Form()] = "month",
    start_date: Annotated[dt_date, Form()] = dt_date.today(),
    first_due_date: Annotated[dt_date | None, Form()] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_loans")),
):
    payload = LoanCreate(
        employee_id=employee_id, 
        principal=principal, 
        interest_type="none", 
        annual_interest_rate=None,
        term_count=term_count, 
        term_unit=term_unit,
        start_date=start_date, 
        first_due_date=first_due_date, 
        fee=None
    )
    # Reuse API path
    from app.api.loans import create_loan
    await create_loan(payload, db, user)
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
            selectinload(Loan.schedules),  # <-- Simplifié
            selectinload(Loan.repayments)   # <-- Simplifié
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
        amount=amount,
        paid_on=paid_on,
        source="cash", 
        notes=notes,
        schedule_id=None 
    )
    
    try:
        # Appeler la fonction API
        await loans_api.repay(loan_id=loan_id, payload=payload, db=db, user=user)
    except HTTPException as e:
        print(f"Erreur lors du remboursement: {e.detail}")
    
    return RedirectResponse(
        request.url_for("loan_detail_page", loan_id=loan_id), 
        status_code=status.HTTP_302_FOUND
    )
