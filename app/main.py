"""
Point d'entrée de l'application FastAPI pour la gestion RH de la Bijouterie.
"""
import os
from datetime import timedelta, date as dt_date, datetime
from decimal import Decimal
from typing import Annotated # --- AJOUTÉ ---

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
# --- AJOUTÉ ---
from sqlalchemy import select, delete, func, case, extract, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db import engine, Base, AsyncSessionLocal
from .auth import authenticate_user, create_access_token, hash_password, ACCESS_TOKEN_EXPIRE_MINUTES
# Importer TOUS les modèles nécessaires
from .models import (
    Attendance, AttendanceType, Branch, Deposit, Employee, Leave, User, Role, Pay, PayType, AuditLog
)
from .audit import latest, log
from .routers import users, branches, employees as employees_api, attendance as attendance_api, leaves as leaves_api, deposits as deposits_api # --- Renommé pour éviter les conflits ---
from .deps import get_db, current_user 


# --- MODIFIÉ : Nom par défaut changé ---
APP_NAME = os.getenv("APP_NAME", "Bijouterie Zaher")

app = FastAPI(title=APP_NAME)

# --- 1. API Routers ---
app.include_router(users.router)
app.include_router(branches.router)
app.include_router(employees_api.router) # --- Variable renommée ---
app.include_router(attendance_api.router) # --- Variable renommée ---
app.include_router(leaves_api.router) # --- Variable renommée ---
app.include_router(deposits_api.router) # --- Variable renommée ---


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
    max_age=int(ACCESS_TOKEN_EXPIRE_MINUTES) * 60 # La session expire en même temps que le token
)


# --- 3. Startup Event ---
@app.on_event("startup")
async def on_startup() -> None:
    """Créer les tables de la base de données et ajouter les données initiales."""
    print("Événement de démarrage...")
    async with engine.begin() as conn:
        print("Création de toutes les tables (si elles n'existent pas)...")
        await conn.run_sync(Base.metadata.create_all)
        print("Tables OK.")

    # --- Logique de Seeding (reste identique) ---
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(User).where(User.email == "zaher@local"))
            if res.scalar_one_or_none() is None:
                print("Base de données vide, ajout des données initiales (seed)...")
                branch_ariana = Branch(name="Magasin Ariana", city="Ariana")
                branch_nabeul = Branch(name="Magasin Nabeul", city="Nabeul")
                session.add_all([branch_ariana, branch_nabeul])
                await session.flush()
                users_to_create = [
                    User(
                        email="zaher@local",
                        full_name="Zaher (Admin)",
                        role=Role.admin,
                        hashed_password=hash_password("zah1405"),
                        is_active=True,
                        branch_id=None
                    ),
                    User(
                        email="ariana@local",
                        full_name="Ariana (Manager)",
                        role=Role.manager,
                        hashed_password=hash_password("ar123"),
                        is_active=True,
                        branch_id=branch_ariana.id
                    ),
                    User(
                        email="nabeul@local",
                        full_name="Nabeul (Manager)",
                        role=Role.manager,
                        hashed_password=hash_password("na123"),
                        is_active=True,
                        branch_id=branch_nabeul.id
                    ),
                ]
                session.add_all(users_to_create)
                await session.commit()
                print(f"✅ {len(users_to_create)} utilisateurs créés avec succès !")
            else:
                print("Utilisateurs initiaux déjà présents. Seeding ignoré.")
    except Exception as e:
        print(f"Erreur pendant le seeding initial : {e}")


# --- 4. Fonctions d'aide (Helper Functions) ---

def _get_user_from_session(request: Request) -> dict | None:
    """Récupère les informations utilisateur de la session."""
    return request.session.get("user")


# --- 5. Routes des Pages Web (GET et POST) ---

@app.get("/", response_class=HTMLResponse, name="home")
async def home(request: Request, db: AsyncSession = Depends(get_db)):
    """Affiche la page d'accueil (tableau de bord)."""
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)

    # --- CORRIGÉ : Renommé la variable pour ne pas écraser 'latest' ---
    latest_logs_list = await latest(
        db, 
        user_role=user.get("role"), 
        branch_id=user.get("branch_id")
    )

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
        "activity": latest_logs_list # --- CORRIGÉ : Passé au template ---
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(request: Request):
    """Affiche la page de connexion."""
    return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME})


