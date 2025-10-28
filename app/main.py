import os
from datetime import timedelta, date as dt_date, datetime
from decimal import Decimal
from typing import Annotated, List, Optional
import json
import enum # Ajout de l'import enum manquant
import traceback # Pour un meilleur logging d'erreur

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status, APIRouter, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, delete, func, case, extract, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.future import select
from . import models, schemas # Keep this general import if other parts of the file use models.XXX
import io # Importé pour l'export

# --- CORRIGÉ : Import de get_db depuis .deps ---
from .db import engine, Base, AsyncSessionLocal
# --- CORRIGÉ : Import de hash_password ---
from .auth import authenticate_user, create_access_token, hash_password, ACCESS_TOKEN_EXPIRE_MINUTES, api_require_permission

# Importer TOUS les modèles nécessaires (including Role and Enums explicitly)
from .models import (
    Role, PayType, AttendanceType, LeaveType, LoanStatus, LoanTermUnit, ScheduleStatus,
    RepaymentSource, User, Branch, Employee, Attendance, Leave, Deposit, Pay, Loan,
    LoanSchedule, LoanRepayment, AuditLog, LoanInterestType # Added missing Enums like LoanInterestType
)
# Import Schemas needed in main.py
from .schemas import RoleCreate, RoleUpdate, LoanCreate, RepaymentCreate

# --- FIX: Import audit functions ---
from .audit import latest, log
# --- END FIX ---

# Import Routers
from .routers import users, branches, employees as employees_api, attendance as attendance_api, leaves as leaves_api, deposits as deposits_api
# --- MODIFIÉ : Importer les nouvelles dépendances ---
from .deps import get_db, web_require_permission, get_current_session_user
# --- LOANS API Router ---
from app.api import loans as loans_api
# Note: Redundant imports like `from app.models import Employee...` are removed as they are covered by `from .models import ...`

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
# ... (Startup code remains the same - not shown for brevity) ...
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
                await session.flush() # Assigner les IDs aux roles

                # Créer les branches même si on ne crée pas les managers par défaut
                res_branch = await session.execute(select(Branch).where(Branch.name == "Magasin Ariana"))
                branch_ariana = res_branch.scalar_one_or_none()

                if not branch_ariana:
                    print("Ajout des magasins par défaut...")
                    branch_ariana = Branch(name="Magasin Ariana", city="Ariana")
                    branch_nabeul = Branch(name="Magasin Nabeul", city="Nabeul")
                    session.add_all([branch_ariana, branch_nabeul])
                    await session.flush() # Assigner les IDs aux branches
                # Pas besoin de else ici, si elles existent déjà, c'est bon.

                res_admin_user = await session.execute(select(User).where(User.email == "zaher@local"))

                if res_admin_user.scalar_one_or_none() is None:
                    print("Ajout de l'utilisateur admin initial...")
                    # --- FIX: Créer seulement l'utilisateur Admin ---
                    admin_user = User(
                            email="zaher@local", full_name="Zaher (Admin)", role_id=admin_role.id,
                            hashed_password=hash_password("zah1405"), is_active=True, branch_id=None
                        )
                    session.add(admin_user)
                    # --- FIN DU FIX ---
                    await session.commit()
                    print(f"✅ Rôles, Magasins et l'utilisateur Admin créés avec succès !")
                else:
                    print("Utilisateur admin déjà présent, commit des rôles/magasins si nécessaire.")
                    await session.commit() # Commit au cas où les roles/branches ont été créés
            else:
                print("Données initiales déjà présentes. Seeding ignoré.")
    except Exception as e:
        print(f"Erreur pendant le seeding initial : {e}")
        import traceback
        traceback.print_exc() # Print full traceback for debugging
        await session.rollback()


# --- 4. Fonctions d'aide (Helper Functions) ---
# ... (Functions _serialize_permissions, CustomJSONEncoder, _parse_dates remain the same - not shown for brevity) ...
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
        # --- FIX: Ne plus exclure hashed_password ---
        if isinstance(obj, Base): # Gérer les objets SQLAlchemy
             return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
        # --- FIN FIX ---
        if isinstance(obj, enum.Enum):
            return obj.value
        return super().default(obj)
# --- FIN NOUVEAU ---

