"""
Source Gmail - Récupère les emails de plusieurs comptes Google.
Utilise l'API Gmail avec OAuth2.
"""

import base64
import json
import logging
import pickle
import re
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

from src.inputs.base_input import BaseInput, InputError, InputRegistry
from src.processing.models import Task, TaskList, Priority, TaskStatus
from src.utils.resilience import with_retry, RetryConfig, classify_error, ErrorSeverity

logger = logging.getLogger("kanbanprinter.gmail")


class GmailInput(BaseInput):
    """
    Source de tâches depuis Gmail.
    Supporte plusieurs comptes Google.
    
    Configuration requise:
    1. Créer un projet Google Cloud: https://console.cloud.google.com/
    2. Activer l'API Gmail
    3. Créer des identifiants OAuth 2.0 (Application de bureau)
    4. Télécharger le fichier credentials.json
    """
    
    SOURCE_NAME = "gmail"
    
    # Scopes nécessaires (lecture seule)
    SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
    
    def __init__(
        self,
        credentials_path: Optional[Path] = None,
        account_name: Optional[str] = None,
        max_emails: int = 20,
        query: str = "is:unread OR is:starred"
    ):
        """
        Initialise la source Gmail.
        
        Args:
            credentials_path: Chemin vers credentials.json
            account_name: Nom du compte (pour différencier les tokens)
            max_emails: Nombre max d'emails à récupérer
            query: Requête Gmail (défaut: non lus ou favoris)
        """
        super().__init__()
        
        self.credentials_path = credentials_path or Path("config/google_credentials.json")
        self.account_name = account_name or "default"
        self.max_emails = max_emails
        self.query = query
        
        # Chemin du token pour ce compte
        self.token_path = Path(f"config/gmail_token_{self.account_name}.pickle")
        
        self._service = None
    
    def is_configured(self) -> bool:
        """Vérifie si les credentials Google sont présents."""
        if self.credentials_path is None:
            return False
        return self.credentials_path.exists()
    
    def connect(self) -> bool:
        """Établit la connexion OAuth avec Gmail."""
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
            try:
                with open(self.token_path, "rb") as token:
                    creds = pickle.load(token)
            except Exception as e:
                logger.warning(f"Erreur lecture token {self.account_name}: {e}")
                creds = None
        
        # Rafraîchir ou créer le token
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    logger.info(f"Token Gmail '{self.account_name}' rafraîchi")
                except Exception as e:
                    logger.warning(f"Échec refresh token '{self.account_name}': {e}")
                    # En mode daemon, on ne peut pas demander une nouvelle auth
                    # On garde l'erreur pour que le circuit breaker gère
                    self._last_error = f"Token expiré, réauthentification nécessaire: {e}"
                    creds = None
            
            if not creds:
                # Vérifier si on est en mode interactif (terminal disponible)
                import sys
                if not sys.stdin.isatty():
                    self._last_error = (
                        f"Token absent/invalide pour '{self.account_name}'. "
                        "Exécutez le script en mode interactif pour autoriser."
                    )
                    return False
                
                try:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        str(self.credentials_path), self.SCOPES
                    )
                    creds = flow.run_local_server(port=0)
                except Exception as e:
                    self._last_error = f"Erreur OAuth: {e}"
                    return False
            
            # Sauvegarder le token
            if creds:
                try:
                    self.token_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(self.token_path, "wb") as token:
                        pickle.dump(creds, token)
                except Exception as e:
                    logger.warning(f"Impossible de sauvegarder le token: {e}")
        
        # Créer le service Gmail
        try:
            self._service = build("gmail", "v1", credentials=creds)
            self._connected = True
            self._creds = creds  # Garder une référence pour refresh
            return True
        except Exception as e:
            self._last_error = f"Erreur création service Gmail: {e}"
            return False
    
    def _refresh_credentials_if_needed(self) -> bool:
        """Rafraîchit les credentials si nécessaire (pour les longues sessions)."""
        if not hasattr(self, '_creds') or not self._creds:
            return False
        
        try:
            if self._creds.expired and self._creds.refresh_token:
                from google.auth.transport.requests import Request
                self._creds.refresh(Request())
                
                # Sauvegarder le nouveau token
                with open(self.token_path, "wb") as token:
                    pickle.dump(self._creds, token)
                
                logger.info(f"Token Gmail '{self.account_name}' auto-rafraîchi")
                return True
        except Exception as e:
            logger.error(f"Échec auto-refresh token: {e}")
            self._connected = False
            return False
        
        return True
    
    def fetch_tasks(self, limit: Optional[int] = None) -> TaskList:
        """Récupère les emails et les convertit en tâches."""
        if not self._connected or not self._service:
            raise InputError("Non connecté à Gmail. Appelez connect() d'abord.")
        
        # Rafraîchir les credentials si nécessaire
        self._refresh_credentials_if_needed()
        
        limit = limit or self.max_emails
        tasks = []
        
        # Utiliser retry avec backoff pour les appels API
        @with_retry(RetryConfig(max_retries=3, base_delay=2.0))
        def _fetch_messages():
            return self._service.users().messages().list(
                userId="me",
                q=self.query,
                maxResults=limit
            ).execute()
        
        try:
            results = _fetch_messages()
            messages = results.get("messages", [])
            
            for msg_info in messages:
                try:
                    task = self._fetch_single_email(msg_info["id"])
                    if task:
                        tasks.append(task)
                except Exception as e:
                    # Log mais continue avec les autres emails
                    logger.warning(f"Erreur parsing email {msg_info['id']}: {e}")
            
        except Exception as e:
            severity = classify_error(e)
            if severity == ErrorSeverity.TRANSIENT:
                # Erreur réseau temporaire
                raise InputError(f"Erreur réseau Gmail (temporaire): {e}")
            elif severity == ErrorSeverity.RECOVERABLE:
                # Token expiré ou problème d'auth
                self._connected = False
                raise InputError(f"Erreur auth Gmail: {e}")
            else:
                raise InputError(f"Erreur récupération emails: {e}")
        
        return tasks
    
    def _fetch_single_email(self, msg_id: str) -> Optional[Task]:
        """Récupère un email avec retry."""
        @with_retry(RetryConfig(max_retries=2, base_delay=1.0))
        def _get_message():
            return self._service.users().messages().get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
        
        msg = _get_message()
        return self._parse_email_to_task(msg)
    
    def _parse_email_to_task(self, msg: dict) -> Optional[Task]:
        """Convertit un email Gmail en Task."""
        headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
        
        subject = headers.get("Subject", "(Sans sujet)")
        from_addr = headers.get("From", "")
        date_str = headers.get("Date", "")
        
        # Parser l'expéditeur
        sender_match = re.match(r"(.+?)\s*<(.+?)>", from_addr)
        if sender_match:
            sender_name = sender_match.group(1).strip().strip('"')
        else:
            sender_name = from_addr.split("@")[0] if "@" in from_addr else from_addr
        
        # Parser la date
        created_at = datetime.now()
        if date_str:
            try:
                created_at = parsedate_to_datetime(date_str)
            except Exception:
                pass
        
        # Déterminer la priorité initiale basée sur les labels Gmail
        labels = msg.get("labelIds", [])
        priority = Priority.MEDIUM
        
        if "STARRED" in labels:
            priority = Priority.HIGH
        if "IMPORTANT" in labels:
            priority = Priority.HIGH
        
        # Extraire un extrait du contenu (snippet)
        snippet = msg.get("snippet", "")[:200]
        
        # Créer la description
        description = f"De: {sender_name}\n{snippet}" if snippet else f"De: {sender_name}"
        
        return Task(
            id=f"gmail-{self.account_name}-{msg['id']}",
            source=f"gmail:{self.account_name}",
            title=subject,
            description=description,
            priority=priority,
            status=TaskStatus.PENDING,
            category="Email",
            created_at=created_at,
            raw_data={
                "gmail_id": msg["id"],
                "thread_id": msg.get("threadId"),
                "labels": labels,
                "from": from_addr,
                "snippet": snippet,
            },
        )
    
    def disconnect(self):
        """Ferme la connexion."""
        self._service = None
        self._connected = False


