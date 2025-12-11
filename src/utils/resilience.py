"""
Utilitaires de résilience - Retry, backoff, gestion d'erreurs.
Pour permettre au script de tourner en arrière-plan de manière robuste.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from functools import wraps
from typing import Callable, Optional, TypeVar, Any

# Configuration du logging
logger = logging.getLogger("kanbanprinter")


class ErrorSeverity(Enum):
    """Niveau de sévérité des erreurs."""
    TRANSIENT = "transient"      # Erreur temporaire (réseau, rate limit) - retry automatique
    RECOVERABLE = "recoverable"  # Erreur récupérable (token expiré) - besoin d'action
    FATAL = "fatal"              # Erreur fatale (config manquante) - arrêt


@dataclass
class RetryConfig:
    """Configuration pour les retries."""
    max_retries: int = 3
    base_delay: float = 1.0          # Délai initial en secondes
    max_delay: float = 300.0         # Délai max (5 minutes)
    exponential_base: float = 2.0    # Facteur d'augmentation
    jitter: float = 0.1              # Variance aléatoire (10%)


@dataclass
class SourceHealth:
    """État de santé d'une source de données."""
    source_name: str
    is_healthy: bool = True
    consecutive_failures: int = 0
    last_success: Optional[datetime] = None
    last_failure: Optional[datetime] = None
    last_error: Optional[str] = None
    next_retry: Optional[datetime] = None
    total_failures: int = 0
    total_successes: int = 0
    
    # Seuils de circuit breaker
    failure_threshold: int = 5       # Nombre d'échecs avant "ouverture"
    recovery_timeout: float = 300.0  # Temps avant réessai (5 minutes)
    
    @property
    def is_circuit_open(self) -> bool:
        """Le circuit est-il ouvert (source désactivée temporairement) ?"""
        if self.consecutive_failures < self.failure_threshold:
            return False
        
        # Vérifier si le timeout de récupération est passé
        if self.next_retry and datetime.now() >= self.next_retry:
            return False  # Permettre un nouvel essai
        
        return True
    
    def record_success(self):
        """Enregistre un succès."""
        self.is_healthy = True
        self.consecutive_failures = 0
        self.last_success = datetime.now()
        self.total_successes += 1
        self.next_retry = None
        self.last_error = None
    
    def record_failure(self, error: str):
        """Enregistre un échec."""
        self.consecutive_failures += 1
        self.total_failures += 1
        self.last_failure = datetime.now()
        self.last_error = error
        
        if self.consecutive_failures >= self.failure_threshold:
            self.is_healthy = False
            self.next_retry = datetime.now() + timedelta(seconds=self.recovery_timeout)


class SourceHealthMonitor:
    """Moniteur de santé pour toutes les sources."""
    
    def __init__(self):
        self._sources: dict[str, SourceHealth] = {}
    
    def get_health(self, source_name: str) -> SourceHealth:
        """Récupère ou crée l'état de santé d'une source."""
        if source_name not in self._sources:
            self._sources[source_name] = SourceHealth(source_name=source_name)
        return self._sources[source_name]
    
    def should_skip(self, source_name: str) -> bool:
        """Vérifie si une source doit être ignorée (circuit ouvert)."""
        health = self.get_health(source_name)
        return health.is_circuit_open
    
    def record_success(self, source_name: str):
        """Enregistre un succès pour une source."""
        self.get_health(source_name).record_success()
    
    def record_failure(self, source_name: str, error: str):
        """Enregistre un échec pour une source."""
        self.get_health(source_name).record_failure(error)
    
    def get_summary(self) -> dict:
        """Résumé de l'état de toutes les sources."""
        return {
            name: {
                "healthy": health.is_healthy,
                "failures": health.consecutive_failures,
                "last_error": health.last_error,
                "circuit_open": health.is_circuit_open,
            }
            for name, health in self._sources.items()
        }


# Instance globale du moniteur
health_monitor = SourceHealthMonitor()