# --- NOUVEAU: Helper pour convertir les dates/datetimes lors de l'import ---
def _parse_dates(item: dict, date_fields: list[str] = [], datetime_fields: list[str] = []):
    """Convertit les champs date/datetime string d'un dict en objets Python."""
    for field in date_fields:
        if field in item and item[field] and isinstance(item[field], str): # Ajout de 'item[field]' pour vérifier non-None
            try:
                item[field] = dt_date.fromisoformat(item[field])
            except ValueError:
                print(f"AVERTISSEMENT: Impossible de parser la date '{item[field]}' pour le champ '{field}'. Mise à None.")
                item[field] = None
    for field in datetime_fields:
        if field in item and item[field] and isinstance(item[field], str): # Ajout de 'item[field]' pour vérifier non-None
            try:
                # Gérer les différents formats possibles (avec/sans T, avec/sans Z/+offset)
                dt_str = item[field].replace('T', ' ').split('.')[0] # Enlever les millisecondes
                dt_str = dt_str.split('+')[0].split('Z')[0].strip() # Enlever offset/Z

                # Essayer différents formats si fromisoformat échoue
                try:
                   item[field] = datetime.fromisoformat(dt_str)
                except ValueError:
                   # Tenter avec un format commun si isoformat échoue (ex: backup ancien)
                   item[field] = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                print(f"AVERTISSEMENT: Impossible de parser datetime '{item[field]}' pour le champ '{field}'. Mise à None.")
                item[field] = None
    return item


# --- 5. Routes des Pages Web (GET et POST) ---

@app.get("/", response_class=HTMLResponse, name="home")
async def home(
    request: Request,
    db: AsyncSession = Depends(get_db), # <<< Add db dependency
    current_user: models.User = Depends(get_current_db_user) # Get full user object
):
    if not current_user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

