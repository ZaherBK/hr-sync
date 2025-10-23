"""
Point d'entrée de l'application FastAPI pour la gestion RH de la Bijouterie.

Définit l'application FastAPI, inclut les routeurs API, monte les fichiers statiques,
configure les templates Jinja2, et fournit des pages frontend simples basées sur HTML
pour la connexion, la gestion des employés, des présences, des congés et des avances.
Inclut également le middleware de session pour stocker les sessions utilisateur dans les cookies.
"""
import os
from datetime import timedelta, date as dt_date
# Utiliser Decimal pour les montants des avances
from decimal import Decimal

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, delete # Ajout de delete pour le seeding initial

# Importer AsyncSessionLocal pour les opérations directes dans main.py
from .db import engine, Base, AsyncSessionLocal
from .auth import authenticate_user, create_access_token, hash_password # Ajout de hash_password pour le seeding
# Importer tous les modèles, y compris Deposit
from .models import Attendance, Branch, Deposit, Employee, Leave, User, Role
from .audit import latest
# Importer tous les routeurs, y compris deposits
from .routers import users, branches, employees, attendance, leaves, deposits


APP_NAME = os.getenv("APP_NAME", "Bijouterie Zaher RH") # Nouveau nom par défaut

app = FastAPI(title=APP_NAME)

