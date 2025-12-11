"""
Source Google Tasks - Récupère les tâches depuis Google Tasks.
Utilise l'API Google Tasks avec OAuth2.
"""

import pickle
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.inputs.base_input import BaseInput, InputError, InputRegistry
from src.processing.models import Task, TaskList, Priority, TaskStatus


class GoogleTasksInput(BaseInput):
    """
    Source de tâches depuis Google Tasks.
    Supporte plusieurs comptes Google avec tokens persistants.
    
    Configuration requise:
    1. Créer un projet Google Cloud: https://console.cloud.google.com/
    2. Activer l'API Google Tasks
    3. Créer des identifiants OAuth 2.0 (Application de bureau)
    4. Télécharger le fichier credentials.json
    """
    
    SOURCE_NAME = "google_tasks"
    
    # Scopes nécessaires (lecture seule)
    SCOPES = ["https://www.googleapis.com/auth/tasks.readonly"]
    
    def __init__(
        self,
        credentials_path: Optional[Path] = None,
        account_name: Optional[str] = None,
        tasklist_id: str = "@default",
        include_completed: bool = False
    ):
        """
        Initialise la source Google Tasks.
        
        Args:
            credentials_path: Chemin vers credentials.json
            account_name: Nom du compte (pour différencier les tokens)
            tasklist_id: ID de la liste de tâches (défaut: liste principale)
            include_completed: Inclure les tâches terminées
        """
        super().__init__()
        
        self.credentials_path = credentials_path or Path("config/google_credentials.json")
        self.account_name = account_name or "default"
        self.tasklist_id = tasklist_id
        self.include_completed = include_completed
        
        # Chemin du token pour ce compte (persiste les credentials)
        self.token_path = Path(f"config/google_tasks_token_{self.account_name}.pickle")
        
        self._service = None
    
    def is_configured(self) -> bool:
        """Vérifie si les credentials Google sont présents."""
        if self.credentials_path is None:
            return False
        return self.credentials_path.exists()
    
    def connect(self) -> bool:
        """Établit la connexion OAuth avec Google Tasks."""
        if not self.is_configured():
            self._last_error = f"Fichier credentials non trouvé: {self.credentials_path}"
            return False
        
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError:
            self._last_error = (
                "Packages Google non installés. Exécutez:\n"
                "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
            )
            return False
        
        creds = None
        
        # Charger le token existant
        if self.token_path.exists():
            with open(self.token_path, "rb") as token:
                creds = pickle.load(token)
        
        # Rafraîchir ou créer le token
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception:
                    creds = None
            
            if not creds:
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_path), self.SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    self._last_error = f"Erreur OAuth: {e}"
                    return False
            
            # Sauvegarder le token
            self.token_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.token_path, "wb") as token:
                pickle.dump(creds, token)
        
        # Créer le service Google Tasks
        try:
            self._service = build("tasks", "v1", credentials=creds)
            self._connected = True
            return True
        except Exception as e:
            self._last_error = f"Erreur création service Google Tasks: {e}"
            return False
    
    def fetch_tasks(self, limit: Optional[int] = None) -> TaskList:
        """Récupère les tâches depuis Google Tasks."""
        if not self._connected or not self._service:
            raise InputError("Non connecté à Google Tasks. Appelez connect() d'abord.")
        
        tasks = []
        
        try:
            # Récupérer les tâches
            params = {
                "tasklist": self.tasklist_id,
                "showCompleted": self.include_completed,
                "showHidden": False,
            }
            if limit:
                params["maxResults"] = limit
            
            results = self._service.tasks().list(**params).execute()
            items = results.get("items", [])
            
            for item in items:
                task = self._parse_google_task(item)
                if task:
                    tasks.append(task)
                    
        except Exception as e:
            raise InputError(f"Erreur récupération tâches: {e}")
        
        return tasks
    
    def _parse_google_task(self, item: dict) -> Optional[Task]:
        """Convertit une tâche Google Tasks en Task."""
        title = item.get("title", "").strip()
        if not title:
            return None  # Ignorer les tâches sans titre
        
        # Notes/description
        notes = item.get("notes", "")
        
        # Date d'échéance
        due_date = None
        if item.get("due"):
            try:
                # Format: 2025-12-15T00:00:00.000Z
                due_str = item["due"].replace("Z", "+00:00")
                due_date = datetime.fromisoformat(due_str.split("T")[0])
            except Exception:
                pass
        
        # Date de création/mise à jour
        created_at = datetime.now()
        if item.get("updated"):
            try:
                updated_str = item["updated"].replace("Z", "+00:00")
                created_at = datetime.fromisoformat(updated_str.replace("T", " ").split(".")[0])
            except Exception:
                pass
        
        # Statut
        status = TaskStatus.PENDING
        if item.get("status") == "completed":
            status = TaskStatus.COMPLETED
        
        # Priorité initiale (sera recalculée par le LLM)
        priority = Priority.MEDIUM
        
        # Calculer l'ancienneté pour la priorité initiale
        if created_at:
            days_old = (datetime.now() - created_at).days
            if days_old > 14:
                priority = Priority.HIGH
            elif days_old > 7:
                priority = Priority.MEDIUM
        
        # Échéance proche = priorité haute
        if due_date:
            days_until = (due_date - datetime.now()).days
            if days_until < 0:
                priority = Priority.URGENT  # En retard
            elif days_until <= 2:
                priority = Priority.HIGH
        
        return Task(
            id=f"gtasks-{item['id']}",
            source="google_tasks",
            title=title,
            description=notes if notes else None,
            priority=priority,
            status=status,
            category="Google Tasks",
            created_at=created_at,
            due_date=due_date,
            raw_data={
                "google_id": item["id"],
                "etag": item.get("etag"),
                "position": item.get("position"),
                "parent": item.get("parent"),
            },
        )
    
    def list_tasklists(self) -> list[dict]:
        """Liste toutes les listes de tâches disponibles."""
        if not self._connected or not self._service:
            return []
        
        try:
            results = self._service.tasklists().list().execute()
            return [
                {"id": tl["id"], "title": tl["title"]}
                for tl in results.get("items", [])
            ]
        except Exception:
            return []
    
    def disconnect(self):
        """Ferme la connexion."""
        self._service = None
        self._connected = False


