"""
KanbanPrinter - Point d'entrée principal.
CLI pour analyser les tâches et imprimer les plus importantes.
"""

import argparse
import logging
import signal
import sys
import time
from datetime import datetime
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
from src.utils.resilience import health_monitor, safe_execute, classify_error, ErrorSeverity

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("kanbanprinter")

# Supprimer les warnings inutiles
logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
logging.getLogger("httpx").setLevel(logging.WARNING)


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
        """
        Récupère les tâches de toutes les sources.
        Utilise le circuit breaker pour éviter les sources en échec répété.
        """
        all_tasks = []
        
        for source in self.sources:
            source_name = source.source_name
            
            # Vérifier si la source est en "circuit ouvert" (trop d'échecs)
            if health_monitor.should_skip(source_name):
                health = health_monitor.get_health(source_name)
                print(f"  ⏸️  {source_name}: désactivé temporairement (retry à {health.next_retry.strftime('%H:%M')})")
                continue
            
            try:
                if source.connect():
                    tasks = source.fetch_tasks()
                    all_tasks.extend(tasks)
                    health_monitor.record_success(source_name)
                    print(f"  ✅ {source_name}: {len(tasks)} tâches")
                else:
                    error_msg = source.last_error or "Erreur de connexion"
                    health_monitor.record_failure(source_name, error_msg)
                    print(f"  ❌ {source_name}: {error_msg}")
                    
            except Exception as e:
                severity = classify_error(e)
                health_monitor.record_failure(source_name, str(e))
                
                if severity == ErrorSeverity.TRANSIENT:
                    print(f"  ⚠️  {source_name}: erreur réseau temporaire - {e}")
                elif severity == ErrorSeverity.RECOVERABLE:
                    print(f"  🔐 {source_name}: erreur d'auth - {e}")
                else:
                    print(f"  ❌ {source_name}: {e}")
                    logger.error(f"Erreur source {source_name}: {e}")
        
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
        emails_skipped = 0
        tasks_from_emails = 0
        skipped_already_printed = 0
        
        for task in tasks:
            # Pour les emails, vérifier si déjà traité AVANT d'appeler le LLM
            if task.source.startswith("gmail") or task.source.startswith("email"):
                # Extraire l'ID Gmail depuis raw_data ou l'ID de la tâche
                gmail_id = None
                if task.raw_data:
                    gmail_id = task.raw_data.get("gmail_id")
                if not gmail_id:
                    # Essayer d'extraire depuis l'ID (format: gmail-account-id)
                    parts = task.id.split("-")
                    if len(parts) >= 3:
                        gmail_id = parts[-1]
                
                # Vérifier si cet email a déjà été traité
                if gmail_id and self.skip_printed and self.db.is_source_processed(task.source, gmail_id):
                    emails_skipped += 1
                    continue
                
                if self.use_llm and self.parser.is_configured:
                    emails_processed += 1
                    extracted = self.parser.extract_tasks_from_email(task)
                    
                    # Marquer l'email comme traité (même s'il n'a généré aucune tâche)
                    if gmail_id:
                        self.db.mark_source_processed(
                            source=task.source,
                            source_id=gmail_id,
                            original_title=task.title,
                            tasks_extracted=len(extracted)
                        )
                    
                    for extracted_task, scoring in extracted:
                        if scoring["score"] >= self.print_threshold:
                            extracted_task.priority = scoring["priority"]
                            results.append((extracted_task, scoring))
                            tasks_from_emails += 1
                # Sans LLM, ignorer les emails (pas de conversion 1:1)
                continue
            
            # Pour les autres sources (non-email), vérifier si déjà imprimé
            if self.skip_printed and self.db.is_already_printed(task.content_hash):
                skipped_already_printed += 1
                continue
            
            # Pour les autres sources, scoring normal
            if self.use_llm and self.parser.is_configured:
                scoring = self.parser.score_task(task)
            else:
                scoring = self.parser._score_without_llm(task)
            
            if scoring["score"] >= self.print_threshold:
                task.priority = scoring["priority"]
                results.append((task, scoring))
        
        if emails_processed > 0 or emails_skipped > 0:
            print(f"  📧 {emails_processed} emails analysés, {emails_skipped} déjà traités → {tasks_from_emails} tâches extraites")
        
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
    
    def run_daemon(
        self,
        interval: int = 300,
        auto_print: bool = False,
        max_iterations: Optional[int] = None
    ) -> None:
        """
        Exécute le programme en mode daemon (boucle continue).
        
        Args:
            interval: Intervalle entre les vérifications (en secondes, défaut: 5 min)
            auto_print: Imprimer automatiquement sans confirmation
            max_iterations: Nombre max d'itérations (None = infini)
        """
        print("\n" + "=" * 50)
        print("🔄 KANBANPRINTER - MODE DAEMON")
        print("=" * 50)
        print(f"  Intervalle: {interval}s ({interval // 60} min)")
        print(f"  Auto-print: {'Oui' if auto_print else 'Non'}")
        print(f"  Seuil: {self.print_threshold}/100")
        print("  Ctrl+C pour arrêter")
        print("=" * 50)
        
        # Gestionnaire de signal pour arrêt propre
        self._running = True
        
        def signal_handler(signum, frame):
            print("\n\n🛑 Arrêt demandé...")
            self._running = False
        
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        
        iteration = 0
        total_printed = 0
        errors_count = 0
        
        while self._running:
            iteration += 1
            
            if max_iterations and iteration > max_iterations:
                print(f"\n✅ Nombre max d'itérations atteint ({max_iterations})")
                break
            
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n{'─' * 50}")
            print(f"🕐 [{timestamp}] Itération #{iteration}")
            print(f"{'─' * 50}")
            
            try:
                # Exécuter le cycle
                stats = self._run_cycle(auto_print=auto_print)
                total_printed += stats.get("printed", 0)
                
                # Résumé de l'itération
                print(f"\n📊 Cycle #{iteration}: {stats['filtered_tasks']} nouvelles tâches, {stats['printed']} imprimées")
                
            except Exception as e:
                errors_count += 1
                logger.error(f"Erreur cycle #{iteration}: {e}")
                print(f"\n❌ Erreur: {e}")
                
                # Si trop d'erreurs consécutives, augmenter le délai
                if errors_count >= 3:
                    extra_wait = min(errors_count * 60, 600)  # Max 10 min de plus
                    print(f"  ⏳ Trop d'erreurs, attente supplémentaire de {extra_wait}s")
                    time.sleep(extra_wait)
            
            # Réinitialiser le compteur d'erreurs après un succès
            if errors_count > 0 and stats.get("total_tasks", 0) > 0:
                errors_count = 0
            
            # Afficher l'état des sources
            health_summary = health_monitor.get_summary()
            unhealthy = [n for n, h in health_summary.items() if not h["healthy"]]
            if unhealthy:
                print(f"  ⚠️  Sources en échec: {', '.join(unhealthy)}")
            
            # Attendre avant le prochain cycle
            if self._running:
                next_run = datetime.now().timestamp() + interval
                print(f"\n💤 Prochaine vérification dans {interval}s...")
                
                # Attendre par petits intervalles pour pouvoir réagir aux signaux
                while self._running and time.time() < next_run:
                    time.sleep(min(5, interval))
        
        # Résumé final
        print("\n" + "=" * 50)
        print("📊 RÉSUMÉ FINAL")
        print("=" * 50)
        print(f"  Itérations: {iteration}")
        print(f"  Total imprimé: {total_printed}")
        print(f"  Erreurs: {errors_count}")
        
        db_stats = self.db.get_stats()
        print(f"  En base: {db_stats['total']} tâches")
        print("=" * 50)
    
    def _run_cycle(self, auto_print: bool = False) -> dict:
        """
        Exécute un cycle du daemon (sans les bannières).
        
        Args:
            auto_print: Imprimer automatiquement sans confirmation
            
        Returns:
            Statistiques du cycle
        """
        stats = {
            "total_tasks": 0,
            "filtered_tasks": 0,
            "printed": 0,
        }
        
        # 1. Récupérer les tâches
        print("📥 Récupération des tâches...")
        if not self.sources:
            print("  ⚠️ Aucune source configurée")
            return stats
        
        all_tasks = self.fetch_all_tasks()
        stats["total_tasks"] = len(all_tasks)
        
        if not all_tasks:
            print("  Aucune tâche récupérée")
            return stats
        
        # 2. Analyser et filtrer
        print(f"🧠 Analyse (seuil: {self.print_threshold}/100)...")
        to_print = self.analyze_and_filter(all_tasks)
        stats["filtered_tasks"] = len(to_print)
        
        if not to_print:
            print("  Rien de nouveau à imprimer")
            return stats
        
        # 3. Afficher les tâches trouvées
        print(f"🎯 À imprimer: {len(to_print)} tâches")
        for task, scoring in to_print:
            print(f"  [{scoring['score']:3d}] {task.priority_symbol} {task.title}")
        
        # 4. Générer et imprimer
        print("🖼️  Génération des étiquettes...")
        image_paths = self.generate_labels(to_print)
        
        if auto_print:
            print("🖨️  Impression automatique...")
            stats["printed"] = self.print_labels(image_paths)
            if stats["printed"] > 0:
                self._save_printed_tasks(to_print[:stats["printed"]])
                print(f"  ✅ {stats['printed']} étiquettes imprimées")
        else:
            print(f"  📋 {len(image_paths)} étiquettes générées (auto-print désactivé)")
            # En mode non-auto, on enregistre quand même pour éviter de regénérer
            self._save_printed_tasks(to_print)
        
        return stats


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
  
Mode daemon (arrière-plan):
  python main.py --gmail pro --daemon
  python main.py --gmail pro --daemon --interval 600 --auto-print
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
    
    # Mode daemon
    parser.add_argument(
        "--daemon", "-d",
        action="store_true",
        help="Mode daemon: tourne en arrière-plan avec vérifications périodiques"
    )
    parser.add_argument(
        "--interval", "-i",
        type=int,
        default=300,
        help="Intervalle entre les vérifications en mode daemon (secondes, défaut: 300 = 5 min)"
    )
    parser.add_argument(
        "--auto-print",
        action="store_true",
        help="En mode daemon, imprimer automatiquement sans confirmation"
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help="Afficher l'état de santé des sources et quitter"
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
    
    # Mode daemon ou exécution unique
    if args.daemon:
        app.run_daemon(
            interval=args.interval,
            auto_print=args.auto_print
        )
    else:
        app.run(dry_run=args.dry_run, show_all=args.show_all)


if __name__ == "__main__":
    main()