# Inclure tous les routeurs API
app.include_router(users.router)
app.include_router(branches.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(leaves.router)
app.include_router(deposits.router) # Inclure le nouveau routeur des avances

# Configuration des fichiers statiques et templates
BASE_DIR = os.path.dirname(__file__)
static_path = os.path.join(BASE_DIR, "frontend", "static")
templates_path = os.path.join(BASE_DIR, "frontend", "templates")

# Vérifier si les dossiers existent, sinon les créer (utile pour certains déploiements)
os.makedirs(static_path, exist_ok=True)
os.makedirs(templates_path, exist_ok=True)


app.mount(
    "/static",
    StaticFiles(directory=static_path),
    name="static",
)
templates = Jinja2Templates(directory=templates_path)
# Ajouter le middleware de session
# Assurez-vous que SECRET_KEY est bien défini dans vos variables d'environnement !
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY", "une_cle_secrete_tres_longue_et_aleatoire"))


@app.on_event("startup")
async def on_startup() -> None:
    """Créer les tables de la base de données et potentiellement ajouter les données initiales."""
    print("Événement de démarrage...")
    async with engine.begin() as conn:
        
        # --- LIGNE TEMPORAIRE À AJOUTER ---
        print("ATTENTION: Suppression de toutes les tables existantes pour mise à jour du schéma...")
        await conn.run_sync(Base.metadata.drop_all)
        print("Tables supprimées.")
        # --- FIN DE LA LIGNE TEMPORAIRE ---

        print("Création de toutes les nouvelles tables...")
        await conn.run_sync(Base.metadata.create_all)
        print("Tables OK.")

    # --- LOGIQUE DE SEEDING INITIAL (Exécuté une seule fois si la table user est vide) ---
    try:
        async with AsyncSessionLocal() as session:
            # Vérifier si l'utilisateur admin existe déjà
            res = await session.execute(select(User).where(User.email == "zaher@local"))
            if res.scalar_one_or_none() is None:
                print("Base de données vide, ajout des données initiales (seed)...")

                # Créer les magasins (Branches)
                branch_ariana = Branch(name="Magasin Ariana", city="Ariana")
                branch_nabeul = Branch(name="Magasin Nabeul", city="Nabeul")
                session.add_all([branch_ariana, branch_nabeul])
                await session.flush() # Pour obtenir les IDs des magasins avant de créer les utilisateurs

                print(f"Magasins créés : ID Ariana={branch_ariana.id}, ID Nabeul={branch_nabeul.id}")

                # Créer les utilisateurs (Admin/Managers)
                users_to_create = [
                    User(
                        email="zaher@local",
                        full_name="Zaher (Admin)",
                        role=Role.admin, # Rôle admin
                        hashed_password=hash_password("zah1405"), # Utiliser la fonction de hachage
                        is_active=True,
                        branch_id=None # L'admin n'est pas lié à un magasin spécifique
                    ),
                    User(
                        email="ariana@local",
                        full_name="Ariana (Manager)",
                        role=Role.manager, # Rôle manager
                        hashed_password=hash_password("ar123"),
                        is_active=True,
                        branch_id=branch_ariana.id # Lié au Magasin Ariana
                    ),
                    User(
                        email="nabeul@local",
                        full_name="Nabeul (Manager)",
                        role=Role.manager, # Rôle manager
                        hashed_password=hash_password("na123"),
                        is_active=True,
                        branch_id=branch_nabeul.id # Lié au Magasin Nabeul
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
    # L'admin voit toute l'activité, les managers voient l'activité de leur magasin
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
    if user: # Si déjà connecté, rediriger vers l'accueil
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
            # Re-afficher la page de login avec un message d'erreur
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "error": "Email ou mot de passe incorrect.",
                    "app_name": APP_NAME,
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        # Créer le token JWT (peut être utile pour l'API plus tard)
        token = create_access_token(
            {"sub": str(user.id), "role": user.role.value, "branch_id": user.branch_id},
            expires_delta=timedelta(days=7), # Expiration du token/session
        )

        # Stocker les informations utilisateur dans la session
        request.session["user"] = {
            "id": user.id,
            "email": user.email,
            "full_name": user.full_name,
            "role": user.role.value, # Stocker la valeur string de l'enum
            "branch_id": user.branch_id,
            "token": token, # Stocker le token dans la session peut être pratique
        }
        # Rediriger vers la page d'accueil après connexion réussie
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

@app.get("/logout", name="logout")
async def logout(request: Request):
    """Déconnexion de l'utilisateur."""
    request.session.clear() # Vider la session
    return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)


@app.get("/employees", response_class=HTMLResponse, name="employees_page")
async def employees_page(request: Request):
    """Page de gestion des employés."""
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        # L'admin voit tous les employés, les managers voient ceux de leur magasin
        if user["role"] == Role.admin.value:
            res_emp = await db.execute(select(Employee).order_by(Employee.last_name, Employee.first_name))
        elif user["role"] == Role.manager.value and user["branch_id"]:
            res_emp = await db.execute(select(Employee).where(Employee.branch_id == user["branch_id"]).order_by(Employee.last_name, Employee.first_name))
        else: # Normalement ne devrait pas arriver si les rôles sont bien gérés
             res_emp = await db.execute(select(Employee).where(Employee.id == -1)) # Retourne une liste vide

        employees = res_emp.scalars().all()

        # Récupérer tous les magasins pour le formulaire (l'admin peut choisir)
        res_branches = await db.execute(select(Branch).order_by(Branch.name))
        branches = res_branches.scalars().all()

        # Si l'utilisateur est manager, pré-sélectionner son magasin
        manager_branch_id = user.get("branch_id") if user["role"] == Role.manager.value else None

    return templates.TemplateResponse(
        "employees.html",
        {
            "request": request,
            "user": user,
            "employees": employees,
            "branches": branches,
            "manager_branch_id": manager_branch_id, # Pour le formulaire
            "app_name": APP_NAME,
        },
    )

@app.post("/employees/create", name="employees_create")
async def employees_create(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    cin: str | None = Form(None), # Récupérer le CIN
    position: str = Form(...),
    branch_id: int = Form(...),
):
    """Traitement du formulaire de création d'employé."""
    user = _get_user_from_session(request)
    # Seuls l'admin et les managers peuvent créer des employés
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    # Validation supplémentaire : un manager ne peut ajouter que dans son magasin
    if user["role"] == Role.manager.value and user["branch_id"] != branch_id:
         # Rediriger vers la page employée avec un message d'erreur (ou utiliser flash messages)
         # Pour l'instant, simple redirection. Idéalement, afficher une erreur.
         print(f"Erreur: Manager {user['id']} a tenté d'ajouter un employé au magasin {branch_id} au lieu de {user['branch_id']}")
         return RedirectResponse("/employees", status_code=status.HTTP_302_FOUND) # Ou status 403 Forbidden ?

    # Nettoyer le CIN (enlever espaces, etc.)
    cleaned_cin = cin.strip() if cin else None
    if cleaned_cin == "":
        cleaned_cin = None

    async with AsyncSessionLocal() as db:
        # Vérifier si le CIN existe déjà (s'il est fourni)
        if cleaned_cin:
            res_cin = await db.execute(select(Employee).where(Employee.cin == cleaned_cin))
            if res_cin.scalar_one_or_none():
                 # Idéalement, retourner à la page avec une erreur
                 print(f"Erreur: CIN {cleaned_cin} existe déjà.")
                 # Pour simplifier, on redirige juste pour l'instant
                 # TODO: Ajouter un mécanisme de message d'erreur (flash message)
                 return RedirectResponse("/employees", status_code=status.HTTP_302_FOUND)


        new_employee = Employee(
            first_name=first_name,
            last_name=last_name,
            cin=cleaned_cin, # Sauvegarder le CIN nettoyé
            position=position,
            branch_id=branch_id,
            active=True # Par défaut actif lors de la création
        )
        db.add(new_employee)
        await db.commit()
        await db.refresh(new_employee) # Pour obtenir l'ID si nécessaire pour l'audit

        # Log d'audit
        await log(
            db, user['id'], "create", "employee", new_employee.id,
            branch_id, f"Création employé: {first_name} {last_name}, CIN: {cleaned_cin}, Poste: {position}"
        )

    return RedirectResponse("/employees", status_code=status.HTTP_302_FOUND)


@app.get("/attendance", response_class=HTMLResponse, name="attendance_page")
async def attendance_page(request: Request):
    """Page de gestion des présences."""
    user = _get_user_from_session(request)
    if not user:
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        # L'admin voit tout, le manager voit les employés/présences de son magasin
        if user["role"] == Role.admin.value:
            res_emp = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.last_name))
            res_att = await db.execute(select(Attendance).order_by(Attendance.date.desc(), Attendance.created_at.desc()).limit(100)) # Limiter pour performance
        elif user["role"] == Role.manager.value and user["branch_id"]:
            res_emp = await db.execute(select(Employee).where(Employee.branch_id == user["branch_id"], Employee.active == True).order_by(Employee.last_name))
            # Filtrer les présences par les employés du magasin du manager
            subquery = select(Employee.id).where(Employee.branch_id == user["branch_id"]).scalar_subquery()
            res_att = await db.execute(select(Attendance).where(Attendance.employee_id.in_(subquery)).order_by(Attendance.date.desc(), Attendance.created_at.desc()).limit(100))
        else:
            res_emp = await db.execute(select(Employee).where(Employee.id == -1)) # Vide
            res_att = await db.execute(select(Attendance).where(Attendance.id == -1)) # Vide

        employees = res_emp.scalars().all()
        attendance_records = res_att.scalars().all()

    return templates.TemplateResponse(
        "attendance.html",
        {
            "request": request,
            "user": user,
            "employees": employees, # Employés actifs pour le formulaire/filtrage
            "attendance": attendance_records, # Présences récentes
            "app_name": APP_NAME,
        },
    )

