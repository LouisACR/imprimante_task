"""
Script pour tester une impression réelle sur Munbyn ITPP941.
Imprime une seule étiquette de test.
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.processing.models import Task, Label, Priority
from src.output.label_generator import LabelGenerator
from src.output.printer import Printer
from datetime import datetime


def print_test_label():
    """Imprime une étiquette de test."""
    
    print("🖨️  Test d'impression Munbyn ITPP941")
    print("=" * 40)
    
    # Créer une tâche de test
    task = Task(
        id="test-print",
        source="test",
        title="Test KanbanPrinter",
        description="Étiquette de test",
        priority=Priority.HIGH,
        category="Test",
        due_date=datetime.now(),
    )
    
    # Générer le label
    label = Label.from_task(task)
    
    # Générer l'image
    generator = LabelGenerator()
    img = generator.generate(label)
    
    # Sauvegarder une copie (debug)
    debug_path = PROJECT_ROOT / "output" / "test_print.png"
    img.save(debug_path)
    print(f"📁 Image sauvegardée: {debug_path}")
    
    # Imprimer
    printer = Printer()
    
    if not printer.is_available:
        print("❌ win32print non disponible")
        return False
    
    if not printer.printer_exists():
        print(f"❌ Imprimante '{printer.printer_name}' non trouvée")
        print(f"   Disponibles: {printer.list_printers()}")
        return False
    
    print(f"\n🖨️  Impression sur: {printer.printer_name}")
    
    # Confirmation
    response = input("\n⚠️  Prêt à imprimer ? (o/n): ").strip().lower()
    if response not in ("o", "oui", "y", "yes"):
        print("❌ Impression annulée")
        return False
    
    try:
        success = printer.print_image(img)
        if success:
            print("✅ Impression envoyée !")
        return success
    except Exception as e:
        print(f"❌ Erreur: {e}")
        return False


if __name__ == "__main__":
    print_test_label()