# --- FIX: Fetch recent activity logs FOR ADMIN ---
    activity_logs = []
    # Ensure permissions relation is loaded and user has permissions attribute
    if hasattr(current_user, 'permissions') and current_user.permissions and current_user.permissions.is_admin:
        permissions_dict = current_user.permissions.to_dict() # Use the existing method
        # --- FIX: Call 'latest' correctly ---
        activity_logs = await latest(
            db, # Pass db as the first argument
            user_is_admin=permissions_dict.get("is_admin", False),
            branch_id=current_user.branch_id, # Use branch_id from the full user object
             # Fetch a broader range of activities for the admin dashboard view
            entity_types=["leave", "attendance", "deposit", "pay", "loan", "user", "role", "employee", "branch", "all_logs"],
            limit=15 # Limit to the latest 15 activities for the dashboard
        )
        # --- END FIX ---
        # Optional Eager Loading (commented out as 'latest' might handle it)
        # actor_ids = {log.actor_id for log in activity_logs if log.actor_id}
        # if actor_ids:
        #     actors_res = await db.execute(select(User).where(User.id.in_(actor_ids)))
        #     actors_map = {actor.id: actor for actor in actors_res.scalars()}
        #     for log in activity_logs:
        #         log.actor = actors_map.get(log.actor_id)
    # --- END FIX BLOCK ---

    context = {
        "request": request,
        "user": current_user, # Pass the full user object
        "activity": activity_logs # Pass activity logs to template
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
        # --- FIX: Re-fetch users list on failed login ---
        res_users = await db.execute(select(User).order_by(User.full_name))
        users_list = res_users.scalars().all()
        # --- END FIX ---
        context = {
            "request": request,
            "app_name": APP_NAME,
            "error": "Email ou mot de passe incorrect.",
            "users": users_list # --- FIX: Pass users list to context ---
        }
        return templates.TemplateResponse("login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    # If login is successful
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
# ... (Employees routes remain the same - not shown for brevity) ...
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
# ... (Attendance routes remain the same - not shown for brevity) ...
@app.get("/attendance", response_class=HTMLResponse, name="attendance_page")
async def attendance_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_absences"))
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    attendance_query = select(Attendance).options(selectinload(Attendance.employee)).order_by(Attendance.date.desc(), Attendance.created_at.desc()) # Charger l'employé

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

@app.post("/attendance/{attendance_id}/delete", name="attendance_delete")
async def attendance_delete(
    request: Request,
    attendance_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_absences")) # Ensure correct permission
):
    """Supprime un enregistrement d'absence."""

    # Fetch the attendance record along with the employee to check branch permission
    attendance_query = select(Attendance).options(selectinload(Attendance.employee)).where(Attendance.id == attendance_id)
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        # Non-admin can only delete if the employee belongs to their branch
        attendance_query = attendance_query.join(Employee).where(Employee.branch_id == user.get("branch_id"))

    res_att = await db.execute(attendance_query)
    attendance_to_delete = res_att.scalar_one_or_none()

    if attendance_to_delete:
        try:
            employee_name = f"{attendance_to_delete.employee.first_name} {attendance_to_delete.employee.last_name}" if attendance_to_delete.employee else f"ID {attendance_to_delete.employee_id}"
            attendance_date = attendance_to_delete.date
            emp_branch_id = attendance_to_delete.employee.branch_id if attendance_to_delete.employee else None

            await db.delete(attendance_to_delete)
            await db.commit()

            # Log the deletion
            await log(
                db, user['id'], "delete", "attendance", attendance_id,
                emp_branch_id, f"Absence supprimée pour {employee_name} le {attendance_date}"
            )
            await db.commit() # Commit the log entry

            print(f"✅ Absence ID={attendance_id} supprimée avec succès.")

        except Exception as e:
            await db.rollback()
            print(f"ERREUR lors de la suppression de l'absence ID={attendance_id}: {e}")
            traceback.print_exc()
            # Optionally add a flash message here

    else:
        # Attendance record not found or user doesn't have permission
        print(f"Tentative de suppression de l'absence ID={attendance_id} échouée (non trouvée ou accès refusé).")

    # Redirect back to the attendance list page
    return RedirectResponse(request.url_for("attendance_page"), status_code=status.HTTP_302_FOUND)

# --- Avances (Deposits) ---
# ... (Deposits routes remain the same - not shown for brevity) ...
@app.get("/deposits", response_class=HTMLResponse, name="deposits_page")
async def deposits_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_deposits"))
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    deposits_query = select(Deposit).options(selectinload(Deposit.employee)).order_by(Deposit.date.desc(), Deposit.created_at.desc()) # Charger l'employé

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

@app.post("/deposits/{deposit_id}/delete", name="deposits_delete")
async def deposits_delete(
    request: Request,
    deposit_id: int,
    db: AsyncSession = Depends(get_db),
    # --- FIX: Use correct permission 'can_manage_deposits' or is_admin ---
    user: dict = Depends(web_require_permission("can_manage_deposits"))
):
    """Supprime un enregistrement d'avance."""

    # Fetch the deposit record along with the employee to check branch permission
    deposit_query = select(Deposit).options(selectinload(Deposit.employee)).where(Deposit.id == deposit_id)
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        # Non-admin requires specific permission AND matching branch
        if not permissions.get("can_manage_deposits"): # Double check permission needed
             return RedirectResponse(request.url_for("deposits_page"), status_code=status.HTTP_403_FORBIDDEN)
        deposit_query = deposit_query.join(Employee).where(Employee.branch_id == user.get("branch_id"))

    res_dep = await db.execute(deposit_query)
    deposit_to_delete = res_dep.scalar_one_or_none()

    if deposit_to_delete:
        try:
            employee_name = f"{deposit_to_delete.employee.first_name} {deposit_to_delete.employee.last_name}" if deposit_to_delete.employee else f"ID {deposit_to_delete.employee_id}"
            deposit_date = deposit_to_delete.date
            deposit_amount = deposit_to_delete.amount
            emp_branch_id = deposit_to_delete.employee.branch_id if deposit_to_delete.employee else None

            await db.delete(deposit_to_delete)
            await db.commit()

            # Log the deletion
            await log(
                db, user['id'], "delete", "deposit", deposit_id,
                emp_branch_id, f"Avance supprimée ({deposit_amount} TND) pour {employee_name} du {deposit_date}"
            )
            await db.commit() # Commit the log entry

            print(f"✅ Avance ID={deposit_id} supprimée avec succès.")

        except Exception as e:
            await db.rollback()
            print(f"ERREUR lors de la suppression de l'avance ID={deposit_id}: {e}")
            traceback.print_exc()
            # Optionally add a flash message here

    else:
        # Deposit record not found or user doesn't have permission
        print(f"Tentative de suppression de l'avance ID={deposit_id} échouée (non trouvée ou accès refusé).")

    # Redirect back to the deposits list page
    return RedirectResponse(request.url_for("deposits_page"), status_code=status.HTTP_302_FOUND)

# --- Congés (Leaves) ---
# ... (Leaves routes remain the same - not shown for brevity) ...
@app.get("/leaves", response_class=HTMLResponse, name="leaves_page")
async def leaves_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_leaves"))
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    leaves_query = select(Leave).options(selectinload(Leave.employee)).order_by(Leave.start_date.desc()) # Charger l'employé

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

@app.post("/leaves/{leave_id}/delete", name="leaves_delete")
async def leaves_delete(
    request: Request,
    leave_id: int,
    db: AsyncSession = Depends(get_db),
    # Only Admin can delete leaves for now, adjust permission if needed
    user: dict = Depends(web_require_permission("is_admin"))
):
    """Supprime une demande de congé."""

    # Fetch the leave record along with the employee
    # No need for branch check here as only admin can access
    leave_query = select(Leave).options(selectinload(Leave.employee)).where(Leave.id == leave_id)

    res_leave = await db.execute(leave_query)
    leave_to_delete = res_leave.scalar_one_or_none()

    if leave_to_delete:
        try:
            employee_name = f"{leave_to_delete.employee.first_name} {leave_to_delete.employee.last_name}" if leave_to_delete.employee else f"ID {leave_to_delete.employee_id}"
            leave_start = leave_to_delete.start_date
            leave_end = leave_to_delete.end_date
            emp_branch_id = leave_to_delete.employee.branch_id if leave_to_delete.employee else None

            await db.delete(leave_to_delete)
            await db.commit()

            # Log the deletion
            await log(
                db, user['id'], "delete", "leave", leave_id,
                emp_branch_id, f"Congé supprimé ({leave_start} à {leave_end}) pour {employee_name}"
            )
            await db.commit() # Commit the log entry

            print(f"✅ Congé ID={leave_id} supprimé avec succès.")

        except Exception as e:
            await db.rollback()
            print(f"ERREUR lors de la suppression du congé ID={leave_id}: {e}")
            traceback.print_exc()
            # Optionally add a flash message here

    else:
        # Leave record not found
        print(f"Tentative de suppression du congé ID={leave_id} échouée (non trouvé).")

    # Redirect back to the leaves list page
    return RedirectResponse(request.url_for("leaves_page"), status_code=status.HTTP_302_FOUND)

# --- Rapport Employé ---
# ... (Employee Report route remains the same - not shown for brevity) ...
@app.get("/employee-report", response_class=HTMLResponse, name="employee_report_index")
async def employee_report_index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_view_reports")),
    employee_id: int | None = None
):
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        employees_query = employees_query.where(Employee.branch_id == user.get("branch_id"))

    res_employees = await db.execute(employees_query)
    employees_list = res_employees.scalars().all()

    selected_employee = None
    pay_history = []
    deposits = []
    absences = []
    leaves = []
    loans = [] # Ajout des prêts au rapport

    if employee_id:
        # Vérifier si l'utilisateur a le droit de voir cet employé spécifique
        employee_visible = False
        if permissions.get("is_admin"):
             employee_visible = True
        else:
             for emp in employees_list: # Vérifier dans la liste filtrée
                 if emp.id == employee_id:
                     employee_visible = True
                     break

        if employee_visible:
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
        else:
             employee_id = None # Ne pas montrer les données si pas autorisé

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "employees": employees_list, "selected_employee": selected_employee,
        "pay_history": pay_history, "deposits": deposits,
        "absences": absences, "leaves": leaves, "loans": loans,
        "current_employee_id": employee_id # Passer l'ID pour le selecteur
    }
    return templates.TemplateResponse("employee_report.html", context)