def classify_error(error: Exception) -> ErrorSeverity:
    """
    Classifie une erreur selon sa sévérité.
    
    Args:
        error: L'exception à classifier
        
    Returns:
        ErrorSeverity indiquant comment gérer l'erreur
    """
    error_str = str(error).lower()
    error_type = type(error).__name__
    
    # Erreurs transitoires (réseau, rate limiting)
    transient_patterns = [
        "timeout", "timed out", "connection", "network",
        "rate limit", "quota", "too many requests", "429",
        "503", "502", "504", "temporarily unavailable",
        "ssl", "certificate", "handshake",
        "reset by peer", "broken pipe",
    ]
    
    for pattern in transient_patterns:
        if pattern in error_str:
            return ErrorSeverity.TRANSIENT
    
    # Erreurs récupérables (authentification)
    recoverable_patterns = [
        "token", "expired", "invalid_grant", "unauthorized",
        "401", "403", "refresh", "credentials",
    ]
    
    for pattern in recoverable_patterns:
        if pattern in error_str:
            return ErrorSeverity.RECOVERABLE
    
    # Certains types d'exceptions sont toujours transitoires
    transient_exceptions = [
        "TimeoutError", "ConnectionError", "ConnectionResetError",
        "BrokenPipeError", "OSError", "socket.error",
    ]
    
    if error_type in transient_exceptions:
        return ErrorSeverity.TRANSIENT
    
    # Par défaut, considérer comme récupérable (pas fatal)
    return ErrorSeverity.RECOVERABLE


T = TypeVar('T')


def with_retry(
    config: Optional[RetryConfig] = None,
    on_retry: Optional[Callable[[int, Exception], None]] = None,
) -> Callable:
    """
    Décorateur pour ajouter du retry avec backoff exponentiel.
    
    Args:
        config: Configuration de retry (utilise défauts si None)
        on_retry: Callback appelé avant chaque retry (retry_num, exception)
        
    Usage:
        @with_retry()
        def fetch_data():
            ...
            
        @with_retry(RetryConfig(max_retries=5))
        def risky_operation():
            ...
    """
    config = config or RetryConfig()
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            import random
            
            last_exception = None
            
            for attempt in range(config.max_retries + 1):
                try:
                    return func(*args, **kwargs)
                    
                except Exception as e:
                    last_exception = e
                    severity = classify_error(e)
                    
                    # Ne pas retry les erreurs fatales
                    if severity == ErrorSeverity.FATAL:
                        logger.error(f"Erreur fatale: {e}")
                        raise
                    
                    # Dernier essai ?
                    if attempt >= config.max_retries:
                        logger.warning(f"Échec après {config.max_retries} tentatives: {e}")
                        raise
                    
                    # Calculer le délai avec backoff exponentiel
                    delay = min(
                        config.base_delay * (config.exponential_base ** attempt),
                        config.max_delay
                    )
                    
                    # Ajouter du jitter pour éviter les "thundering herds"
                    jitter_amount = delay * config.jitter * random.random()
                    delay += jitter_amount
                    
                    # Callback de retry
                    if on_retry:
                        on_retry(attempt + 1, e)
                    else:
                        logger.info(
                            f"Retry {attempt + 1}/{config.max_retries} après {delay:.1f}s "
                            f"({type(e).__name__}: {str(e)[:50]})"
                        )
                    
                    time.sleep(delay)
            
            # Ne devrait jamais arriver, mais au cas où
            raise last_exception
        
        return wrapper
    return decorator


def safe_execute(
    func: Callable[..., T],
    *args,
    default: Optional[T] = None,
    source_name: Optional[str] = None,
    **kwargs
) -> tuple[Optional[T], Optional[Exception]]:
    """
    Exécute une fonction de manière sécurisée.
    
    Args:
        func: Fonction à exécuter
        *args: Arguments positionnels
        default: Valeur par défaut en cas d'erreur
        source_name: Nom de la source (pour le monitoring)
        **kwargs: Arguments nommés
        
    Returns:
        Tuple (résultat, exception) - exception est None si succès
    """
    try:
        result = func(*args, **kwargs)
        
        if source_name:
            health_monitor.record_success(source_name)
        
        return result, None
        
    except Exception as e:
        if source_name:
            health_monitor.record_failure(source_name, str(e))
        
        logger.error(f"Erreur safe_execute: {e}")
        return default, e