@app.post("/attendance/create", name="attendance_create")
async def attendance_create(
    request: Request,
    employee_id: int = Form(...),
    date: str = Form(...),
    atype: str = Form(...), # 'present' ou 'absent'
    note: str | None = Form(None),
):
    """Traitement du formulaire de création de présence."""
    user = _get_user_from_session(request)
    # Admin et Managers peuvent marquer la présence
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        return RedirectResponse("/login", status_code=status.HTTP_302_FOUND)

    try:
        attendance_date = dt_date.fromisoformat(date)
    except ValueError:
        # Gérer l'erreur de format de date (idéalement retourner avec un message)
        print(f"Erreur: Format de date invalide reçu: {date}")
        return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND) # Simplifié

    async with AsyncSessionLocal() as db:
         # Vérifier si l'employé appartient au magasin du manager (si manager)
        res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = res_emp.scalar_one_or_none()
        if not employee:
             print(f"Erreur: Employé ID {employee_id} non trouvé pour marquer présence.")
             return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND)

        if user["role"] == Role.manager.value and user["branch_id"] != employee.branch_id:
             print(f"Erreur: Manager {user['id']} a tenté de marquer présence pour employé {employee_id} hors de son magasin.")
             return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND)

        # Vérifier si une entrée existe déjà pour cet employé à cette date ? Optionnel
        # res_existing = await db.execute(select(Attendance).where(Attendance.employee_id == employee_id, Attendance.date == attendance_date))
        # if res_existing.scalar_one_or_none():
        #     print(f"Avertissement: Entrée de présence existe déjà pour Employé {employee_id} le {attendance_date}.")
            # Que faire ? Mettre à jour ? Ignorer ? Retourner une erreur ? Pour l'instant, on ajoute quand même.

        new_attendance = Attendance(
            employee_id=employee_id,
            date=attendance_date,
            atype=atype, # Assumer que la valeur est correcte ('present'/'absent') - validation Pydantic si via API
            note=note,
            created_by=user["id"],
        )
        db.add(new_attendance)
        await db.commit()
        await db.refresh(new_attendance)

        # Log d'audit
        await log(
            db, user['id'], "create", "attendance", new_attendance.id,
            employee.branch_id, f"Présence marquée pour Employé ID={employee_id}: {atype} le {attendance_date}"
        )

    return RedirectResponse("/attendance", status_code=status.HTTP_302_FOUND)