# --- Payer Employé ---
# ... (Pay Employee routes remain the same - not shown for brevity) ...
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
# ... (Roles routes remain the same - not shown for brevity) ...
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
# ... (Users routes remain the same - not shown for brevity) ...
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
# ... (Settings route remains the same) ...
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
        # --- FIX: Inclure 'loan' dans les types d'entités pour le log ---
        entity_types=["leave", "attendance", "deposit", "pay", "loan"]
    )

    context = {
        "request": request, "user": user, "app_name": APP_NAME,
        "logs": filtered_logs
    }
    return templates.TemplateResponse("settings.html", context)


# --- 6. Route de Nettoyage (Corrigée) ---
# ... (Clear Logs route remains the same) ...
@app.post("/settings/clear-logs", name="clear_logs")
async def clear_transaction_logs(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_clear_logs"))
):
    print(f"ACTION ADMIN (user {user['id']}): Nettoyage des journaux...")

    try:
        # Supprimer dans l'ordre inverse des dépendances pour éviter les erreurs de contrainte
        await db.execute(delete(AuditLog))
        await db.execute(delete(LoanRepayment))
        await db.execute(delete(LoanSchedule))
        await db.execute(delete(Loan))
        await db.execute(delete(Pay))
        await db.execute(delete(Deposit))
        await db.execute(delete(Leave))
        await db.execute(delete(Attendance))

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

        # --- FIX: Inclure hashed_password dans l'export ---
        # L'encodeur JSON personnalisé va maintenant inclure toutes les colonnes par défaut
        data_to_export["users"] = (await db.execute(select(User))).scalars().all()
        # --- FIN FIX ---

        data_to_export["employees"] = (await db.execute(select(Employee))).scalars().all()
        data_to_export["attendance"] = (await db.execute(select(Attendance))).scalars().all()
        data_to_export["leaves"] = (await db.execute(select(Leave))).scalars().all()
        data_to_export["deposits"] = (await db.execute(select(Deposit))).scalars().all()
        data_to_export["pay_history"] = (await db.execute(select(Pay))).scalars().all()
        data_to_export["loans"] = (await db.execute(select(Loan))).scalars().all()
        data_to_export["loan_schedules"] = (await db.execute(select(LoanSchedule))).scalars().all()
        data_to_export["loan_repayments"] = (await db.execute(select(LoanRepayment))).scalars().all()
        data_to_export["roles"] = (await db.execute(select(Role))).scalars().all()
        data_to_export["audit_logs"] = (await db.execute(select(AuditLog).order_by(AuditLog.created_at))).scalars().all() # Add this line


    except Exception as e:
        print(f"Erreur pendant l'export: {e}")
        # Log the full traceback for debugging
        import traceback
        traceback.print_exc()
        return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)

    # Créer un fichier JSON en mémoire
    try:
        json_data = json.dumps(data_to_export, cls=CustomJSONEncoder, indent=2, ensure_ascii=False) # Added ensure_ascii=False
    except Exception as e:
        print(f"Erreur pendant l'encodage JSON: {e}")
        import traceback
        traceback.print_exc()
        return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)

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
        return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)

    try:
        contents = await backup_file.read()
        data = json.loads(contents.decode("utf-8"))

        # --- DANGER : SUPPRESSION DES DONNÉES ---
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

        # --- RÉINSERTION DES DONNÉES ---

        # Helper function to safely convert string to Enum
        def get_enum_member(enum_cls, value, default=None):
            if value is None:
                return default
            try:
                return enum_cls(value)
            except ValueError:
                print(f"AVERTISSEMENT: Valeur d'énumération invalide '{value}' pour {enum_cls.__name__}. Utilisation de la valeur par défaut {default}.")
                return default

        if "branches" in data:
            for item in data["branches"]:
                item = _parse_dates(item, datetime_fields=['created_at'])
                db.add(Branch(**item))
        await db.flush()

        if "users" in data:
            for user_data in data["users"]:
                if 'hashed_password' not in user_data or user_data['hashed_password'] is None:
                    print(f"AVERTISSEMENT: Mot de passe manquant pour {user_data.get('email', 'Utilisateur inconnu')}. Utilisation de 'password123'.")
                    user_data['hashed_password'] = hash_password("password123")
                else:
                     user_data['hashed_password'] = str(user_data['hashed_password'])

                user_data = _parse_dates(user_data, datetime_fields=['created_at'])
                user_data.setdefault('is_active', True)
                user_data.setdefault('role_id', 1)
                if user_data.get('role_id') is None:
                     print(f"AVERTISSEMENT: role_id manquant ou null pour {user_data.get('email', 'Utilisateur inconnu')}. Assignation du rôle ID 1 (Admin).")
                     user_data['role_id'] = 1
                db.add(User(**user_data))

        if "employees" in data:
            for item in data["employees"]:
                item = _parse_dates(item, datetime_fields=['created_at'])
                item.setdefault('active', True)
                item.setdefault('position', 'Inconnu')
                if item.get('branch_id') is None:
                    first_branch_res = await db.execute(select(Branch).limit(1))
                    first_branch = first_branch_res.scalar_one_or_none()
                    if first_branch:
                        print(f"AVERTISSEMENT: branch_id manquant pour employé {item.get('first_name')} {item.get('last_name')}. Assignation de la branche ID {first_branch.id}.")
                        item['branch_id'] = first_branch.id
                    else:
                        print(f"ERREUR: branch_id manquant pour employé {item.get('first_name')} {item.get('last_name')} et aucune branche par défaut trouvée. Employé ignoré.")
                        continue
                db.add(Employee(**item))
        await db.flush()

        if "attendance" in data:
            for item in data["attendance"]:
                item = _parse_dates(item, date_fields=['date'], datetime_fields=['created_at'])
                if item.get('employee_id') is None: continue
                # Convert AttendanceType
                item['atype'] = get_enum_member(AttendanceType, item.get('atype'), AttendanceType.absent)
                db.add(Attendance(**item))

        if "leaves" in data:
            for item in data["leaves"]:
                item = _parse_dates(item, date_fields=['start_date', 'end_date'], datetime_fields=['created_at'])
                if item.get('employee_id') is None: continue
                # Convert LeaveType
                item['ltype'] = get_enum_member(LeaveType, item.get('ltype'), LeaveType.unpaid)
                item.setdefault('approved', False)
                db.add(Leave(**item))

        if "deposits" in data:
            for item in data["deposits"]:
                item = _parse_dates(item, date_fields=['date'], datetime_fields=['created_at'])
                if item.get('employee_id') is None: continue
                item.setdefault('amount', 0.0)
                db.add(Deposit(**item))

        if "audit_logs" in data:
            print(f"Importation de {len(data['audit_logs'])} entrées d'audit log...") # Optional: Add logging
            for item in data["audit_logs"]:
                item = _parse_dates(item, datetime_fields=['created_at'])
                if item.get('actor_id') is None:
                    # Maybe try to find user by email if actor_id is missing but email exists?
                    # For now, we skip if actor_id is essential and missing.
                    print(f"AVERTISSEMENT: actor_id manquant pour l'entrée d'audit log ID {item.get('id', 'N/A')}. Log ignoré.")
                    continue
                # Set defaults for nullable fields if they are missing
                item.setdefault('entity_id', None)
                item.setdefault('branch_id', None)
                item.setdefault('details', None)
                # Ensure required fields like action and entity exist
                if not item.get('action') or not item.get('entity'):
                     print(f"AVERTISSEMENT: Action ou Entité manquante pour l'entrée d'audit log ID {item.get('id', 'N/A')}. Log ignoré.")
                     continue

                # Remove 'id' if present, let DB generate new one if needed, or handle potential conflicts
                item.pop('id', None)

                db.add(AuditLog(**item))

        if "pay_history" in data:
            for item in data["pay_history"]:
                item = _parse_dates(item, date_fields=['date'], datetime_fields=['created_at'])
                if item.get('employee_id') is None: continue
                # Convert PayType <<<<------ FIX IS HERE
                item['pay_type'] = get_enum_member(PayType, item.get('pay_type'), PayType.mensuel) # Assuming 'mensuel' is a valid default
                item.setdefault('amount', 0.0)
                # item.setdefault('pay_type', PayType.salary) # Incorrect default removed
                db.add(Pay(**item))

        if "loans" in data:
            for item in data["loans"]:
                item = _parse_dates(item, date_fields=['start_date', 'next_due_on'], datetime_fields=['created_at'])
                if item.get('employee_id') is None: continue
                # Convert LoanStatus and LoanTermUnit
                item['status'] = get_enum_member(LoanStatus, item.get('status'), LoanStatus.draft)
                item['term_unit'] = get_enum_member(LoanTermUnit, item.get('term_unit'), LoanTermUnit.month)
                # Convert LoanInterestType (though likely 'none' based on your code)
                item['interest_type'] = get_enum_member(LoanInterestType, item.get('interest_type'), LoanInterestType.none)

                item.setdefault('principal', 0.0)
                item.setdefault('term_count', 1)
                item.setdefault('repaid_total', 0.0)
                # Recalculate scheduled_total and outstanding_principal if needed, or use defaults
                item.setdefault('scheduled_total', item.get('principal', 0.0))
                item.setdefault('outstanding_principal', item.get('principal', 0.0) - item.get('repaid_total', 0.0))
                db.add(Loan(**item))
        await db.flush()

        if "loan_schedules" in data:
            for item in data["loan_schedules"]:
                item = _parse_dates(item, date_fields=['due_date'], datetime_fields=['created_at'])
                if item.get('loan_id') is None: continue
                # --- FIX: Use correct Enum name ScheduleStatus ---
                item['status'] = get_enum_member(ScheduleStatus, item.get('status'), ScheduleStatus.pending) # Convert Enum using correct name
                # --- END FIX ---
                item.setdefault('sequence_no', 0)
                item.setdefault('due_total', 0.0)
                item.setdefault('paid_total', 0.0)
                db.add(LoanSchedule(**item))

        if "loan_repayments" in data:
            for item in data["loan_repayments"]:
                item = _parse_dates(item, date_fields=['paid_on'], datetime_fields=['created_at'])
                if item.get('loan_id') is None: continue
                # Convert RepaymentSource
                item['source'] = get_enum_member(RepaymentSource, item.get('source'), RepaymentSource.cash)
                item.setdefault('amount', 0.0)
                db.add(LoanRepayment(**item))

        await db.commit()
        print("✅ Importation terminée avec succès.") # Success message

    except json.JSONDecodeError:
        print("ERREUR: Le fichier de sauvegarde n'est pas un JSON valide.")
        await db.rollback()
    except KeyError as e:
         print(f"ERREUR lors de l'import: Clé manquante dans le JSON - {e}")
         traceback.print_exc()
         await db.rollback()
    except Exception as e:
        await db.rollback()
        print(f"ERREUR lors de l'import: {e}")
        traceback.print_exc()

    return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)


