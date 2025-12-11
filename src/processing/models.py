"""
Modèles de données pour KanbanPrinter.
Définit les structures Task et Label utilisées dans toute l'application.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Priority(Enum):
    """Niveaux de priorité des tâches."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"
    
    @classmethod
    def from_string(cls, value: str) -> "Priority":
        """Convertit une chaîne en Priority (avec fallback)."""
        value_lower = value.lower().strip()
        mapping = {
            "low": cls.LOW,
            "basse": cls.LOW,
            "medium": cls.MEDIUM,
            "moyenne": cls.MEDIUM,
            "normal": cls.MEDIUM,
            "high": cls.HIGH,
            "haute": cls.HIGH,
            "urgent": cls.URGENT,
            "urgente": cls.URGENT,
            "critique": cls.URGENT,
        }
        return mapping.get(value_lower, cls.MEDIUM)


class TaskStatus(Enum):
    """Statut d'une tâche."""
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


@dataclass
class Task:
    """
    Représente une tâche provenant de n'importe quelle source.
    Structure unifiée pour tous les inputs (Google Tasks, emails, etc.).
    """
    
    # Identifiants
    id: str
    source: str  # "google_tasks", "email", "local_json", etc.
    
    # Contenu principal
    title: str
    description: Optional[str] = None
    
    # Métadonnées
    priority: Priority = Priority.MEDIUM
    status: TaskStatus = TaskStatus.PENDING
    category: Optional[str] = None  # Projet, contexte, etc.
    
    # Dates
    created_at: datetime = field(default_factory=datetime.now)
    due_date: Optional[datetime] = None
    
    # Données brutes de la source (pour debug/traçabilité)
    raw_data: Optional[dict] = None
    
    def __post_init__(self):
        """Validation après initialisation."""
        # Tronquer le titre si trop long
        if len(self.title) > 100:
            self.title = self.title[:97] + "..."
    
    @property
    def short_title(self) -> str:
        """Titre court pour l'étiquette (max 40 chars)."""
        if len(self.title) <= 40:
            return self.title
        return self.title[:37] + "..."
    
    @property
    def due_date_str(self) -> str:
        """Date d'échéance formatée."""
        if not self.due_date:
            return ""
        return self.due_date.strftime("%d/%m/%Y")
    
    @property
    def priority_symbol(self) -> str:
        """Symbole visuel de priorité."""
        symbols = {
            Priority.LOW: "○",
            Priority.MEDIUM: "●",
            Priority.HIGH: "▲",
            Priority.URGENT: "⚠",
        }
        return symbols.get(self.priority, "●")
    
    @property
    def content_hash(self) -> str:
        """
        Hash unique basé sur le contenu source (avant traitement LLM).
        
        Ce hash garantit qu'une même tâche source aura toujours le même ID,
        permettant d'éviter les réimpressions de tâches déjà traitées.
        
        Pour les emails extraits par LLM, utilise les données source originales
        plus un index pour distinguer plusieurs tâches du même email.
        """
        # Pour les tâches extraites d'emails, utiliser les données source
        if self.raw_data and self.raw_data.get("extracted_from_email"):
            # Utiliser l'ID Gmail original et le sujet original
            gmail_id = self.raw_data.get("gmail_id", "")
            original_subject = self.raw_data.get("original_subject", "")
            # Extraire l'index de la tâche depuis l'ID (ex: "gmail-pro-abc123-task1" -> "task1")
            task_index = self.id.split("-")[-1] if "-task" in self.id else "task1"
            content = f"{self.source}|{gmail_id}|{original_subject}|{task_index}"
        else:
            # Pour les autres sources, utiliser id + title + description
            content = f"{self.source}|{self.id}|{self.title}|{self.description or ''}"
        
        content = content.lower().strip()
        hash_obj = hashlib.sha256(content.encode("utf-8"))
        return hash_obj.hexdigest()[:16]


@dataclass
class Label:
    """
    Représente une étiquette à imprimer.
    Contient les données formatées prêtes pour le rendu.
    """
    
    # Lignes de texte à afficher
    line1: str  # Titre principal
    line2: Optional[str] = None  # Sous-titre ou description
    line3: Optional[str] = None  # Métadonnées (date, catégorie)
    
    # Indicateurs visuels
    priority_indicator: str = "●"
    
    # Raison du choix (générée par le LLM)
    reason: Optional[str] = None
    
    # Référence à la tâche source
    task_id: Optional[str] = None
    source: Optional[str] = None
    
    @classmethod
    def from_task(cls, task: Task, scoring: Optional[dict] = None) -> "Label":
        """
        Crée un Label à partir d'une Task.
        Formate les données pour un affichage optimal sur 2" x 1".
        
        Args:
            task: Tâche source
            scoring: Dictionnaire du scoring LLM avec les champs optionnels:
                - label_title: Titre court optimisé
                - label_description: Description courte
                - reason: Raison du choix
                - priority: Priorité déterminée
        """
        scoring = scoring or {}
        
        # Toujours utiliser le titre/description du LLM s'ils existent (plus courts)
        title = scoring.get("label_title") or task.title
        description = scoring.get("label_description") or task.description
        
        # Ligne 1: Titre (priorité + texte)
        line1 = f"{task.priority_symbol} {title}"
        
        # Ligne 2: Description (le label_generator gère le wrapping)
        line2 = description.strip() if description else None
        
        # Ligne 3: Raison + date d'échéance
        meta_parts = []
        reason = scoring.get("reason", "")
        if reason:
            meta_parts.append(f"→ {reason}")
        if task.due_date_str:
            meta_parts.append(f"📅 {task.due_date_str}")
        line3 = " ".join(meta_parts) if meta_parts else None
        
        return cls(
            line1=line1,
            line2=line2,
            line3=line3,
            priority_indicator=task.priority_symbol,
            reason=reason,
            task_id=task.id,
            source=task.source,
        )


# Type alias pour les collections
TaskList = list[Task]
LabelList = list[Label]


if __name__ == "__main__":
    # Test des modèles
    task = Task(
        id="test-001",
        source="local_json",
        title="Finaliser le rapport trimestriel Q4",
        description="Inclure les métriques de vente et les projections",
        priority=Priority.HIGH,
        category="Travail",
        due_date=datetime(2025, 12, 15),
    )
    
    print(f"Task: {task.title}")
    print(f"Priority: {task.priority_symbol} {task.priority.value}")
    print(f"Due: {task.due_date_str}")
    
    label = Label.from_task(task)
    print(f"\n--- Label ---")
    print(f"L1: {label.line1}")
    print(f"L2: {label.line2}")
    print(f"L3: {label.line3}")
