"""
Configuration centralisée pour KanbanPrinter.
Charge les variables depuis .env et fournit des valeurs par défaut.
"""

import os
from pathlib import Path
from functools import lru_cache
from typing import Optional

from dotenv import load_dotenv
from pydantic import BaseModel, Field


# Charger le fichier .env depuis la racine du projet
PROJECT_ROOT = Path(__file__).parent.parent
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(ENV_FILE)


class Settings(BaseModel):
    """Configuration de l'application."""
    
    # === OpenAI ===
    openai_api_key: str = Field(
        default_factory=lambda: os.getenv("OPENAI_API_KEY", "")
    )
    openai_model: str = Field(
        default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    )
    
    # === Imprimante ===
    printer_name: str = Field(
        default_factory=lambda: os.getenv("PRINTER_NAME", "Munbyn ITPP941")
    )
    printer_dpi: int = Field(
        default_factory=lambda: int(os.getenv("PRINTER_DPI", "203"))
    )
    
    # === Étiquettes === (2" x 1" = 50.8mm x 25.4mm)
    label_width_mm: int = Field(
        default_factory=lambda: int(os.getenv("LABEL_WIDTH_MM", "51"))
    )
    label_height_mm: int = Field(
        default_factory=lambda: int(os.getenv("LABEL_HEIGHT_MM", "25"))
    )
    
    # === Google Tasks ===
    google_credentials_path: Optional[str] = Field(
        default_factory=lambda: os.getenv("GOOGLE_CREDENTIALS_PATH")
    )
    
    # === Chemins ===
    project_root: Path = PROJECT_ROOT
    output_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "output")
    fonts_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "assets" / "fonts")
    
    class Config:
        arbitrary_types_allowed = True
    
    @property
    def label_width_px(self) -> int:
        """Largeur de l'étiquette en pixels (basé sur DPI)."""
        return int((self.label_width_mm / 25.4) * self.printer_dpi)
    
    @property
    def label_height_px(self) -> int:
        """Hauteur de l'étiquette en pixels (basé sur DPI)."""
        return int((self.label_height_mm / 25.4) * self.printer_dpi)
    
    def validate_config(self) -> list[str]:
        """Vérifie la configuration et retourne les erreurs éventuelles."""
        errors = []
        
        if not self.openai_api_key:
            errors.append("OPENAI_API_KEY non configurée")
        
        if not self.printer_name:
            errors.append("PRINTER_NAME non configuré")
            
        return errors


@lru_cache()
def get_settings() -> Settings:
    """Retourne une instance singleton des settings."""
    return Settings()


# Pour un accès rapide
settings = get_settings()


if __name__ == "__main__":
    # Test de la configuration
    s = get_settings()
    print(f"Projet: {s.project_root}")
    print(f"Imprimante: {s.printer_name}")
    print(f"Taille étiquette: {s.label_width_mm}mm x {s.label_height_mm}mm")
    print(f"Taille en pixels: {s.label_width_px}px x {s.label_height_px}px")
    
    errors = s.validate_config()
    if errors:
        print(f"\n⚠️ Erreurs de configuration:")
        for e in errors:
            print(f"  - {e}")
    else:
        print("\n✅ Configuration valide")