# MODIFIÉ : Seul l'admin accède à la page des congés
@app.get("/leaves", response_class=HTMLResponse, name="leaves_page")
async def leaves_page(request: Request):
    """Page de gestion des congés (Admin seulement)."""
    user = _get_user_from_session(request)
    # Vérifier si admin, sinon rediriger
    if not user or user["role"] != Role.admin.value:
        # Peut-être rediriger vers l'accueil ou afficher une page "accès refusé"
        print(f"Accès refusé à la page des congés pour : {user.get('email', 'Non connecté')}")
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND) # Redirection vers l'accueil

    async with AsyncSessionLocal() as db:
        # L'admin voit tous les employés actifs pour pouvoir demander un congé pour eux
        res_emp = await db.execute(select(Employee).where(Employee.active == True).order_by(Employee.last_name))
        employees = res_emp.scalars().all()
        # L'admin voit toutes les demandes de congé
        res_leaves = await db.execute(select(Leave).order_by(Leave.start_date.desc()).limit(100)) # Limiter
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

# MODIFIÉ : Seul l'admin peut créer une demande de congé via le formulaire
@app.post("/leaves/create", name="leaves_create")
async def leaves_create(
    request: Request,
    employee_id: int = Form(...),
    start_date: str = Form(...),
    end_date: str = Form(...),
    ltype: str = Form(...), # 'paid', 'unpaid', 'sick'
):
    """Traitement du formulaire de création de congé (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
         print(f"Tentative de création de congé non autorisée par : {user.get('email', 'Non connecté')}")
         return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    try:
        sd = dt_date.fromisoformat(start_date)
        ed = dt_date.fromisoformat(end_date)
    except ValueError:
        print(f"Erreur: Format de date invalide pour congé: {start_date} ou {end_date}")
        return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND) # Simplifié

    if ed < sd:
        # Idéalement, retourner à la page avec une erreur
        print(f"Erreur: Date de fin ({ed}) antérieure à date de début ({sd}) pour congé.")
        return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND) # Simplifié

    async with AsyncSessionLocal() as db:
        # Vérifier si l'employé existe
        res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = res_emp.scalar_one_or_none()
        if not employee:
             print(f"Erreur: Employé ID {employee_id} non trouvé pour demande de congé.")
             return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

        new_leave = Leave(
            employee_id=employee_id,
            start_date=sd,
            end_date=ed,
            ltype=ltype, # Validation implicite si les valeurs du form sont correctes
            approved=False, # Non approuvé par défaut
            created_by=user["id"],
        )
        db.add(new_leave)
        await db.commit()
        await db.refresh(new_leave)

         # Log d'audit
        await log(
            db, user['id'], "create", "leave", new_leave.id,
            employee.branch_id, f"Demande congé pour Employé ID={employee_id}: {ltype} du {sd} au {ed}"
        )

    return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)


# MODIFIÉ : Seul l'admin peut approuver un congé
@app.post("/leaves/{leave_id}/approve", name="leaves_approve")
async def leaves_approve(leave_id: int, request: Request):
    """Approbation d'une demande de congé (Admin seulement)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        print(f"Tentative d'approbation de congé non autorisée par : {user.get('email', 'Non connecté')}")
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        res_leave = await db.execute(select(Leave).where(Leave.id == leave_id))
        leave_to_approve = res_leave.scalar_one_or_none()

        if not leave_to_approve:
            print(f"Erreur: Tentative d'approbation congé ID {leave_id} non trouvé.")
            return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND) # Simplifié

        if leave_to_approve.approved:
            print(f"Info: Congé ID {leave_id} est déjà approuvé.")
            # Pas besoin de re-commiter, on redirige juste
            return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

        leave_to_approve.approved = True
        await db.commit()
        await db.refresh(leave_to_approve) # Rafraîchir l'état

        # Log d'audit
        # Récupérer l'employé pour branch_id
        res_emp = await db.execute(select(Employee).where(Employee.id == leave_to_approve.employee_id))
        employee = res_emp.scalar_one_or_none()
        await log(
            db, user['id'], "approve", "leave", leave_to_approve.id,
            employee.branch_id if employee else None, f"Congé approuvé pour Employé ID={leave_to_approve.employee_id}"
        )

    return RedirectResponse("/leaves", status_code=status.HTTP_302_FOUND)

