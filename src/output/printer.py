"""
Interface d'impression pour Windows via win32print.
Envoie les images générées à l'imprimante Munbyn ITPP941.
"""

import sys
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Optional, Union

from PIL import Image

# Ajouter le chemin parent pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config.settings import get_settings


class PrinterError(Exception):
    """Erreur liée à l'impression."""
    pass


class Printer:
    """
    Gère l'impression sur imprimante thermique via win32print.
    Conçu pour Munbyn ITPP941 sur Windows.
    """
    
    def __init__(self, printer_name: Optional[str] = None):
        """
        Initialise l'interface d'impression.
        
        Args:
            printer_name: Nom de l'imprimante (utilise la config si non spécifié)
        """
        self.settings = get_settings()
        self.printer_name = printer_name or self.settings.printer_name
        
        # Import conditionnel de win32print (Windows uniquement)
        self._win32print = None
        self._win32ui = None
        self._win32con = None
        self._load_win32_modules()
    
    def _load_win32_modules(self):
        """Charge les modules win32 si disponibles."""
        try:
            import win32print
            import win32ui
            import win32con
            self._win32print = win32print
            self._win32ui = win32ui
            self._win32con = win32con
        except ImportError:
            # Sera None si non disponible (dev sur autre OS)
            pass
    
    @property
    def is_available(self) -> bool:
        """Vérifie si le module d'impression est disponible."""
        return self._win32print is not None
    
    def list_printers(self) -> list[str]:
        """
        Liste toutes les imprimantes disponibles.
        
        Returns:
            Liste des noms d'imprimantes
        """
        if not self.is_available:
            return ["[win32print non disponible - simulation]"]
        
        printers = []
        flags = self._win32print.PRINTER_ENUM_LOCAL | self._win32print.PRINTER_ENUM_CONNECTIONS
        for printer in self._win32print.EnumPrinters(flags, None, 1):
            printers.append(printer[2])  # Le nom est à l'index 2
        
        return printers
    
    def printer_exists(self) -> bool:
        """Vérifie si l'imprimante configurée existe."""
        if not self.is_available:
            return False
        return self.printer_name in self.list_printers()
    
    def get_default_printer(self) -> Optional[str]:
        """Retourne le nom de l'imprimante par défaut."""
        if not self.is_available:
            return None
        try:
            return self._win32print.GetDefaultPrinter()
        except Exception:
            return None
    
    def print_image(self, image: Union[Image.Image, Path, str]) -> bool:
        """
        Imprime une image sur l'imprimante thermique.
        
        Args:
            image: Image PIL, chemin vers fichier, ou chaîne de chemin
            
        Returns:
            True si l'impression a réussi
            
        Raises:
            PrinterError: En cas d'erreur d'impression
        """
        # Charger l'image si nécessaire
        if isinstance(image, (str, Path)):
            image = Image.open(image)
        
        if not self.is_available:
            return self._simulate_print(image)
        
        if not self.printer_exists():
            available = self.list_printers()
            raise PrinterError(
                f"Imprimante '{self.printer_name}' non trouvée. "
                f"Disponibles: {available}"
            )
        
        try:
            return self._print_via_gdi(image)
        except Exception as e:
            raise PrinterError(f"Erreur d'impression: {e}")
    
    def _print_via_gdi(self, image: Image.Image) -> bool:
        """
        Imprime via l'API GDI de Windows.
        Méthode fiable pour les imprimantes thermiques.
        """
        # Convertir en RGB si nécessaire (GDI préfère RGB)
        if image.mode == "L":
            image = image.convert("RGB")
        elif image.mode != "RGB":
            image = image.convert("RGB")
        
        # Créer un device context pour l'imprimante
        hdc = self._win32ui.CreateDC()
        hdc.CreatePrinterDC(self.printer_name)
        
        try:
            # Démarrer le document
            hdc.StartDoc("KanbanPrinter Label")
            hdc.StartPage()
            
            # Obtenir les dimensions de la zone imprimable
            printable_width = hdc.GetDeviceCaps(self._win32con.HORZRES)
            printable_height = hdc.GetDeviceCaps(self._win32con.VERTRES)
            
            # Dimensions de l'image
            img_width, img_height = image.size
            
            # Calculer le ratio pour remplir au maximum sans déformer
            ratio_w = printable_width / img_width
            ratio_h = printable_height / img_height
            ratio = min(ratio_w, ratio_h)
            
            # Nouvelles dimensions
            new_width = int(img_width * ratio)
            new_height = int(img_height * ratio)
            
            # Centrer l'image
            x_offset = (printable_width - new_width) // 2
            y_offset = (printable_height - new_height) // 2
            
            # Créer un bitmap compatible
            dib = ImageWin_Dib(image)
            dib.draw(hdc.GetHandleOutput(), (x_offset, y_offset, x_offset + new_width, y_offset + new_height))
            
            # Terminer l'impression
            hdc.EndPage()
            hdc.EndDoc()
            
            return True
            
        finally:
            hdc.DeleteDC()
    
    def _simulate_print(self, image: Image.Image) -> bool:
        """
        Simule l'impression (pour développement/test sans imprimante).
        Sauvegarde l'image dans le dossier output.
        """
        output_dir = self.settings.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = __import__("time").strftime("%Y%m%d_%H%M%S")
        output_path = output_dir / f"simulated_print_{timestamp}.png"
        
        image.save(output_path)
        print(f"🖨️ [SIMULATION] Image sauvegardée: {output_path}")
        
        return True
    
    def print_test_page(self) -> bool:
        """
        Imprime une page de test pour vérifier la configuration.
        
        Returns:
            True si l'impression a réussi
        """
        # Créer une image de test
        width = self.settings.label_width_px
        height = self.settings.label_height_px
        
        img = Image.new("L", (width, height), color=255)
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        
        # Dessiner un cadre
        draw.rectangle([(5, 5), (width - 6, height - 6)], outline=0, width=2)
        
        # Texte de test
        draw.text((20, 20), "KanbanPrinter", fill=0)
        draw.text((20, 50), f"Test: {self.printer_name}", fill=0)
        draw.text((20, 80), f"Size: {width}x{height}px", fill=0)
        draw.text((20, 110), f"DPI: {self.settings.printer_dpi}", fill=0)
        
        return self.print_image(img)


# Helper pour l'impression Windows
class ImageWin_Dib:
    """Wrapper pour PIL ImageWin.Dib avec fallback."""
    
    def __init__(self, image: Image.Image):
        self.image = image
        try:
            from PIL import ImageWin
            self._dib = ImageWin.Dib(image)
        except ImportError:
            self._dib = None
    
    def draw(self, hdc, box):
        if self._dib:
            self._dib.draw(hdc, box)


if __name__ == "__main__":
    # Test du module printer
    printer = Printer()
    
    print("=== Test du module Printer ===\n")
    print(f"Module win32 disponible: {printer.is_available}")
    print(f"Imprimante configurée: {printer.printer_name}")
    
    if printer.is_available:
        print(f"\nImprimantes disponibles:")
        for p in printer.list_printers():
            marker = "→" if p == printer.printer_name else " "
            default = "(défaut)" if p == printer.get_default_printer() else ""
            print(f"  {marker} {p} {default}")
        
        print(f"\nImprimante trouvée: {printer.printer_exists()}")
    
    print("\n--- Test d'impression simulée ---")
    printer.print_test_page()
