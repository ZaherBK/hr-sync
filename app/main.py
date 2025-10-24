"""
Point d'entrée de l'application FastAPI pour la gestion RH de la Bijouterie.
"""
import os
from datetime import timedelta, date as dt_date, datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, delete

from .db import engine, Base, AsyncSessionLocal
from .auth import authenticate_user, create_access_token, hash_password
# Importer tous les modèles, y compris les nouveaux
from .models import (
    Attendance, AttendanceType, Branch, Deposit, Employee, Leave, User, Role, Pay, PayType
)
from .audit import latest, log # Importer 'log'
from .routers import users, branches, employees, attendance, leaves, deposits


APP_NAME = os.getenv("APP_NAME", "Bijouterie Zaher") # --- NOM CHANGÉ ---

app = FastAPI(title=APP_NAME)

# Inclure tous les routeurs API
app.include_router(users.router)
app.include_router(branches.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(leaves.router)
app.include_router(deposits.router)

# Configuration des fichiers statiques et templates
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
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "une_cle_secrete_tres_longue_et_aleatoire"))


@app.on_event("startup")
async def on_startup() -> None:
    """Créer les tables de la base de données et ajouter les données initiales."""
    print("Événement de démarrage...")
    async with engine.begin() as conn:
        
        # --- LIGNE TEMPORAIRE À AJOUTER ---
        # A ENLEVER APRES LE PREMIER DEPLOIEMENT REUSSI !
        print("ATTENTION: Suppression de toutes les tables existantes pour mise à jour du schéma...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Tables supprimées.")
        # --- FIN DE LA LIGNE TEMPORAIRE ---

        print("Création de toutes les nouvelles tables...")
        await conn.run_sync(Base.metadata.create_all)
        print("Tables OK.")

    # --- LOGIQUE DE SEEDING INITIAL ---
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(User).where(User.email == "zaher@local"))
            if res.scalar_one_or_none() is None:
                print("Base de données vide, ajout des données initiales (seed)...")

                branch_ariana = Branch(name="Magasin Ariana", city="Ariana")
                branch_nabeul = Branch(name="Magasin Nabeul", city="Nabeul")
                session.add_all([branch_ariana, branch_nabeul])
                await session.flush()

                print(f"Magasins créés : ID Ariana={branch_ariana.id}, ID Nabeul={branch_nabeul.id}")

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
    # --- FIN DE LA LOGIQUE DE SEEDING ---

def _get_user_from_session(request: Request) -> dict | None:
    """Récupérer les informations utilisateur stockées dans la session."""
    return request.session.get("user")


# --- Routes HTML ---

@app.get("/", response_class=HTMLResponse, name="home")
async def home(request: Request):
    """Page d'accueil (Tableau de bord)."""
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    activity = []
    async with AsyncSessionLocal() as db:
        activity = await latest(db, user_role=user.get("role"), branch_id=user.get("branch_id"))

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "activity": activity,
            "app_name": APP_NAME,
        },
    )

@app.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(request: Request, error: str | None = None):
    """Page de connexion."""
    user = _get_user_from_session(request)
    if user:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    return templates.TemplateResponse(
        "login.html",
        {"request": request, "error": error, "app_name": APP_NAME},
    )

@app.post("/login", name="login_action")
async def login_action(request: Request, username: str = Form(...), password: str = Form(...)):
    """Traitement du formulaire de connexion."""
    async with AsyncSessionLocal() as db:
        user = await authenticate_user(db, username, password)
        if not user:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Email ou mot de passe incorrect.",
                    "app_name": APP_NAME,
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        token = create_access_token(
            {"sub": str(user.id), "role": user.role.value, "branch_id": user.branch_id},
            expires_delta=timedelta(days=7),
        )

        request.session["user"] = {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value,
            "branch_id": user.branch_id,
            "token": token,
        }
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

@app.get("/logout", name="logout")
async def logout(request: Request):
    """Déconnexion de l'utilisateur."""
    request.session.clear()
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)


