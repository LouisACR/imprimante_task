"""
Script de test du pipeline complet KanbanPrinter.
JSON → Task → Label → Image → (Print simulation)
"""

import sys
from pathlib import Path

# Ajouter le chemin du projet
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.inputs.local_json import LocalJsonInput
from src.processing.models import Label
from src.output.label_generator import LabelGenerator
from src.output.printer import Printer


def test_full_pipeline():
    """Test du pipeline complet."""
    
    print("=" * 50)
    print("🧪 TEST PIPELINE KANBANPRINTER")
    print("=" * 50)
    
    # === 1. Charger les tâches depuis JSON ===
    print("\n📂 1. Chargement des tâches depuis JSON...")
    
    json_path = PROJECT_ROOT / "data" / "sample_tasks.json"
    source = LocalJsonInput(json_path)
    
    if not source.connect():
        print(f"❌ Erreur: {source.last_error}")
        return
    
    tasks = source.fetch_tasks()
    print(f"   ✅ {len(tasks)} tâches chargées")
    
    # === 2. Convertir en Labels ===
    print("\n🏷️  2. Conversion en Labels...")
    
    labels = [Label.from_task(task) for task in tasks]
    print(f"   ✅ {len(labels)} labels créés")
    
    for label in labels:
        print(f"      • {label.line1}")
    
    # === 3. Générer les images ===
    print("\n🖼️  3. Génération des images...")
    
    generator = LabelGenerator()
    print(f"   Dimensions: {generator.width}x{generator.height} px")
    
    output_dir = PROJECT_ROOT / "output"
    output_dir.mkdir(exist_ok=True)
    
    generated_files = []
    for label in labels:
        output_path = generator.generate_and_save(label)
        generated_files.append(output_path)
        print(f"   ✅ {output_path.name}")
    
    # === 4. Test impression (simulation) ===
    print("\n🖨️  4. Test d'impression...")
    
    printer = Printer()
    print(f"   Module win32 disponible: {printer.is_available}")
    print(f"   Imprimante configurée: {printer.printer_name}")
    
    if printer.is_available:
        print(f"   Imprimante trouvée: {printer.printer_exists()}")
        print(f"\n   Imprimantes disponibles:")
        for p in printer.list_printers():
            marker = "→" if p == printer.printer_name else " "
            print(f"     {marker} {p}")
    
    # Impression simulée de la première étiquette
    if generated_files:
        print(f"\n   Simulation d'impression de: {generated_files[0].name}")
        printer.print_image(generated_files[0])
    
    # === Résumé ===
    print("\n" + "=" * 50)
    print("✅ PIPELINE TEST TERMINÉ")
    print("=" * 50)
    print(f"\n📁 Images générées dans: {output_dir}")
    print("   Ouvrez les fichiers PNG pour vérifier le rendu.")
    
    if not printer.is_available:
        print("\n⚠️  win32print non disponible (pywin32 non installé)")
        print("   Pour l'installer: pip install pywin32")


if __name__ == "__main__":
    test_full_pipeline()
