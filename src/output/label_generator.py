"""
Générateur d'étiquettes avec Pillow.
Crée des images optimisées pour impression thermique 6cm x 3cm.
"""

import sys
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

# Ajouter le chemin parent pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config.settings import get_settings
from src.processing.models import Label, Priority


class LabelGenerator:
    """
    Génère des images d'étiquettes pour impression thermique.
    Optimisé pour Munbyn ITPP941 sur étiquettes prédécoupées 6cm x 3cm.
    """
    
    def __init__(self):
        self.settings = get_settings()
        self.width = self.settings.label_width_px   # ~480px à 203 DPI
        self.height = self.settings.label_height_px  # ~240px à 203 DPI
        
        # Marges internes (en pixels) - ajustées pour 2"x1"
        self.margin_x = 10
        self.margin_y = 6
        
        # Zone utilisable
        self.content_width = self.width - (2 * self.margin_x)
        self.content_height = self.height - (2 * self.margin_y)
        
        # Charger les polices
        self._load_fonts()
    
    def _load_fonts(self):
        """Charge les polices avec fallback sur les polices système."""
        # Tailles optimisées pour 2"x1" (51x25mm)
        size_title = 22    # Titre principal
        size_body = 16     # Description  
        size_meta = 15     # Raison/métadonnées (plus grand)
        
        # Chercher une police dans l'ordre de préférence
        font_candidates = [
            # Polices personnalisées (si présentes)
            self.settings.fonts_dir / "Roboto-Bold.ttf",
            self.settings.fonts_dir / "Roboto-Regular.ttf",
            # Polices Windows courantes
            Path("C:/Windows/Fonts/arial.ttf"),
            Path("C:/Windows/Fonts/arialbd.ttf"),
            Path("C:/Windows/Fonts/segoeui.ttf"),
            Path("C:/Windows/Fonts/consola.ttf"),
        ]
        
        # Trouver une police disponible
        font_path = None
        bold_font_path = None
        
        for candidate in font_candidates:
            if candidate.exists():
                if "bold" in candidate.name.lower() or "bd" in candidate.name.lower():
                    bold_font_path = bold_font_path or candidate
                else:
                    font_path = font_path or candidate
        
        try:
            if bold_font_path:
                self.font_title = ImageFont.truetype(str(bold_font_path), size_title)
            elif font_path:
                self.font_title = ImageFont.truetype(str(font_path), size_title)
            else:
                raise FileNotFoundError("Aucune police trouvée")
            
            if font_path:
                self.font_body = ImageFont.truetype(str(font_path), size_body)
                self.font_meta = ImageFont.truetype(str(font_path), size_meta)
            else:
                self.font_body = self.font_title
                self.font_meta = self.font_title
                
        except Exception:
            # Fallback sur police par défaut de Pillow
            self.font_title = ImageFont.load_default()
            self.font_body = ImageFont.load_default()
            self.font_meta = ImageFont.load_default()
    
    def _get_priority_style(self, indicator: str) -> dict:
        """Retourne le style visuel selon la priorité (high contrast)."""
        # Toutes les barres en noir pour un contraste maximal
        styles = {
            "○": {"color": "#000000", "bar_color": "#AAAAAA"},      # Low - barre grise
            "●": {"color": "#000000", "bar_color": "#000000"},      # Medium - barre noire
            "▲": {"color": "#000000", "bar_color": "#000000"},      # High - barre noire
            "⚠": {"color": "#000000", "bar_color": "#000000"},      # Urgent - barre noire
        }
        return styles.get(indicator, styles["●"])
    
    def _truncate_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> str:
        """Tronque le texte pour qu'il tienne dans la largeur donnée."""
        if not text:
            return ""
        
        # Créer une image temporaire pour mesurer
        temp_img = Image.new("L", (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        
        # Vérifier si le texte tient
        bbox = temp_draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        
        if text_width <= max_width:
            return text
        
        # Tronquer progressivement
        for i in range(len(text), 0, -1):
            truncated = text[:i] + "..."
            bbox = temp_draw.textbbox((0, 0), truncated, font=font)
            if bbox[2] - bbox[0] <= max_width:
                return truncated
        
        return "..."
    
    def _wrap_text(self, text: str, font: ImageFont.FreeTypeFont, max_width: int, max_lines: int = 2) -> list[str]:
        """Découpe le texte en plusieurs lignes pour tenir dans la largeur."""
        if not text:
            return []
        
        temp_img = Image.new("L", (1, 1))
        temp_draw = ImageDraw.Draw(temp_img)
        
        words = text.split()
        lines = []
        current_line = ""
        
        for word in words:
            test_line = f"{current_line} {word}".strip()
            bbox = temp_draw.textbbox((0, 0), test_line, font=font)
            
            if bbox[2] - bbox[0] <= max_width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word
                
                if len(lines) >= max_lines:
                    # On a atteint le max, ajouter le reste tronqué à la dernière ligne
                    remaining = " ".join([current_line] + words[words.index(word)+1:]) if current_line else ""
                    if remaining:
                        lines[-1] = self._truncate_text(lines[-1] + " " + remaining, font, max_width)
                    return lines
        
        if current_line:
            lines.append(current_line)
        
        return lines[:max_lines]

    def _line_height(self, draw: ImageDraw.ImageDraw, font: ImageFont.FreeTypeFont) -> int:
        """Hauteur approximative d'une ligne pour une police donnée."""
        bbox = draw.textbbox((0, 0), "Ag", font=font)
        return bbox[3] - bbox[1]
    
    def generate(self, label: Label) -> Image.Image:
        """
        Génère une image d'étiquette à partir d'un Label.
        
        Args:
            label: Label contenant les données à afficher
            
        Returns:
            Image PIL en mode "L" (grayscale) optimisée pour impression thermique
        """
        # Créer l'image en niveaux de gris (optimal pour thermique)
        img = Image.new("L", (self.width, self.height), color=255)  # Fond blanc
        draw = ImageDraw.Draw(img)
        
        # Style selon priorité
        style = self._get_priority_style(label.priority_indicator)
        
        # Barre latérale de priorité (à gauche)
        bar_width = 5
        draw.rectangle(
            [(0, 0), (bar_width, self.height)],
            fill=self._gray_value(style["bar_color"])
        )
        
        # Ajuster la marge X pour la barre
        x_start = bar_width + 6
        available_width = self.width - x_start - 4
        
        # Position Y courante
        y = 4
        line_spacing = 2
        
        # === Titre principal ===
        if label.line1:
            title_lines = self._wrap_text(label.line1, self.font_title, available_width, max_lines=2)
            for line in title_lines:
                draw.text((x_start, y), line, font=self.font_title, fill=0)
                bbox = draw.textbbox((x_start, y), line, font=self.font_title)
                y = bbox[3] + line_spacing
        
        # === Raison/Métadonnées (en bas) ===
        meta_lines: list[str] = []
        if label.line3:
            meta_lines = self._wrap_text(label.line3, self.font_meta, available_width, max_lines=2)

        meta_line_h = self._line_height(draw, self.font_meta)
        meta_block_h = 0
        if meta_lines:
            meta_block_h = (len(meta_lines) * meta_line_h) + ((len(meta_lines) - 1) * line_spacing)

        y_meta = self.height - 4 - meta_block_h

        # === Description (utilise l'espace restant au maximum) ===
        if label.line2:
            body_line_h = self._line_height(draw, self.font_body)
            # Espace disponible entre y courant et bloc meta
            available_h = max(0, y_meta - y)
            # Combien de lignes body peuvent tenir ?
            per_line = body_line_h + line_spacing
            max_desc_lines = 1
            if per_line > 0:
                max_desc_lines = max(1, min(10, available_h // per_line))

            desc_lines = self._wrap_text(label.line2, self.font_body, available_width, max_lines=max_desc_lines)
            for line in desc_lines:
                # Stop sécurité si on arrive au bloc meta
                if y + body_line_h > y_meta:
                    break
                draw.text((x_start, y), line, font=self.font_body, fill=0)
                bbox = draw.textbbox((x_start, y), line, font=self.font_body)
                y = bbox[3] + line_spacing

        # Rendre le bloc meta en dernier, au bas
        if meta_lines:
            y_cursor = y_meta
            for line in meta_lines:
                draw.text((x_start, y_cursor), line, font=self.font_meta, fill=0)  # Noir (plus foncé)
                y_cursor += meta_line_h + line_spacing
        
        return img
    
    def _gray_value(self, hex_color: str) -> int:
        """Convertit une couleur hex en valeur de gris (0-255)."""
        hex_color = hex_color.lstrip("#")
        r = int(hex_color[0:2], 16)
        g = int(hex_color[2:4], 16)
        b = int(hex_color[4:6], 16)
        # Formule de luminosité
        return int(0.299 * r + 0.587 * g + 0.114 * b)
    
    def generate_and_save(self, label: Label, output_path: Optional[Path] = None) -> Path:
        """
        Génère une étiquette et la sauvegarde en PNG.
        
        Args:
            label: Label à générer
            output_path: Chemin de sortie (optionnel)
            
        Returns:
            Chemin du fichier généré
        """
        img = self.generate(label)
        
        if output_path is None:
            # Générer un nom unique
            timestamp = __import__("time").strftime("%Y%m%d_%H%M%S")
            filename = f"label_{label.task_id or timestamp}.png"
            output_path = self.settings.output_dir / filename
        
        # Créer le dossier si nécessaire
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Sauvegarder
        img.save(output_path, "PNG", dpi=(self.settings.printer_dpi, self.settings.printer_dpi))
        
        return output_path
    
    def preview(self, label: Label):
        """Affiche un aperçu de l'étiquette (pour debug)."""
        img = self.generate(label)
        img.show()


if __name__ == "__main__":
    from src.processing.models import Task, Priority
    from datetime import datetime
    
    # Créer une tâche de test
    task = Task(
        id="test-001",
        source="test",
        title="Finaliser le rapport Q4 2025",
        description="Inclure métriques ventes et projections",
        priority=Priority.HIGH,
        category="Travail",
        due_date=datetime(2025, 12, 15),
    )
    
    # Générer le label
    label = Label.from_task(task)
    
    # Générer et sauvegarder l'image
    generator = LabelGenerator()
    output = generator.generate_and_save(label)
    print(f"✅ Étiquette générée: {output}")
    
    # Afficher l'aperçu
    generator.preview(label)
