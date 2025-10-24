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
# Assurez-vous que 'delete' est importé
from sqlalchemy import select, delete 

from .db import engine, Base, AsyncSessionLocal
from .auth import authenticate_user, create_access_token, hash_password
# Importer TOUS les modèles nécessaires pour le nettoyage
from .models import (
    Attendance, AttendanceType, Branch, Deposit, Employee, Leave, User, Role, Pay, PayType, AuditLog
)
from .audit import latest, log
from .routers import users, branches, employees, attendance, leaves, deposits


# --- MODIFIÉ : Nom par défaut changé ---
APP_NAME = os.getenv("APP_NAME", "Bijouterie Zaher")

app = FastAPI(title=APP_NAME)

# ... (tous vos app.include_router restent ici) ...
app.include_router(users.router)
app.include_router(branches.router)
app.include_router(employees.router)
app.include_router(attendance.router)
app.include_router(leaves.router)
app.include_router(deposits.router)


# ... (configuration static/templates) ...
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
                # ... (le reste de votre logique de seeding) ...
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


# ... (les fonctions _get_user_from_session, home, login_page, login_action, logout restent identiques) ...
# ... (les fonctions employees_page, employees_create restent identiques) ...
# ... (les fonctions attendance_page, attendance_create restent identiques) ...
# ... (les fonctions leaves_page, leaves_create, leaves_approve restent identiques) ...
# ... (les fonctions deposits_page, deposits_create restent identiques) ...
# ... (les fonctions employee_report_index, settings_page, pay_employee_page, pay_employee_action restent identiques) ...


# --- NOUVELLE ROUTE : Nettoyer les journaux ---
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
            # Note: nous avons besoin de l'objet 'employee' pour le branch_id,
            # mais l'admin n'a pas de branch_id. On met None.
            await log(
                db, user['id'], "delete", "all_logs", None,
                None, "Toutes les données transactionnelles ont été supprimées."
            )
            
        except Exception as e:
            await db.rollback()
            print(f"ERREUR lors du nettoyage des journaux: {e}")
            # Idéalement, on afficherait une erreur à l'utilisateur
            # Pour l'instant, on redirige juste.

    # Rediriger vers la page des paramètres
    return RedirectResponse(request.url_for('settings_page'), status_code=status.HTTP_302_FOUND)

# In your main app file (e.g., main.py or app.py)

@app.get("/")
def read_root():
    # This endpoint is just to confirm the API is running.
    return {"status": "ok", "message": "Welcome to Bijouterie Zaher API"}