# --- NOUVELLES ROUTES POUR LES AVANCES (DEPOSITS) ---

@app.get("/deposits", response_class=HTMLResponse, name="deposits_page")
async def deposits_page(request: Request):
    """Page de gestion des avances (Admin et Managers)."""
    user = _get_user_from_session(request)
    # Accessible par admin et managers
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        print(f"Accès refusé page avances: {user.get('email', 'Non connecté')}")
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    async with AsyncSessionLocal() as db:
        # Préparer les requêtes
        emp_stmt = select(Employee).where(Employee.active == True)
        dep_stmt = select(Deposit)

        # Filtrer pour les managers
        if user["role"] == Role.manager.value and user["branch_id"]:
            emp_stmt = emp_stmt.where(Employee.branch_id == user["branch_id"])
            # Filtrer les avances en joignant sur Employee
            dep_stmt = dep_stmt.join(Deposit.employee).where(Employee.branch_id == user["branch_id"])

        # Ordonner et exécuter
        emp_stmt = emp_stmt.order_by(Employee.last_name)
        dep_stmt = dep_stmt.order_by(Deposit.date.desc(), Deposit.created_at.desc()).limit(100) # Limiter

        res_emp = await db.execute(emp_stmt)
        res_dep = await db.execute(dep_stmt)

        employees = res_emp.scalars().all()
        deposits_records = res_dep.scalars().all()

    return templates.TemplateResponse(
        "deposits.html",
        {
            "request": request,
            "user": user,
            "employees": employees, # Employés actifs pour le formulaire
            "deposits": deposits_records, # Avances récentes
            "app_name": APP_NAME,
        },
    )

@app.post("/deposits/create", name="deposits_create")
async def deposits_create(
    request: Request,
    employee_id: int = Form(...),
    amount: str = Form(...), # Récupérer comme string pour gérer la conversion/validation
    date: str = Form(...),
    note: str | None = Form(None),
):
    """Traitement du formulaire de création d'avance (Admin et Managers)."""
    user = _get_user_from_session(request)
    if not user or user["role"] not in (Role.admin.value, Role.manager.value):
        print(f"Tentative création avance non autorisée: {user.get('email', 'Non connecté')}")
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    # --- Validation du Montant ---
    try:
        # Convertir en Decimal pour précision
        deposit_amount = Decimal(amount.replace(',', '.')) # Remplacer virgule par point si nécessaire
        if deposit_amount <= 0:
            raise ValueError("Le montant doit être positif.")
        # Optionnel : Vérifier nombre de décimales si besoin strict
        if deposit_amount.as_tuple().exponent < -2:
             raise ValueError("Le montant ne doit pas avoir plus de 2 décimales.")

    except Exception as e:
        print(f"Erreur: Montant invalide reçu '{amount}'. Détails: {e}")
        # Idéalement, retourner avec message d'erreur
        return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND) # Simplifié

    # --- Validation de la Date ---
    try:
        deposit_date = dt_date.fromisoformat(date)
    except ValueError:
        print(f"Erreur: Format de date invalide pour avance: {date}")
        return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND) # Simplifié


    async with AsyncSessionLocal() as db:
        # Vérifier si l'employé existe et appartient au magasin (si manager)
        res_emp = await db.execute(select(Employee).where(Employee.id == employee_id))
        employee = res_emp.scalar_one_or_none()
        if not employee:
             print(f"Erreur: Employé ID {employee_id} non trouvé pour avance.")
             return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)

        if user["role"] == Role.manager.value and user["branch_id"] != employee.branch_id:
             print(f"Erreur: Manager {user['id']} tentative avance pour employé {employee_id} hors magasin.")
             return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)

        # Créer l'avance
        new_deposit = Deposit(
            employee_id=employee_id,
            amount=deposit_amount, # Utiliser le Decimal validé
            date=deposit_date, # Utiliser la date validée
            note=note,
            created_by=user["id"],
        )
        db.add(new_deposit)
        await db.commit()
        await db.refresh(new_deposit)

        # Log d'audit
        await log(
            db, user['id'], "create", "deposit", new_deposit.id,
            employee.branch_id, f"Avance créée pour Employé ID={employee_id}: Montant={deposit_amount} le {deposit_date}"
        )

    return RedirectResponse("/deposits", status_code=status.HTTP_302_FOUND)

# --- FIN DES NOUVELLES ROUTES ---
