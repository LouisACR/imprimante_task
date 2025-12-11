"""
Database - Stockage SQLite des tâches imprimées.
Permet d'éviter la réimpression de tâches déjà traitées.
"""

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class PrintedTask:
    """Représente une tâche imprimée stockée en base."""
    task_hash: str  # Hash unique basé sur le contenu source
    source: str
    original_title: str
    label_title: str
    label_description: str
    score: int
    printed_at: datetime
    source_id: Optional[str] = None  # ID original de la source (email id, etc.)


class TaskDatabase:
    """
    Gestion de la base de données SQLite pour les tâches imprimées.
    
    L'ID de chaque tâche est un hash du contenu source (titre + description + source_id)
    calculé AVANT le traitement LLM, ce qui garantit qu'une même tâche source
    aura toujours le même ID, peu importe les variations du LLM.
    """
    
    DEFAULT_DB_PATH = Path("data/printed_tasks.db")
    
    def __init__(self, db_path: Optional[Path] = None):
        """
        Initialise la connexion à la base de données.
        
        Args:
            db_path: Chemin vers le fichier SQLite (défaut: data/printed_tasks.db)
        """
        self.db_path = db_path or self.DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _init_db(self):
        """Crée les tables si elles n'existent pas."""
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Table des tâches imprimées
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS printed_tasks (
                task_hash TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_id TEXT,
                original_title TEXT NOT NULL,
                label_title TEXT NOT NULL,
                label_description TEXT,
                score INTEGER NOT NULL,
                printed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Table des emails/sources déjà traités (pour éviter de re-appeler le LLM)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS processed_sources (
                source_hash TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                original_title TEXT NOT NULL,
                tasks_extracted INTEGER DEFAULT 0,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Index pour recherches par source
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_source ON printed_tasks(source)
        """)
        
        # Index pour recherches par date
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_printed_at ON printed_tasks(printed_at)
        """)
        
        # Index pour processed_sources
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_processed_source ON processed_sources(source)
        """)
        
        conn.commit()
    
    def _get_connection(self) -> sqlite3.Connection:
        """Retourne une connexion à la base (lazy loading)."""
        if self._conn is None:
            self._conn = sqlite3.connect(
                str(self.db_path),
                detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
            )
            self._conn.row_factory = sqlite3.Row
        return self._conn
    
    @staticmethod
    def compute_task_hash(source: str, source_id: str, title: str, description: Optional[str] = None) -> str:
        """
        Calcule un hash unique pour identifier une tâche.
        
        Ce hash est basé sur les données SOURCE (avant traitement LLM),
        ce qui garantit que la même tâche source aura toujours le même hash.
        
        Args:
            source: Nom de la source (gmail_pro, google_tasks_perso, etc.)
            source_id: ID unique de la source (message_id pour email, task_id pour Google Tasks)
            title: Titre original de la tâche/email
            description: Description ou snippet (optionnel)
            
        Returns:
            Hash SHA256 tronqué à 16 caractères
        """
        # Normaliser les entrées
        content = f"{source}|{source_id}|{title}|{description or ''}"
        content = content.lower().strip()
        
        # Calculer le hash
        hash_obj = hashlib.sha256(content.encode("utf-8"))
        return hash_obj.hexdigest()[:16]
    
    @staticmethod
    def compute_source_hash(source: str, source_id: str) -> str:
        """
        Calcule un hash unique pour identifier une source (email, etc.).
        
        Args:
            source: Nom de la source (gmail:pro, gmail:perso, etc.)
            source_id: ID unique dans la source (gmail_id, task_id, etc.)
            
        Returns:
            Hash SHA256 tronqué à 16 caractères
        """
        content = f"{source}|{source_id}"
        content = content.lower().strip()
        hash_obj = hashlib.sha256(content.encode("utf-8"))
        return hash_obj.hexdigest()[:16]
    
    def is_source_processed(self, source: str, source_id: str) -> bool:
        """
        Vérifie si une source (email, etc.) a déjà été traitée.
        
        Args:
            source: Nom de la source
            source_id: ID dans la source
            
        Returns:
            True si la source a déjà été traitée
        """
        source_hash = self.compute_source_hash(source, source_id)
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT 1 FROM processed_sources WHERE source_hash = ?",
            (source_hash,)
        )
        
        return cursor.fetchone() is not None
    
    def mark_source_processed(
        self,
        source: str,
        source_id: str,
        original_title: str,
        tasks_extracted: int = 0
    ) -> bool:
        """
        Marque une source comme traitée.
        
        Args:
            source: Nom de la source
            source_id: ID dans la source
            original_title: Titre original (sujet email, etc.)
            tasks_extracted: Nombre de tâches extraites
            
        Returns:
            True si l'insertion a réussi
        """
        source_hash = self.compute_source_hash(source, source_id)
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO processed_sources 
                (source_hash, source, source_id, original_title, tasks_extracted, processed_at)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                source_hash,
                source,
                source_id,
                original_title,
                tasks_extracted,
                datetime.now()
            ))
            conn.commit()
            return True
        except Exception:
            return False
    
    def is_already_printed(self, task_hash: str) -> bool:
        """
        Vérifie si une tâche a déjà été imprimée.
        
        Args:
            task_hash: Hash de la tâche à vérifier
            
        Returns:
            True si la tâche existe déjà en base
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT 1 FROM printed_tasks WHERE task_hash = ?",
            (task_hash,)
        )
        
        return cursor.fetchone() is not None
    
    def mark_as_printed(
        self,
        task_hash: str,
        source: str,
        original_title: str,
        label_title: str,
        label_description: str,
        score: int,
        source_id: Optional[str] = None
    ) -> bool:
        """
        Enregistre une tâche comme imprimée.
        
        Args:
            task_hash: Hash unique de la tâche
            source: Source de la tâche
            original_title: Titre original (avant reformulation LLM)
            label_title: Titre de l'étiquette (après LLM)
            label_description: Description de l'étiquette
            score: Score de priorité
            source_id: ID dans la source originale
            
        Returns:
            True si l'insertion a réussi, False si déjà existant
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT INTO printed_tasks 
                (task_hash, source, source_id, original_title, label_title, label_description, score, printed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_hash,
                source,
                source_id,
                original_title,
                label_title,
                label_description,
                score,
                datetime.now()
            ))
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # Déjà existant (clé primaire dupliquée)
            return False
    
    def get_printed_task(self, task_hash: str) -> Optional[PrintedTask]:
        """
        Récupère les détails d'une tâche imprimée.
        
        Args:
            task_hash: Hash de la tâche
            
        Returns:
            PrintedTask si trouvée, None sinon
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM printed_tasks WHERE task_hash = ?",
            (task_hash,)
        )
        
        row = cursor.fetchone()
        if row is None:
            return None
        
        return PrintedTask(
            task_hash=row["task_hash"],
            source=row["source"],
            source_id=row["source_id"],
            original_title=row["original_title"],
            label_title=row["label_title"],
            label_description=row["label_description"],
            score=row["score"],
            printed_at=row["printed_at"]
        )
    
    def get_recent_tasks(self, limit: int = 50) -> list[PrintedTask]:
        """
        Récupère les dernières tâches imprimées.
        
        Args:
            limit: Nombre maximum de tâches à retourner
            
        Returns:
            Liste des tâches les plus récentes
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute(
            "SELECT * FROM printed_tasks ORDER BY printed_at DESC LIMIT ?",
            (limit,)
        )
        
        return [
            PrintedTask(
                task_hash=row["task_hash"],
                source=row["source"],
                source_id=row["source_id"],
                original_title=row["original_title"],
                label_title=row["label_title"],
                label_description=row["label_description"],
                score=row["score"],
                printed_at=row["printed_at"]
            )
            for row in cursor.fetchall()
        ]
    
    def get_stats(self) -> dict:
        """
        Retourne des statistiques sur les tâches imprimées.
        
        Returns:
            dict avec total, par source, etc.
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Total
        cursor.execute("SELECT COUNT(*) FROM printed_tasks")
        total = cursor.fetchone()[0]
        
        # Par source
        cursor.execute("""
            SELECT source, COUNT(*) as count 
            FROM printed_tasks 
            GROUP BY source 
            ORDER BY count DESC
        """)
        by_source = {row["source"]: row["count"] for row in cursor.fetchall()}
        
        # Moyenne des scores
        cursor.execute("SELECT AVG(score) FROM printed_tasks")
        avg_score = cursor.fetchone()[0] or 0
        
        return {
            "total": total,
            "by_source": by_source,
            "average_score": round(avg_score, 1)
        }
    
    def clear_old_tasks(self, days: int = 90) -> int:
        """
        Supprime les tâches plus anciennes que X jours.
        
        Args:
            days: Nombre de jours à conserver
            
        Returns:
            Nombre de tâches supprimées
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        cursor.execute("""
            DELETE FROM printed_tasks 
            WHERE printed_at < datetime('now', ?)
        """, (f"-{days} days",))
        
        deleted = cursor.rowcount
        conn.commit()
        
        return deleted
    
    def close(self):
        """Ferme la connexion à la base."""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