@app.get("/employees", response_class=HTMLResponse, name="employees_page")
async def employees_page(request: Request):
    """Page de gestion des employés."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Visible par admin et manager ---
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    employees = []
    branches = []
    manager_branch_id = None

    async with AsyncSessionLocal() as db:
        # L'admin voit tous les employés
        if user["role"] == Role.admin.value:
            res_emp = await db.execute(select(Employee).order_by(Employee.last_name, Employee.first_name))
            employees = res_emp.scalars().all()
        # Les managers voient ceux de leur magasin
        elif user["role"] == Role.manager.value and user["branch_id"]:
            res_emp = await db.execute(select(Employee).where(Employee.branch_id == user["branch_id"]).order_by(Employee.last_name, Employee.first_name))
            employees = res_emp.scalars().all()
        
        res_branches = await db.execute(select(Branch).order_by(Branch.name))
        branches = res_branches.scalars().all()

        if user["role"] == Role.manager.value:
            manager_branch_id = user.get("branch_id")

    return templates.TemplateResponse(
        "employees.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "branches": branches,
            "manager_branch_id": manager_branch_id,
            "app_name": APP_NAME,
        },
    )

@app.post("/employees/create", name="employees_create")
async def employees_create(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    cin: str | None = Form(None),
    position: str = Form(...),
    branch_id: int = Form(...),
    salary: str | None = Form(None) # --- NOUVEAU CHAMP SALAIRE ---
):
    """Traitement du formulaire de création d'employé."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    if user["role"] == Role.manager.value and user["branch_id"] != branch_id:
         print(f"Erreur: Manager {user['id']} a tenté d'ajouter un employé au magasin {branch_id} au lieu de {user['branch_id']}")
         return RedirectResponse("/employees", status_code=status.HTTP_302_FOUND)

    cleaned_cin = cin.strip() if cin else None
    if cleaned_cin == "":
        cleaned_cin = None

    # --- Validation du Salaire (seul l'admin peut le soumettre) ---
    validated_salary = None
    if salary and user["role"] == Role.admin.value: # --- PERMISSION ---
        try:
            validated_salary = Decimal(salary.replace(',', '.'))
            if validated_salary < 0:
                raise ValueError("Le salaire doit être positif.")
        except Exception as e:
            print(f"Erreur: Montant de salaire invalide '{salary}'. Détails: {e}")
            return RedirectResponse("/employees", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        if cleaned_cin:
            res_cin = await db.execute(select(Employee).where(Employee.cin == cleaned_cin))
            if res_cin.scalar_one_or_none():
                 print(f"Erreur: CIN {cleaned_cin} existe déjà.")
                 return RedirectResponse("/employees", status_code=status.HTTP_302_FOUND)

        new_employee = Employee(
            first_name=first_name,
            last_name=last_name,
            cin=cleaned_cin,
            position=position,
            branch_id=branch_id,
            salary=validated_salary, # --- SAUVEGARDER LE SALAIRE ---
            active=True
        )
        db.add(new_employee)
        await db.commit()
        await db.refresh(new_employee)

        await log(
            db, user['id'], "create", "employee", new_employee.id,
            branch_id, f"Création employé: {first_name} {last_name}, CIN: {cleaned_cin}, Salaire: {validated_salary}"
        )

    return RedirectResponse("/employees", status_code=status.HTTP_302_FOUND)


# --- MODIFICATION : Page d'Absences ---
@app.get("/attendance", response_class=HTMLResponse, name="attendance_page")
async def attendance_page(request: Request):
    """Page de gestion des absences."""
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    employees = []
    attendance_records = []

    async with AsyncSessionLocal() as db:
        if user["role"] == Role.admin.value:
            res_emp = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.last_name))
            # --- MODIFIÉ : Ne sélectionne que les absences ---
            res_att = await db.execute(select(Attendance).where(Attendance.atype == AttendanceType.absent).order_by(Attendance.date.desc(), Attendance.created_at.desc()).limit(100))
        elif user["role"] == Role.manager.value and user["branch_id"]:
            res_emp = await db.execute(select(Employee).where(Employee.branch_id == user["branch_id"], Employee.active == True).order_by(Employee.last_name))
            subquery = select(Employee.id).where(Employee.branch_id == user["branch_id"]).scalar_subquery()
            # --- MODIFIÉ : Ne sélectionne que les absences ---
            res_att = await db.execute(select(Attendance).where(Attendance.employee_id.in_(subquery), Attendance.atype == AttendanceType.absent).order_by(Attendance.date.desc(), Attendance.created_at.desc()).limit(100))
        else:
            res_emp = await db.execute(select(Employee).where(Employee.id == -1))
            res_att = await db.execute(select(Attendance).where(Attendance.id == -1))
        
        employees = res_emp.scalars().all()
        attendance_records = res_att.scalars().all()

    return templates.TemplateResponse(
        "attendance.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "attendance": attendance_records,
            "today_date": dt_date.today().isoformat(), # --- AJOUTÉ : Date du jour ---
            "app_name": APP_NAME,
        },
    )

