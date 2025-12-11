"""
KanbanPrinter - Point d'entrée principal.
CLI pour analyser les tâches et imprimer les plus importantes.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

# Ajouter le chemin du projet
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config.settings import get_settings
from src.inputs.base_input import InputRegistry
from src.inputs.local_json import LocalJsonInput  # Auto-register
# Import conditionnel des sources Google (nécessite packages Google)
try:
    from src.inputs.google_tasks_input import GoogleTasksInput, MultiGoogleTasksInput
    from src.inputs.gmail_input import GmailInput, MultiGmailInput
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
from src.processing.models import Task, Label, TaskList
from src.processing.llm_parser import LLMParser
from src.output.label_generator import LabelGenerator
from src.output.printer import Printer
from src.storage.database import TaskDatabase


class KanbanPrinter:
    """
    Application principale KanbanPrinter.
    Orchestre le flux: Sources → Scoring → Filtrage → Impression.
    """
    
    def __init__(self, print_threshold: int = 70, use_llm: bool = True, skip_printed: bool = True):
        """
        Initialise l'application.
        
        Args:
            print_threshold: Score minimum pour imprimer (0-100)
            use_llm: Utiliser le LLM pour le scoring (sinon règles simples)
            skip_printed: Ignorer les tâches déjà imprimées (défaut: True)
        """
        self.settings = get_settings()
        self.print_threshold = print_threshold
        self.use_llm = use_llm
        self.skip_printed = skip_printed
        
        # Composants
        self.parser = LLMParser()
        self.parser.print_threshold = print_threshold
        
        self.generator = LabelGenerator()
        self.printer = Printer()
        
        # Base de données des tâches imprimées
        self.db = TaskDatabase()
        
        # Sources actives
        self.sources: list = []
    
    def add_source(self, source):
        """Ajoute une source de tâches."""
        self.sources.append(source)
    
    def add_json_source(self, file_path: Path):
        """Ajoute une source JSON."""
        source = LocalJsonInput(file_path)
        self.sources.append(source)
    
    def add_google_tasks(self, account_name: str = "default", credentials_path: Optional[Path] = None):
        """Ajoute Google Tasks comme source."""
        if not GOOGLE_AVAILABLE:
            print("⚠️ Packages Google non installés. pip install google-api-python-client google-auth-oauthlib")
            return
        source = GoogleTasksInput(credentials_path=credentials_path, account_name=account_name)
        self.sources.append(source)
    
    def add_multi_google_tasks(self, account_names: list[str], credentials_path: Optional[Path] = None):
        """Ajoute plusieurs comptes Google Tasks comme source unique."""
        if not GOOGLE_AVAILABLE:
            print("⚠️ Packages Google non installés. pip install google-api-python-client google-auth-oauthlib")
            return
        source = MultiGoogleTasksInput(account_names=account_names, credentials_path=credentials_path)
        self.sources.append(source)
    
    def add_gmail(self, account_name: str = "default", credentials_path: Optional[Path] = None, query: str = "is:unread OR is:starred"):
        """Ajoute un compte Gmail comme source."""
        if not GOOGLE_AVAILABLE:
            print("⚠️ Packages Google non installés. pip install google-api-python-client google-auth-oauthlib")
            return
        source = GmailInput(
            credentials_path=credentials_path,
            account_name=account_name,
            query=query
        )
        self.sources.append(source)
    
    def fetch_all_tasks(self) -> TaskList:
        """Récupère les tâches de toutes les sources."""
        all_tasks = []
        
        for source in self.sources:
            try:
                if source.connect():
                    tasks = source.fetch_tasks()
                    all_tasks.extend(tasks)
                    print(f"  ✅ {source.source_name}: {len(tasks)} tâches")
                else:
                    print(f"  ❌ {source.source_name}: {source.last_error}")
            except Exception as e:
                print(f"  ❌ {source.source_name}: {e}")
        
        return all_tasks
    
    def analyze_and_filter(self, tasks: TaskList) -> list[tuple[Task, dict]]:
        """
        Analyse et filtre les tâches.
        Pour les emails: extrait les vraies tâches actionnables.
        Pour les autres: scoring normal.
        
        Returns:
            Liste de (task, scoring) pour les tâches à imprimer
        """
        results = []
        emails_processed = 0
        tasks_from_emails = 0
        skipped_already_printed = 0
        
        for task in tasks:
            # Vérifier si la tâche a déjà été imprimée (avant traitement LLM)
            if self.skip_printed and self.db.is_already_printed(task.content_hash):
                skipped_already_printed += 1
                continue
            
            # Pour les emails, extraire les vraies tâches avec le LLM
            if task.source.startswith("gmail") or task.source.startswith("email"):
                if self.use_llm and self.parser.is_configured:
                    emails_processed += 1
                    extracted = self.parser.extract_tasks_from_email(task)
                    for extracted_task, scoring in extracted:
                        # Vérifier aussi les tâches extraites d'emails
                        if self.skip_printed and self.db.is_already_printed(extracted_task.content_hash):
                            skipped_already_printed += 1
                            continue
                        if scoring["score"] >= self.print_threshold:
                            extracted_task.priority = scoring["priority"]
                            results.append((extracted_task, scoring))
                            tasks_from_emails += 1
                # Sans LLM, ignorer les emails (pas de conversion 1:1)
                continue
            
            # Pour les autres sources, scoring normal
            if self.use_llm and self.parser.is_configured:
                scoring = self.parser.score_task(task)
            else:
                scoring = self.parser._score_without_llm(task)
            
            if scoring["score"] >= self.print_threshold:
                task.priority = scoring["priority"]
                results.append((task, scoring))
        
        if emails_processed > 0:
            print(f"  📧 {emails_processed} emails analysés → {tasks_from_emails} tâches extraites")
        
        if skipped_already_printed > 0:
            print(f"  ⏭️  {skipped_already_printed} tâches déjà imprimées (ignorées)")
        
        # Trier par score décroissant
        results.sort(key=lambda x: x[1]["score"], reverse=True)
        
        return results
    
    def generate_labels(self, tasks_with_scores: list[tuple[Task, dict]]) -> list[Path]:
        """Génère les images d'étiquettes."""
        output_files = []
        
        for task, scoring in tasks_with_scores:
            # Passer le scoring pour utiliser les titres reformulés et la raison
            label = Label.from_task(task, scoring=scoring)
            output_path = self.generator.generate_and_save(label)
            output_files.append(output_path)
        
        return output_files
    
    def print_labels(self, image_paths: list[Path], dry_run: bool = False) -> int:
        """
        Imprime les étiquettes.
        
        Args:
            image_paths: Chemins des images à imprimer
            dry_run: Si True, ne pas vraiment imprimer
            
        Returns:
            Nombre d'étiquettes imprimées
        """
        if dry_run:
            print(f"  🔍 Mode dry-run: {len(image_paths)} étiquettes générées")
            return 0
        
        if not self.printer.is_available:
            print("  ⚠️ Imprimante non disponible")
            return 0
        
        printed = 0
        for path in image_paths:
            try:
                self.printer.print_image(path)
                printed += 1
            except Exception as e:
                print(f"  ❌ Erreur impression {path.name}: {e}")
        
        return printed
    
    def run(self, dry_run: bool = False, show_all: bool = False) -> dict:
        """
        Exécute le pipeline complet.
        
        Args:
            dry_run: Ne pas imprimer, juste analyser
            show_all: Afficher toutes les tâches (pas seulement celles à imprimer)
            
        Returns:
            Statistiques d'exécution
        """
        print("\n" + "=" * 50)
        print("🖨️  KANBANPRINTER")
        print("=" * 50)
        
        stats = {
            "total_tasks": 0,
            "filtered_tasks": 0,
            "printed": 0,
        }
        
        # 1. Récupérer les tâches
        print("\n📥 Récupération des tâches...")
        if not self.sources:
            print("  ⚠️ Aucune source configurée")
            return stats
        
        all_tasks = self.fetch_all_tasks()
        stats["total_tasks"] = len(all_tasks)
        print(f"  Total: {len(all_tasks)} tâches")
        
        # 2. Analyser et filtrer
        print(f"\n🧠 Analyse (seuil: {self.print_threshold}/100)...")
        llm_status = "LLM" if (self.use_llm and self.parser.is_configured) else "règles"
        print(f"  Mode: {llm_status}")
        
        to_print = self.analyze_and_filter(all_tasks)
        stats["filtered_tasks"] = len(to_print)
        
        # Afficher les résultats
        if show_all:
            print("\n📋 Toutes les tâches:")
            for task in all_tasks:
                scoring = self.parser._score_without_llm(task)
                marker = "🖨️" if scoring["score"] >= self.print_threshold else "  "
                print(f"  {marker} [{scoring['score']:3d}] {task.priority_symbol} {task.title}")
        
        print(f"\n🎯 À imprimer: {len(to_print)} tâches")
        for task, scoring in to_print:
            print(f"  [{scoring['score']:3d}] {task.priority_symbol} {task.title}")
            if scoring.get("reason"):
                print(f"        → {scoring['reason']}")
        
        if not to_print:
            print("  Rien à imprimer !")
            return stats
        
        # 3. Générer les étiquettes
        print("\n🖼️  Génération des étiquettes...")
        image_paths = self.generate_labels(to_print)
        for path in image_paths:
            print(f"  ✅ {path.name}")
        
        # 4. Imprimer
        print("\n🖨️  Impression...")
        if dry_run:
            print(f"  🔍 Mode dry-run: pas d'impression")
        else:
            confirm = input(f"  Imprimer {len(image_paths)} étiquettes ? (o/n): ").strip().lower()
            if confirm in ("o", "oui", "y", "yes"):
                stats["printed"] = self.print_labels(image_paths)
                print(f"  ✅ {stats['printed']} étiquettes imprimées")
                
                # Enregistrer les tâches imprimées dans la base de données
                if stats["printed"] > 0:
                    self._save_printed_tasks(to_print[:stats["printed"]])
            else:
                print("  ❌ Impression annulée")
        
        # Résumé
        print("\n" + "=" * 50)
        print(f"📊 Résumé: {stats['total_tasks']} tâches → {stats['filtered_tasks']} filtrées → {stats['printed']} imprimées")
        
        # Afficher les stats de la base
        db_stats = self.db.get_stats()
        print(f"📦 Base de données: {db_stats['total']} tâches enregistrées")
        
        return stats
    
    def _save_printed_tasks(self, tasks_with_scores: list[tuple[Task, dict]]):
        """
        Enregistre les tâches imprimées dans la base de données.
        
        Args:
            tasks_with_scores: Liste de (Task, scoring) des tâches imprimées
        """
        saved = 0
        for task, scoring in tasks_with_scores:
            success = self.db.mark_as_printed(
                task_hash=task.content_hash,
                source=task.source,
                original_title=task.title,
                label_title=scoring.get("label_title", task.title),
                label_description=scoring.get("label_description", task.description or ""),
                score=scoring.get("score", 0),
                source_id=task.id
            )
            if success:
                saved += 1
        
        if saved > 0:
            print(f"  💾 {saved} tâches enregistrées en base")