@app.post("/login", name="login_action")
async def login_action(
    request: Request, 
    username: str = Form(...), 
    password: str = Form(...),
    db: AsyncSession = Depends(get_db) 
):
    """Traite la soumission du formulaire de connexion."""
    user = await authenticate_user(db, username, password)
    if not user:
        context = {
            "request": request, 
            "app_name": APP_NAME, 
            "error": "Email ou mot de passe incorrect."
        }
        return templates.TemplateResponse("login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    access_token = create_access_token(
        data={"sub": user.email, "id": user.id, "role": user.role.value, "branch_id": user.branch_id}
    )
    request.session["user"] = {
        "email": user.email,
        "id": user.id,
        "full_name": user.full_name,
        "role": user.role.value,
        "branch_id": user.branch_id,
        "token": access_token 
    }
    
    return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)


@app.get("/logout", name="logout")
async def logout(request: Request):
    """Déconnecte l'utilisateur en vidant la session."""
    request.session.clear()
    return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)

# --- 
# --- ❗️❗️ DÉBUT DES ROUTES CORRIGÉES ET AJOUTÉES ❗️❗️
# ---

# --- Employés ---
@app.get("/employees", response_class=HTMLResponse, name="employees_page")
async def employees_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Affiche la page de gestion des employés."""
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)

    # L'admin voit toutes les branches, le manager ne voit que la sienne
    branches_query = select(Branch)
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    
    manager_branch_id = None
    if user["role"] == Role.manager.value:
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
    cin: Annotated[str, Form()] = None,
    salary: Annotated[Decimal, Form()] = None
):
    """Crée un nouvel employé."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in [Role.admin.value, Role.manager.value]:
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)
    
    # Sécurité : un manager ne peut ajouter un employé que dans son propre magasin
    if user["role"] == Role.manager.value and user.get("branch_id") != branch_id:
        # Gérer l'erreur (idéalement, renvoyer un message d'erreur au formulaire)
        return RedirectResponse(request.url_for('employees_page'), status_code=status.HTTP_302_FOUND)
    
    # Sécurité : Seul un admin peut définir un salaire
    if user["role"] != Role.admin.value:
        salary = None

    # Vérifier si le CIN existe déjà (s'il est fourni)
    if cin:
        res_cin = await db.execute(select(Employee).where(Employee.cin == cin))
        if res_cin.scalar_one_or_none():
            # Gérer l'erreur CIN dupliqué (idéalement, renvoyer un message)
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
async def attendance_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Affiche la page des absences."""
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)

    # Préparer les requêtes
    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    attendance_query = select(Attendance).order_by(Attendance.date.desc(), Attendance.created_at.desc())

    if user["role"] == Role.manager.value:
        branch_id = user.get("branch_id")
        # Filtrer les employés ET les absences par magasin (via jointure)
        employees_query = employees_query.where(Employee.branch_id == branch_id)
        attendance_query = attendance_query.join(Employee).where(Employee.branch_id == branch_id)

    res_employees = await db.execute(employees_query)
    res_attendance = await db.execute(attendance_query.limit(100)) # Limiter les résultats

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
        "employees": res_employees.scalars().all(),
        "attendance": res_attendance.scalars().all(),
        "today_date": dt_date.today().isoformat() # Pour pré-remplir la date
    }
    return templates.TemplateResponse("attendance.html", context)


@app.post("/attendance/create", name="attendance_create")
async def attendance_create(
    request: Request,
    employee_id: Annotated[int, Form()],
    date: Annotated[dt_date, Form()],
    db: AsyncSession = Depends(get_db),
    note: Annotated[str, Form()] = None
):
    """Enregistre une nouvelle absence."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in [Role.admin.value, Role.manager.value]:
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)

    # Récupérer l'employé pour vérifier le magasin
    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
        return RedirectResponse(request.url_for('attendance_page'), status_code=status.HTTP_302_FOUND)

    # Sécurité : un manager ne peut agir que sur son magasin
    if user["role"] == Role.manager.value and user.get("branch_id") != employee.branch_id:
        return RedirectResponse(request.url_for('attendance_page'), status_code=status.HTTP_302_FOUND)
    
    new_attendance = Attendance(
        employee_id=employee_id,
        date=date,
        atype=AttendanceType.absent, # Toujours 'absent' depuis ce formulaire
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
async def deposits_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Affiche la page des avances."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in [Role.admin.value, Role.manager.value]:
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)

    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    deposits_query = select(Deposit).order_by(Deposit.date.desc(), Deposit.created_at.desc())

    if user["role"] == Role.manager.value:
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
    note: Annotated[str, Form()] = None
):
    """Enregistre une nouvelle avance."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in [Role.admin.value, Role.manager.value]:
        return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)

    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee or amount <= 0:
        return RedirectResponse(request.url_for('deposits_page'), status_code=status.HTTP_302_FOUND)

    if user["role"] == Role.manager.value and user.get("branch_id") != employee.branch_id:
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
async def leaves_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Affiche la page des congés (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)

    employees_query = select(Employee).where(Employee.active == True).order_by(Employee.first_name)
    leaves_query = select(Leave).order_by(Leave.start_date.desc())

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
    db: AsyncSession = Depends(get_db)
):
    """Crée une demande de congé (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)

    if start_date > end_date:
        # Gérer erreur de date
        return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)

    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    if not employee:
        return RedirectResponse(request.url_for('leaves_page'), status_code=status.HTTP_302_FOUND)

    new_leave = Leave(
        employee_id=employee_id,
        start_date=start_date,
        end_date=end_date,
        ltype=ltype,
        approved=False, # Doit être approuvé manuellement
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
    db: AsyncSession = Depends(get_db)
):
    """Approuve un congé (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)

    res_leave = await db.execute(
        select(Leave).options(selectinload(Leave.employee)).where(Leave.id == leave_id)
    )
    leave = res_leave.scalar_one_or_none()

    if not leave or leave.approved:
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
    employee_id: int | None = None
):
    """Affiche la page de rapport d'un employé (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)

    # L'admin voit tous les employés pour le sélecteur
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
            # Récupérer toutes les données liées
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
async def pay_employee_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Affiche la page pour enregistrer un paiement (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)

    res_employees = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.first_name))
    
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
    note: Annotated[str, Form()] = None
):
    """Enregistre un nouveau paiement (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)

    res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
    employee = res_emp.scalar_one_or_none()
    
    if not employee or amount <= 0:
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

    # Rediriger vers la page du rapport de cet employé pour voir le paiement
    return RedirectResponse(
        request.url_for('employee_report_index') + f"?employee_id={employee_id}", 
        status_code=status.HTTP_302_FOUND
    )


# --- Page Paramètres ---
@app.get("/settings", response_class=HTMLResponse, name="settings_page")
async def settings_page(request: Request, db: AsyncSession = Depends(get_db)):
    """Affiche la page des paramètres (réservée à l'admin)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    
    # Récupérer les logs filtrés
    filtered_logs = await latest(
        db,
        user_role=user.get("role"),
        branch_id=user.get("branch_id"),
        entity_types=["leave", "attendance", "deposit", "pay"] # Seulement ces types
    )

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
        "logs": filtered_logs # Passer les logs filtrés
    }
    return templates.TemplateResponse("settings.html", context)