# --- MODIFICATION : Création d'Absence ---
@app.post("/attendance/create", name="attendance_create")
async def attendance_create(
    request: Request,
    employee_id: int = Form(...),
    date: str = Form(...),
    # atype: str = Form(...), # --- SUPPRIMÉ ---
    note: str | None = Form(None),
):
    """Traitement du formulaire de création d'absence."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    try:
        attendance_date = dt_date.fromisoformat(date)
    except ValueError:
        print(f"Erreur: Format de date invalide reçu: {date}")
        return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = res_emp.scalar_one_or_none()
        if not employee:
             print(f"Erreur: Employé ID {employee_id} non trouvé pour marquer absence.")
             return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND)

        if user["role"] == Role.manager.value and user["branch_id"] != employee.branch_id:
             print(f"Erreur: Manager {user['id']} a tenté de marquer absence pour employé {employee_id} hors de son magasin.")
             return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND)

        new_attendance = Attendance(
            employee_id=employee_id,
            date=attendance_date,
            atype=AttendanceType.absent, # --- MODIFIÉ : Toujours 'absent' ---
            note=note,
            created_by=user["id"],
        )
        db.add(new_attendance)
        await db.commit()
        await db.refresh(new_attendance)

        await log(
            db, user['id'], "create", "attendance", new_attendance.id,
            employee.branch_id, f"Absence marquée pour Employé ID={employee_id} le {attendance_date}"
        )

    return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND)


# --- Page des Congés (reste admin-only) ---
@app.get("/leaves", response_class=HTMLResponse, name="leaves_page")
async def leaves_page(request: Request):
    """Page de gestion des congés (Admin seulement)."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        res_emp = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.last_name))
        employees = res_emp.scalars().all()
        res_leaves = await db.execute(select(Leave).order_by(Leave.start_date.desc()).limit(100))
        leaves = res_leaves.scalars().all()

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

@app.post("/leaves/create", name="leaves_create")
async def leaves_create(
    request: Request,
    employee_id: int = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    ltype: str = Form(...),
):
    """Traitement du formulaire de création de congé (Admin seulement)."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
         return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    try:
        sd = dt_date.fromisoformat(start_date)
        ed = dt_date.fromisoformat(end_date)
    except ValueError:
        return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

    if ed < sd:
        return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = res_emp.scalar_one_or_none()
        if not employee:
             return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

        new_leave = Leave(
            employee_id=employee_id,
            start_date=sd,
            end_date=ed,
            ltype=ltype,
            approved=False,
            created_by=user["id"],
        )
        db.add(new_leave)
        await db.commit()
        await db.refresh(new_leave)

        await log(
            db, user['id'], "create", "leave", new_leave.id,
            employee.branch_id, f"Demande congé pour Employé ID={employee_id}: {ltype} du {sd} au {ed}"
        )

    return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)


@app.post("/leaves/{leave_id}/approve", name="leaves_approve")
async def leaves_approve(leave_id: int, request: Request):
    """Approbation d'une demande de congé (Admin seulement)."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        res_leave = await db.execute(select(Leave).where(Leave.id == leave_id))
        leave_to_approve = res_leave.scalar_one_or_none()

        if not leave_to_approve:
            return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

        if leave_to_approve.approved:
            return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

        leave_to_approve.approved = True
        await db.commit()
        await db.refresh(leave_to_approve)

        res_emp = await db.execute(select(Employee).where(Employee.id == leave_to_approve.employee_id))
        employee = res_emp.scalar_one_or_none()
        await log(
            db, user['id'], "approve", "leave", leave_to_approve.id,
            employee.branch_id if employee else None, f"Congé approuvé pour Employé ID={leave_to_approve.employee_id}"
        )

    return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