class MultiGoogleTasksInput(BaseInput):
    """
    Agrège plusieurs comptes Google Tasks.
    Permet de récupérer les tâches de plusieurs comptes en une seule fois.
    """
    
    SOURCE_NAME = "multi_google_tasks"
    
    def __init__(
        self,
        account_names: list[str],
        credentials_path: Optional[Path] = None,
        include_completed: bool = False
    ):
        """
        Initialise le multi-source Google Tasks.
        
        Args:
            account_names: Liste des noms de comptes
            credentials_path: Chemin vers credentials.json
            include_completed: Inclure les tâches terminées
        """
        super().__init__()
        
        self.accounts = [
            GoogleTasksInput(
                credentials_path=credentials_path,
                account_name=name,
                include_completed=include_completed
            )
            for name in account_names
        ]
    
    def is_configured(self) -> bool:
        """Vérifie si au moins un compte est configuré."""
        return any(acc.is_configured() for acc in self.accounts)
    
    def connect(self) -> bool:
        """Connecte tous les comptes."""
        success = False
        for acc in self.accounts:
            if acc.connect():
                success = True
                print(f"    ✅ Google Tasks ({acc.account_name}): connecté")
            else:
                print(f"    ⚠️ Google Tasks ({acc.account_name}): {acc.last_error}")
        self._connected = success
        return success
    
    def fetch_tasks(self, limit: Optional[int] = None) -> TaskList:
        """Récupère les tâches de tous les comptes."""
        all_tasks = []
        for acc in self.accounts:
            if acc._connected:
                try:
                    tasks = acc.fetch_tasks(limit=limit)
                    # Ajouter le nom du compte à la source
                    for task in tasks:
                        task.source = f"google_tasks:{acc.account_name}"
                    all_tasks.extend(tasks)
                except Exception as e:
                    print(f"    ⚠️ Erreur {acc.account_name}: {e}")
        return all_tasks
    
    def disconnect(self):
        """Déconnecte tous les comptes."""
        for acc in self.accounts:
            acc.disconnect()
        self._connected = False


# Enregistrer dans le registre
InputRegistry.register(GoogleTasksInput)
InputRegistry.register(MultiGoogleTasksInput)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    print("=== Test Google Tasks Input ===\n")
    
    gtasks = GoogleTasksInput()
    
    print(f"Configuré: {gtasks.is_configured()}")
    
    if not gtasks.is_configured():
        print("\n⚠️ Pour utiliser Google Tasks:")
        print("1. Allez sur https://console.cloud.google.com/")
        print("2. Créez un projet et activez l'API Google Tasks")
        print("3. Créez des identifiants OAuth 2.0 (Application de bureau)")
        print("4. Téléchargez et placez le fichier dans: config/google_credentials.json")
    else:
        if gtasks.connect():
            print(f"✅ Connecté!")
            
            # Lister les listes de tâches
            tasklists = gtasks.list_tasklists()
            print(f"\n📋 Listes de tâches disponibles:")
            for tl in tasklists:
                print(f"  • {tl['title']} (ID: {tl['id']})")
            
            # Récupérer les tâches
            tasks = gtasks.fetch_tasks(limit=10)
            print(f"\n✅ {len(tasks)} tâches récupérées:")
            for task in tasks:
                status = "✓" if task.status == TaskStatus.COMPLETED else "○"
                due = f" (📅 {task.due_date_str})" if task.due_date else ""
                print(f"  {status} {task.priority_symbol} {task.title}{due}")
        else:
            print(f"❌ Erreur: {gtasks.last_error}")