# --- 6. Route de Nettoyage (Corrigée) ---
@app.post("/settings/clear-logs", name="clear_logs")
async def clear_transaction_logs(request: Request, db: AsyncSession = Depends(get_db)): # Ajout de DB
    """
    Supprime toutes les données transactionnelles (absences, congés, avances, paies, audits).
    NE SUPPRIME PAS les employés, utilisateurs, ou magasins.
    (Admin seulement)
    """
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    print(f"ACTION ADMIN (user {user['id']}): Nettoyage des journaux...")

    try:
        # 1. Supprimer les journaux d'audit
        await db.execute(delete(AuditLog))
        print("Journaux d'audit supprimés.")
        
        # 2. Supprimer les absences
        await db.execute(delete(Attendance))
        print("Journaux d'absences supprimés.")

        # 3. Supprimer les congés
        await db.execute(delete(Leave))
        print("Journaux de congés supprimés.")

        # 4. Supprimer les avances
        await db.execute(delete(Deposit))
        print("Journaux d'avances supprimés.")

        # 5. Supprimer l'historique de paie
        await db.execute(delete(Pay))
        print("Historique de paie supprimé.")
        
        await db.commit()
        print("✅ Nettoyage des journaux terminé avec succès.")
        
        # Recréer un log d'audit pour cette action
        # Note : On commit le log séparément
        await log(
            db, user['id'], "delete", "all_logs", None,
            None, "Toutes les données transactionnelles ont été supprimées."
        )
        await db.commit() # Commit du nouveau log
        
    except Exception as e:
        await db.rollback()
        print(f"ERREUR lors du nettoyage des journaux: {e}")

    return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)