# --- MODIFICATION : Page Avances ---
@app.get("/deposits", response_class=HTMLResponse, name="deposits_page")
async def deposits_page(request: Request):
    """Page de gestion des avances (Admin et Managers)."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    employees = []
    deposits_records = []

    async with AsyncSessionLocal() as db:
        emp_stmt = select(Employee).where(Employee.active == True)
        dep_stmt = select(Deposit)

        if user["role"] == Role.manager.value and user["branch_id"]:
            emp_stmt = emp_stmt.where(Employee.branch_id == user["branch_id"])
            dep_stmt = dep_stmt.join(Deposit.employee).where(Employee.branch_id == user["branch_id"])

        emp_stmt = emp_stmt.order_by(Employee.last_name)
        dep_stmt = dep_stmt.order_by(Deposit.date.desc(), Deposit.created_at.desc()).limit(100)

        res_emp = await db.execute(emp_stmt)
        res_dep = await db.execute(dep_stmt)

        employees = res_emp.scalars().all()
        deposits_records = res_dep.scalars().all()

    return templates.TemplateResponse(
        "deposits.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "deposits": deposits_records,
            "today_date": dt_date.today().isoformat(), # --- AJOUTÉ : Date du jour ---
            "app_name": APP_NAME,
        },
    )

@app.post("/deposits/create", name="deposits_create")
async def deposits_create(
    request: Request,
    employee_id: int = Form(...),
    amount: str = Form(...),
    date: str = Form(...),
    note: str | None = Form(None),
):
    """Traitement du formulaire de création d'avance (Admin et Managers)."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    try:
        deposit_amount = Decimal(amount.replace(',', '.'))
        if deposit_amount <= 0:
            raise ValueError("Le montant doit être positif.")
    except Exception as e:
        print(f"Erreur: Montant invalide reçu '{amount}'. Détails: {e}")
        return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)

    try:
        deposit_date = dt_date.fromisoformat(date)
    except ValueError:
        print(f"Erreur: Format de date invalide pour avance: {date}")
        return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = res_emp.scalar_one_or_none()
        if not employee:
             print(f"Erreur: Employé ID {employee_id} non trouvé pour avance.")
             return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)

        if user["role"] == Role.manager.value and user["branch_id"] != employee.branch_id:
             print(f"Erreur: Manager {user['id']} tentative avance pour employé {employee_id} hors magasin.")
             return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)

        new_deposit = Deposit(
            employee_id=employee_id,
            amount=deposit_amount,
            date=deposit_date,
            note=note,
            created_by=user["id"],
        )
        db.add(new_deposit)
        await db.commit()
        await db.refresh(new_deposit)

        await log(
            db, user['id'], "create", "deposit", new_deposit.id,
            employee.branch_id, f"Avance créée pour Employé ID={employee_id}: Montant={deposit_amount} le {deposit_date}"
        )

    return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)


# --- NOUVELLE PAGE : Rapport Employé ---
@app.get("/employee-report", response_class=HTMLResponse, name="employee_report_index")
async def employee_report_index(request: Request, employee_id: int | None = None):
    """Page du rapport individuel par employé (Admin seulement)."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    employees = []
    selected_employee = None
    absences = []
    leaves = []
    deposits = []
    pay_history = [] # Pour la paie

    async with AsyncSessionLocal() as db:
        # 1. Récupérer tous les employés pour le dropdown
        res_all_emp = await db.execute(select(Employee).order_by(Employee.last_name))
        employees = res_all_emp.scalars().all()

        # 2. Si un employé est sélectionné, récupérer ses détails
        if employee_id:
            res_emp = await db.execute(
                select(Employee).where(Employee.id == employee_id)
            )
            selected_employee = res_emp.scalar_one_or_none()

            if selected_employee:
                # 3. Récupérer les enregistrements liés
                res_abs = await db.execute(select(Attendance).where(Attendance.employee_id == employee_id, Attendance.atype == AttendanceType.absent).order_by(Attendance.date.desc()))
                absences = res_abs.scalars().all()

                res_leaves = await db.execute(select(Leave).where(Leave.employee_id == employee_id).order_by(Leave.start_date.desc()))
                leaves = res_leaves.scalars().all()

                res_deps = await db.execute(select(Deposit).where(Deposit.employee_id == employee_id).order_by(Deposit.date.desc()))
                deposits = res_deps.scalars().all()
                
                res_pay = await db.execute(select(Pay).where(Pay.employee_id == employee_id).order_by(Pay.date.desc()))
                pay_history = res_pay.scalars().all()

    return templates.TemplateResponse(
        "employee_report.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "selected_employee": selected_employee,
            "absences": absences,
            "leaves": leaves,
            "deposits": deposits,
            "pay_history": pay_history, # Passer l'historique de paie
            "app_name": APP_NAME,
        },
    )

# --- NOUVELLE PAGE : Paie Employé ---
@app.get("/pay-employee", response_class=HTMLResponse, name="pay_employee_page")
async def pay_employee_page(request: Request):
    """Page pour payer un employé (Admin seulement)."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    employees = []
    async with AsyncSessionLocal() as db:
        res_emp = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.last_name))
        employees = res_emp.scalars().all()

    return templates.TemplateResponse(
        "pay_employee.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "today_date": dt_date.today().isoformat(),
            "app_name": APP_NAME,
        },
    )