#
# --- SECTION DES PRÊTS (WEB) ---
#

@app.get("/loans", name="loans_page")
async def loans_page(request: Request, db: AsyncSession = Depends(get_db), user: dict = Depends(web_require_permission("can_manage_loans"))):
    employees_query = select(Employee).where(Employee.active==True).order_by(Employee.first_name)
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        employees_query = employees_query.where(Employee.branch_id == user.get("branch_id"))

    employees = (await db.execute(employees_query)).scalars().all()

    loans_query = select(Loan).options(selectinload(Loan.employee)).order_by(Loan.created_at.desc())
    if not permissions.get("is_admin"):
        loans_query = loans_query.join(Employee).where(Employee.branch_id == user.get("branch_id"))

    loans = (await db.execute(loans_query.limit(200))).scalars().all()

    return templates.TemplateResponse("loans.html", {"request": request, "user": user, "app_name": APP_NAME, "employees": employees, "loans": loans})

@app.post("/loans/create", name="loans_create_web")
async def loans_create_web(
    request: Request,
    employee_id: Annotated[int, Form()],
    principal: Annotated[Decimal, Form()],
    term_count: Annotated[int, Form()] = 1, # Gardé pour compatibilité API
    term_unit: Annotated[str, Form()] = "month", # Gardé pour compatibilité API
    start_date: Annotated[dt_date, Form()] = dt_date.today(),
    first_due_date: Annotated[dt_date | None, Form()] = None, # Gardé pour compatibilité API
    notes: Annotated[str, Form()] = None,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_loans")),
):
    # Vérifier l'autorisation de gérer l'employé
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
         return RedirectResponse(request.url_for("loans_page"), status_code=status.HTTP_302_FOUND)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin") and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for("loans_page"), status_code=status.HTTP_302_FOUND)

    # Créer le payload pour l'API interne, même si certains champs ne sont plus utilisés par la logique web
    payload = LoanCreate(
        employee_id=employee_id, principal=principal, interest_type="none",
        annual_interest_rate=None, term_count=term_count, term_unit=term_unit,
        start_date=start_date, first_due_date=first_due_date, fee=None
    )
    from app.api.loans import create_loan

    new_loan = await create_loan(payload, db, user)

    # Ajouter la note manuellement
    if new_loan and notes:
        try:
            new_loan.notes = notes
            await db.commit()
        except Exception as e:
            await db.rollback()
            print(f"Erreur lors de l'ajout de la note au prêt: {e}")

    return RedirectResponse(request.url_for("loans_page"), status_code=status.HTTP_302_FOUND)


