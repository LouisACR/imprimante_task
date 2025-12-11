"""
Source de données locale via fichier JSON.
Utile pour les tests et le développement sans API externe.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.inputs.base_input import BaseInput, InputError, InputRegistry
from src.processing.models import Task, TaskList, Priority, TaskStatus


class LocalJsonInput(BaseInput):
    """
    Source de tâches depuis un fichier JSON local.
    
    Format du fichier JSON attendu:
    {
        "tasks": [
            {
                "id": "task-001",
                "title": "Ma tâche",
                "description": "Description optionnelle",
                "priority": "high",
                "category": "Travail",
                "due_date": "2025-12-15"
            }
        ]
    }
    """
    
    SOURCE_NAME = "local_json"
    
    def __init__(self, file_path: Optional[Path] = None):
        """
        Initialise la source JSON locale.
        
        Args:
            file_path: Chemin vers le fichier JSON (optionnel)
        """
        super().__init__()
        self.file_path = file_path
        self._data: Optional[dict] = None
    
    def is_configured(self) -> bool:
        """Vérifie si un fichier JSON est spécifié et existe."""
        if self.file_path is None:
            return False
        return Path(self.file_path).exists()
    
    def connect(self) -> bool:
        """Charge le fichier JSON."""
        if not self.is_configured() or self.file_path is None:
            self._last_error = f"Fichier non trouvé: {self.file_path}"
            return False
        
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                self._data = json.load(f)
            self._connected = True
            return True
        except json.JSONDecodeError as e:
            self._last_error = f"Erreur de parsing JSON: {e}"
            return False
        except Exception as e:
            self._last_error = f"Erreur de lecture: {e}"
            return False
    
    def fetch_tasks(self, limit: Optional[int] = None) -> TaskList:
        """Récupère les tâches depuis le fichier JSON."""
        if not self._connected or self._data is None:
            raise InputError("Source non connectée. Appelez connect() d'abord.")
        
        tasks = []
        raw_tasks = self._data.get("tasks", [])
        
        for i, raw in enumerate(raw_tasks):
            if limit and len(tasks) >= limit:
                break
            
            try:
                task = self._parse_task(raw, i)
                tasks.append(task)
            except Exception as e:
                # Log mais continue avec les autres tâches
                print(f"⚠️ Erreur parsing tâche {i}: {e}")
        
        return tasks
    
    def _parse_task(self, raw: dict, index: int) -> Task:
        """Parse un objet JSON en Task."""
        # ID (généré si absent)
        task_id = raw.get("id", f"json-{index:03d}")
        
        # Titre (obligatoire)
        title = raw.get("title")
        if not title:
            raise ValueError("Champ 'title' manquant")
        
        # Description (optionnel)
        description = raw.get("description")
        
        # Priorité (avec conversion)
        priority_str = raw.get("priority", "medium")
        priority = Priority.from_string(priority_str)
        
        # Statut
        status_str = raw.get("status", "pending")
        status_map = {
            "pending": TaskStatus.PENDING,
            "in_progress": TaskStatus.IN_PROGRESS,
            "completed": TaskStatus.COMPLETED,
            "cancelled": TaskStatus.CANCELLED,
        }
        status = status_map.get(status_str, TaskStatus.PENDING)
        
        # Catégorie
        category = raw.get("category")
        
        # Dates
        due_date = None
        if raw.get("due_date"):
            try:
                due_date = datetime.fromisoformat(raw["due_date"])
            except ValueError:
                # Essayer format DD/MM/YYYY
                try:
                    due_date = datetime.strptime(raw["due_date"], "%d/%m/%Y")
                except ValueError:
                    pass
        
        created_at = datetime.now()
        if raw.get("created_at"):
            try:
                created_at = datetime.fromisoformat(raw["created_at"])
            except ValueError:
                pass
        
        return Task(
            id=task_id,
            source=self.SOURCE_NAME,
            title=title,
            description=description,
            priority=priority,
            status=status,
            category=category,
            due_date=due_date,
            created_at=created_at,
            raw_data=raw,
        )


# Enregistrer dans le registre
InputRegistry.register(LocalJsonInput)


def create_sample_json(output_path: Path):
    """
    Crée un fichier JSON d'exemple pour les tests.
    
    Args:
        output_path: Chemin où créer le fichier
    """
    sample_data = {
        "tasks": [
            {
                "id": "task-001",
                "title": "Finaliser le rapport Q4",
                "description": "Inclure les métriques de vente et les projections pour 2026",
                "priority": "high",
                "category": "Travail",
                "due_date": "2025-12-15"
            },
            {
                "id": "task-002",
                "title": "Appeler le dentiste",
                "description": "Prendre RDV pour contrôle annuel",
                "priority": "medium",
                "category": "Perso",
                "due_date": "2025-12-20"
            },
            {
                "id": "task-003",
                "title": "Acheter cadeaux Noël",
                "priority": "urgent",
                "category": "Perso",
                "due_date": "2025-12-23"
            },
            {
                "id": "task-004",
                "title": "Réviser le code PR #42",
                "description": "Feature: nouveau module de notification",
                "priority": "medium",
                "category": "Dev"
            },
            {
                "id": "task-005",
                "title": "Faire les courses",
                "priority": "low",
                "category": "Perso"
            }
        ]
    }
    
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sample_data, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Fichier d'exemple créé: {output_path}")


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    
    # Créer un fichier d'exemple
    sample_path = Path(__file__).parent.parent.parent / "data" / "sample_tasks.json"
    create_sample_json(sample_path)
    
    # Tester la lecture
    print("\n--- Test de LocalJsonInput ---")
    source = LocalJsonInput(sample_path)
    
    if source.connect():
        tasks = source.fetch_tasks()
        print(f"\n📋 {len(tasks)} tâches chargées:\n")
        
        for task in tasks:
            print(f"  {task.priority_symbol} [{task.category or 'N/A'}] {task.title}")
            if task.due_date_str:
                print(f"    📅 Échéance: {task.due_date_str}")
    else:
        print(f"❌ Erreur: {source.last_error}")