@app.post("/pay-employee", name="pay_employee_action")
async def pay_employee_action(
    request: Request,
    employee_id: int = Form(...),
    amount: str = Form(...),
    date: str = Form(...),
    pay_type: str = Form(...), # 'hebdomadaire' ou 'mensuel'
    note: str | None = Form(None),
):
    """Traitement du formulaire de paie."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    try:
        pay_amount = Decimal(amount.replace(',', '.'))
        if pay_amount <= 0:
            raise ValueError("Le montant doit être positif.")
    except Exception as e:
        print(f"Erreur: Montant de paie invalide '{amount}'. Détails: {e}")
        return RedirectResponse("/pay-employee", status_code=status.HTTP_302_FOUND)

    try:
        pay_date = dt_date.fromisoformat(date)
    except ValueError:
        print(f"Erreur: Format de date invalide pour paie: {date}")
        return RedirectResponse("/pay-employee", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = res_emp.scalar_one_or_none()
        if not employee:
             print(f"Erreur: Employé ID {employee_id} non trouvé pour paie.")
             return RedirectResponse("/pay-employee", status_code=status.HTTP_302_FOUND)

        new_pay = Pay(
            employee_id=employee_id,
            amount=pay_amount,
            date=pay_date,
            pay_type=pay_type,
            note=note,
            created_by=user["id"],
        )
        db.add(new_pay)
        await db.commit()
        await db.refresh(new_pay)

        await log(
            db, user['id'], "create", "pay", new_pay.id,
            employee.branch_id, f"Paiement enregistré pour Employé ID={employee_id}: Montant={pay_amount} ({pay_type}) le {pay_date}"
        )

    # Rediriger vers le rapport de l'employé payé
    return RedirectResponse(f"/employee-report?employee_id={employee_id}", status_code=status.HTTP_302_FOUND)


# --- NOUVELLE PAGE : Paramètres / Journal Filtré ---
@app.get("/settings", response_class=HTMLResponse, name="settings_page")
async def settings_page(request: Request):
    """Page de Paramètres, affichant un journal d'audit filtré (Admin seulement)."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    filtered_logs = []
    async with AsyncSessionLocal() as db:
        # Appeler 'latest' avec les filtres
        filtered_logs = await latest(
            db,
            user_role=user.get("role"),
            branch_id=user.get("branch_id"),
            entity_types=['leave', 'attendance', 'deposit', 'pay'] # On inclut la paie
        )

    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "user": user,
            "logs": filtered_logs,
            "app_name": APP_NAME,
        },
    )

# --- NOUVELLE ACTION : Vider les journaux ---
@app.post("/settings/clear-logs", name="clear_logs_action")
async def clear_logs_action(request: Request):
    """Vide toutes les données transactionnelles (Admin seulement)."""
    user = _get_user_from_session(request)
    # --- PERMISSION : Admin seulement ---
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        print(f"ACTION ADMIN (ID={user['id']}): Vidage des journaux transactionnels...")
        
        # 1. Vider les journaux d'audit
        await db.execute(delete(AuditLog))
        print("Logs d'audit vidés.")
        
        # 2. Vider les absences
        await db.execute(delete(Attendance))
        print("Absences vidées.")
        
        # 3. Vider les congés
        await db.execute(delete(Leave))
        print("Congés vidés.")
        
        # 4. Vider les avances
        await db.execute(delete(Deposit))
        print("Avances vidées.")
        
        # 5. Vider l'historique de paie
        await db.execute(delete(Pay))
        print("Historique de paie vidé.")
        
        await db.commit()
        print("Tous les journaux transactionnels ont été vidés.")

    return RedirectResponse("/settings", status_code=status.HTTP_302_FOUND)
