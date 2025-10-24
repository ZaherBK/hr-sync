"""
Point d'entrée de l'application FastAPI pour la gestion RH de la Bijouterie.
"""
import os
from datetime import timedelta, date as dt_date, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import select, delete, func, case, extract
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .db import engine, Base, AsyncSessionLocal
from .auth import authenticate_user, create_access_token, hash_password, ACCESS_TOKEN_EXPIRE_MINUTES
# Importer TOUS les modèles nécessaires
from .models import (
    Attendance, AttendanceType, Branch, Deposit, Employee, Leave, User, Role, Pay, PayType, AuditLog
)
from .audit import latest, log
from .routers import users, branches, employees, attendance, leaves, deposits
from .deps import get_db, current_user # Assurez-vous que les dépendances sont là


# --- MODIFIÉ : Nom par défaut changé ---
APP_NAME = os.getenv("APP_NAME", "Bijouterie Zaher")

app = FastAPI(title=APP_NAME)

# --- 1. API Routers ---
app.include_router(users.router)
app.include_router(branches.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(leaves.router)
app.include_router(deposits.router)


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
        
        # --- !! RAPPEL IMPORTANT !! ---
        # Si vous avez encore la ligne 'await conn.run_sync(Base.metadata.drop_all)'
        # de la mise à jour précédente, VEUILLEZ LA SUPPRIMER MAINTENANT
        # que votre schéma de BDD (avec 'salary' et 'pay_history') est à jour.
        # --- FIN DU RAPPEL ---

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

# ❗️❗️ FONCTION AJOUTÉE (MANQUANTE) ❗️❗️
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

    # --- CORRECTION ---
    # Passez les arguments par mot-clé au lieu de l'objet utilisateur entier.
    # La fonction 'latest' utilise ces arguments pour filtrer les journaux.
    latest_logs = await latest(
        db, 
        user_role=user.get("role"), 
        branch_id=user.get("branch_id")
    )
    # --- FIN DE LA CORRECTION ---

    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
        "latest_logs": latest_logs # Utiliser la variable corrigée
    }
    return templates.TemplateResponse("dashboard.html", context)


@app.get("/login", response_class=HTMLResponse, name="login_page")
async def login_page(request: Request):
    """Affiche la page de connexion."""
    return templates.TemplateResponse("login.html", {"request": request, "app_name": APP_NAME})


# ❗️❗️ FONCTION AJOUTÉE (MANQUANTE) ❗️❗️
@app.post("/login", name="login_action")
async def login_action(
    request: Request, 
    db: AsyncSession = Depends(get_db), 
    username: str = Form(...), 
    password: str = Form(...)
):
    """Traite la soumission du formulaire de connexion."""
    user = await authenticate_user(db, username, password)
    if not user:
        # Re-affiche la page de connexion avec un message d'erreur
        context = {
            "request": request, 
            "app_name": APP_NAME, 
            "error": "Email ou mot de passe incorrect."
        }
        return templates.TemplateResponse("login.html", context, status_code=status.HTTP_401_UNAUTHORIZED)

    # Créer le token et le stocker dans la session
    access_token = create_access_token(
        data={"sub": user.email, "id": user.id, "role": user.role.value, "branch_id": user.branch_id}
    )
    request.session["user"] = {
        "email": user.email,
        "id": user.id,
        "full_name": user.full_name,
        "role": user.role.value,
        "branch_id": user.branch_id,
        "token": access_token # Stocker le token pour les appels API
    }
    
    # Rediriger vers la page d'accueil
    return RedirectResponse(request.url_for('home'), status_code=status.HTTP_302_FOUND)


# ❗️❗️ FONCTION AJOUTÉE (MANQUANTE) ❗️❗️
@app.get("/logout", name="logout")
async def logout(request: Request):
    """Déconnecte l'utilisateur en vidant la session."""
    request.session.clear()
    return RedirectResponse(request.url_for('login_page'), status_code=status.HTTP_302_FOUND)


# ❗️❗️ FONCTION AJOUTÉE (MANQUANTE) ❗️❗️
# Ceci est une version STUB. Vous devez la remplacer par votre vraie fonction.
@app.get("/settings", response_class=HTMLResponse, name="settings_page")
async def settings_page(request: Request):
    """Affiche la page des paramètres (réservée à l'admin)."""
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)
    
    context = {
        "request": request,
        "user": user,
        "app_name": APP_NAME,
    }
    # Assurez-vous d'avoir un template "settings.html"
    return templates.TemplateResponse("settings.html", context)


# ❗️❗️ PLACEHOLDER ❗️❗️
# --- AJOUTEZ VOS AUTRES FONCTIONS DE PAGE ICI ---
#
# @app.get("/employees", response_class=HTMLResponse, name="employees_page")
# async def employees_page(...):
#     ...
#
# @app.post("/employees/create", name="employees_create")
# async def employees_create(...):
#     ...
#
# @app.get("/attendance", response_class=HTMLResponse, name="attendance_page")
# async def attendance_page(...):
#     ...
#
# ... (et ainsi de suite pour deposits_page, leaves_page, etc.) ...
#
# --------------------------------------------------


# --- 6. Route de Nettoyage (Corrigée, une seule copie) ---
@app.post("/settings/clear-logs", name="clear_logs")
async def clear_transaction_logs(request: Request):
    """
    Supprime toutes les données transactionnelles (absences, congés, avances, paies, audits).
    NE SUPPRIME PAS les employés, utilisateurs, ou magasins.
    (Admin seulement)
    """
    user = _get_user_from_session(request)
    if not user or user["role"] != Role.admin.value:
        # Si pas admin, rediriger vers l'accueil
        return RedirectResponse("/", status_code=status.HTTP_302_FOUND)

    print(f"ACTION ADMIN (user {user['id']}): Nettoyage des journaux...")

    async with AsyncSessionLocal() as db:
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
            await log(
                db, user['id'], "delete", "all_logs", None,
                None, "Toutes les données transactionnelles ont été supprimées."
            )
            
        except Exception as e:
            await db.rollback()
            print(f"ERREUR lors du nettoyage des journaux: {e}")
            # Idéalement, on afficherait une erreur à l'utilisateur

    # Rediriger vers la page des paramètres
    return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)