@app.get("/loan/{loan_id}", response_class=HTMLResponse, name="loan_detail_page")
async def loan_detail_page(
    request: Request,
    loan_id: int,
    db: AsyncSession = Depends(get_db),
    user: dict = Depends(web_require_permission("can_manage_loans"))
):
    """Affiche la page de détails d'un prêt."""

    loan_query = select(Loan).options(
            selectinload(Loan.employee),
            selectinload(Loan.schedules),
            selectinload(Loan.repayments)
        ).where(Loan.id == loan_id)

    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        loan_query = loan_query.join(Employee).where(Employee.branch_id == user.get("branch_id"))

    loan = (await db.execute(loan_query)).scalar_one_or_none()

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

    # Vérifier si l'utilisateur a le droit de voir/supprimer ce prêt
    loan_query = select(Loan).options(selectinload(Loan.employee)).where(Loan.id == loan_id)
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
        loan_query = loan_query.join(Employee).where(Employee.branch_id == user.get("branch_id"))

    loan = (await db.execute(loan_query)).scalar_one_or_none()

    if loan:
        try:
            employee_id_log = loan.employee_id # Sauvegarder avant suppression
            # Vérifier si l'employé existe encore avant d'accéder à branch_id
            branch_id_log = loan.employee.branch_id if loan.employee else None

            # La suppression en cascade est gérée par app/models.py
            await db.delete(loan)
            await db.commit()

            await log(
                db, user['id'], "delete", "loan", loan_id,
                branch_id_log, f"Prêt supprimé pour l'employé ID={employee_id_log}"
            )
            await db.commit() # Commit du log
        except Exception as e:
            await db.rollback()
            print(f"Erreur lors de la suppression du prêt {loan_id}: {e}")
            import traceback
            traceback.print_exc()


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

    # Vérifier l'autorisation avant de traiter le remboursement
    loan_check_query = select(Loan).options(selectinload(Loan.employee)).where(Loan.id == loan_id)
    permissions = user.get("permissions", {})
    if not permissions.get("is_admin"):
         loan_check_query = loan_check_query.join(Employee).where(Employee.branch_id == user.get("branch_id"))

    loan_exists = (await db.execute(loan_check_query)).scalar_one_or_none()
    if not loan_exists:
        # L'utilisateur n'a pas accès à ce prêt ou il n'existe pas
         return RedirectResponse(request.url_for("loans_page"), status_code=status.HTTP_302_FOUND)

    payload = schemas.RepaymentCreate(
        amount=amount, paid_on=paid_on, source="cash",
        notes=notes, schedule_id=None
    )

    try:
        # L'API (repay) gère déjà la logique de paiement flexible/partiel et le log d'audit
        await loans_api.repay(loan_id=loan_id, payload=payload, db=db, user=user)
    except HTTPException as e:
        print(f"Erreur HTTP lors du remboursement web pour prêt {loan_id}: {e.detail}")
        # Ajouter potentiellement un message flash ici
    except Exception as e:
         print(f"Erreur générale lors du remboursement web pour prêt {loan_id}: {e}")
         await db.rollback() # S'assurer que la session est propre en cas d'erreur inattendue
         # Ajouter potentiellement un message flash ici

    return RedirectResponse(
        request.url_for("loan_detail_page", loan_id=loan_id),
        status_code=status.HTTP_302_FOUND
    )
