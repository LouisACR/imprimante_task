"""
Classe abstraite pour les sources de données (inputs).
Définit l'interface commune pour tous les connecteurs.
"""

from abc import ABC, abstractmethod
from typing import Optional

from src.processing.models import Task, TaskList


class InputError(Exception):
    """Erreur lors de la récupération des tâches depuis une source."""
    pass


class BaseInput(ABC):
    """
    Classe abstraite pour les sources de tâches.
    
    Pour ajouter une nouvelle source (ex: Todoist, Notion, Email):
    1. Créer un fichier dans src/inputs/ (ex: todoist.py)
    2. Hériter de BaseInput
    3. Implémenter les méthodes abstraites
    4. Ajouter la source dans le registre (voir InputRegistry)
    """
    
    # Nom unique de la source (à définir dans chaque sous-classe)
    SOURCE_NAME: str = "base"
    
    def __init__(self):
        """Initialise la source."""
        self._connected = False
        self._last_error: Optional[str] = None
    
    @property
    def source_name(self) -> str:
        """Retourne le nom de la source."""
        return self.SOURCE_NAME
    
    @property
    def is_connected(self) -> bool:
        """Vérifie si la source est connectée/configurée."""
        return self._connected
    
    @property
    def last_error(self) -> Optional[str]:
        """Retourne la dernière erreur rencontrée."""
        return self._last_error
    
    @abstractmethod
    def connect(self) -> bool:
        """
        Établit la connexion avec la source.
        
        Returns:
            True si la connexion a réussi
            
        Note:
            Doit être résilient (ne pas crasher si internet coupe).
            Stocker l'erreur dans self._last_error si échec.
        """
        pass
    
    @abstractmethod
    def fetch_tasks(self, limit: Optional[int] = None) -> TaskList:
        """
        Récupère les tâches depuis la source.
        
        Args:
            limit: Nombre maximum de tâches à récupérer (None = toutes)
            
        Returns:
            Liste de Task
            
        Raises:
            InputError: En cas d'erreur de récupération
        """
        pass
    
    @abstractmethod
    def is_configured(self) -> bool:
        """
        Vérifie si la source est correctement configurée.
        (API keys, credentials, etc.)
        
        Returns:
            True si la configuration est valide
        """
        pass
    
    def disconnect(self):
        """Ferme la connexion avec la source."""
        self._connected = False
    
    def _safe_fetch(self, limit: Optional[int] = None) -> TaskList:
        """
        Version sécurisée de fetch_tasks avec gestion d'erreurs.
        
        Returns:
            Liste de Task (vide si erreur)
        """
        try:
            return self.fetch_tasks(limit)
        except Exception as e:
            self._last_error = str(e)
            return []
    
    def __enter__(self):
        """Context manager: entrée."""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager: sortie."""
        self.disconnect()
        return False  # Ne pas supprimer les exceptions


class InputRegistry:
    """
    Registre des sources de données disponibles.
    Permet d'ajouter dynamiquement des sources.
    """
    
    _sources: dict[str, type[BaseInput]] = {}
    
    @classmethod
    def register(cls, source_class: type[BaseInput]):
        """
        Enregistre une source dans le registre.
        
        Args:
            source_class: Classe héritant de BaseInput
        """
        cls._sources[source_class.SOURCE_NAME] = source_class
    
    @classmethod
    def get(cls, source_name: str) -> Optional[type[BaseInput]]:
        """
        Récupère une classe source par son nom.
        
        Args:
            source_name: Nom de la source
            
        Returns:
            Classe source ou None si non trouvée
        """
        return cls._sources.get(source_name)
    
    @classmethod
    def list_sources(cls) -> list[str]:
        """Liste tous les noms de sources enregistrées."""
        return list(cls._sources.keys())
    
    @classmethod
    def create(cls, source_name: str, **kwargs) -> Optional[BaseInput]:
        """
        Crée une instance de source.
        
        Args:
            source_name: Nom de la source
            **kwargs: Arguments à passer au constructeur
            
        Returns:
            Instance de la source ou None
        """
        source_class = cls.get(source_name)
        if source_class:
            return source_class(**kwargs)
        return None