def main():
    """Point d'entrée CLI."""
    parser = argparse.ArgumentParser(
        description="KanbanPrinter - Imprime les tâches importantes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemples:
  python main.py --json data/sample_tasks.json
  python main.py --google-tasks --gmail
  python main.py --gmail perso --gmail pro --threshold 80
  python main.py --dry-run --show-all
        """
    )
    
    # Sources
    parser.add_argument(
        "--json", "-j",
        type=Path,
        help="Chemin vers un fichier JSON de tâches"
    )
    parser.add_argument(
        "--google-tasks", "-g",
        action="append",
        metavar="ACCOUNT",
        nargs="?",
        const="default",
        help="Ajouter un compte Google Tasks (peut être répété: --google-tasks perso --google-tasks pro)"
    )
    parser.add_argument(
        "--gmail",
        action="append",
        metavar="ACCOUNT",
        help="Ajouter un compte Gmail (peut être répété: --gmail perso --gmail pro)"
    )
    parser.add_argument(
        "--gmail-query",
        type=str,
        default="is:unread OR is:starred",
        help="Requête Gmail (défaut: 'is:unread OR is:starred')"
    )
    
    # Options de scoring
    parser.add_argument(
        "--threshold", "-t",
        type=int,
        default=70,
        help="Score minimum pour imprimer (0-100, défaut: 70)"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Désactiver le LLM (utiliser règles simples)"
    )
    
    # Options d'exécution
    parser.add_argument(
        "--dry-run", "-n",
        action="store_true",
        help="Analyser sans imprimer"
    )
    parser.add_argument(
        "--show-all", "-a",
        action="store_true",
        help="Afficher toutes les tâches (pas seulement filtrées)"
    )
    parser.add_argument(
        "--reprint",
        action="store_true",
        help="Réimprimer même les tâches déjà imprimées (ignore la base de données)"
    )
    parser.add_argument(
        "--db-stats",
        action="store_true",
        help="Afficher les statistiques de la base de données et quitter"
    )
    
    args = parser.parse_args()
    
    # Si demande de stats seulement
    if args.db_stats:
        db = TaskDatabase()
        stats = db.get_stats()
        print("\n📦 Statistiques de la base de données:")
        print(f"   Total: {stats['total']} tâches imprimées")
        print(f"   Score moyen: {stats['average_score']}/100")
        print("   Par source:")
        for source, count in stats['by_source'].items():
            print(f"     - {source}: {count}")
        db.close()
        return
    
    # Créer l'application
    app = KanbanPrinter(
        print_threshold=args.threshold,
        use_llm=not args.no_llm,
        skip_printed=not args.reprint
    )
    
    # Ajouter les sources
    sources_added = False
    
    if args.json:
        if not args.json.exists():
            print(f"❌ Fichier non trouvé: {args.json}")
            sys.exit(1)
        app.add_json_source(args.json)
        sources_added = True
    
    if args.google_tasks:
        for account_name in args.google_tasks:
            app.add_google_tasks(account_name=account_name)
        sources_added = True
    
    if args.gmail:
        for account_name in args.gmail:
            app.add_gmail(account_name=account_name, query=args.gmail_query)
        sources_added = True
    
    # Source par défaut si rien spécifié
    if not sources_added:
        default_json = PROJECT_ROOT / "data" / "sample_tasks.json"
        if default_json.exists():
            app.add_json_source(default_json)
        else:
            print("❌ Aucune source spécifiée.")
            print("   Utilisez: --json, --google-tasks, ou --gmail")
            sys.exit(1)
    
    # Exécuter
    app.run(dry_run=args.dry_run, show_all=args.show_all)


if __name__ == "__main__":
    main()