# Enregistrer dans le registre
InputRegistry.register(GmailInput)


class MultiGmailInput(BaseInput):
    """
    Gère plusieurs comptes Gmail en une seule source.
    """
    
    SOURCE_NAME = "multi_gmail"
    
    def __init__(
        self,
        accounts: list[dict],
        credentials_path: Optional[Path] = None
    ):
        """
        Initialise la source multi-comptes Gmail.
        
        Args:
            accounts: Liste de configs [{"name": "perso"}, {"name": "pro", "query": "is:unread"}]
            credentials_path: Chemin partagé vers credentials.json
        """
        super().__init__()
        
        self.credentials_path = credentials_path or Path("config/google_credentials.json")
        self.accounts_config = accounts
        self._gmail_sources: list[GmailInput] = []
    
    def is_configured(self) -> bool:
        """Vérifie si les credentials sont présents."""
        return self.credentials_path.exists()
    
    def connect(self) -> bool:
        """Connecte tous les comptes."""
        if not self.is_configured():
            self._last_error = f"Fichier credentials non trouvé: {self.credentials_path}"
            return False
        
        self._gmail_sources = []
        all_connected = True
        
        for account in self.accounts_config:
            name = account.get("name", "default")
            query = account.get("query", "is:unread OR is:starred")
            max_emails = account.get("max_emails", 10)
            
            source = GmailInput(
                credentials_path=self.credentials_path,
                account_name=name,
                max_emails=max_emails,
                query=query
            )
            
            print(f"  📧 Connexion compte Gmail '{name}'...")
            if source.connect():
                self._gmail_sources.append(source)
                print(f"    ✅ Connecté")
            else:
                print(f"    ❌ Échec: {source.last_error}")
                all_connected = False
        
        self._connected = len(self._gmail_sources) > 0
        return self._connected
    
    def fetch_tasks(self, limit: Optional[int] = None) -> TaskList:
        """Récupère les emails de tous les comptes."""
        all_tasks = []
        
        for source in self._gmail_sources:
            try:
                tasks = source.fetch_tasks(limit)
                all_tasks.extend(tasks)
            except Exception as e:
                print(f"⚠️ Erreur {source.account_name}: {e}")
        
        return all_tasks
    
    def disconnect(self):
        """Déconnecte tous les comptes."""
        for source in self._gmail_sources:
            source.disconnect()
        self._gmail_sources = []
        self._connected = False


InputRegistry.register(MultiGmailInput)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    print("=== Test Gmail Input ===\n")
    
    # Test simple compte
    gmail = GmailInput(account_name="test")
    
    print(f"Configuré: {gmail.is_configured()}")
    
    if not gmail.is_configured():
        print("\n⚠️ Pour utiliser Gmail:")
        print("1. Allez sur https://console.cloud.google.com/")
        print("2. Créez un projet et activez l'API Gmail")
        print("3. Créez des identifiants OAuth 2.0 (Application de bureau)")
        print("4. Téléchargez et placez le fichier dans: config/google_credentials.json")
    else:
        if gmail.connect():
            print(f"✅ Connecté!")
            tasks = gmail.fetch_tasks(limit=5)
            print(f"\n📧 {len(tasks)} emails récupérés:")
            for task in tasks:
                print(f"  • {task.title[:50]}...")
        else:
            print(f"❌ Erreur: {gmail.last_error}")
