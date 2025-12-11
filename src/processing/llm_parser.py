"""
LLM Parser - Analyse et scoring des tâches via OpenAI.
Détermine l'importance/urgence de chaque tâche.
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Ajouter le chemin parent pour les imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config.settings import get_settings
from src.processing.models import Task, Priority, TaskList


def _normalize_datetime(dt: Optional[datetime]) -> Optional[datetime]:
    """Convertit un datetime en naive UTC pour comparaison."""
    if dt is None:
        return None
    # Si aware (a une timezone), convertir en UTC puis rendre naive
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


class LLMParserError(Exception):
    """Erreur lors du parsing LLM."""
    pass


class LLMParser:
    """
    Analyse les tâches via LLM pour déterminer leur importance.
    Assigne un score de 0-100 et décide si la tâche doit être imprimée.
    """
    
    # Seuil par défaut pour imprimer (score >= ce seuil)
    DEFAULT_PRINT_THRESHOLD = 70
    
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        """
        Initialise le parser LLM.
        
        Args:
            api_key: Clé API OpenAI (utilise .env si non spécifié)
            model: Modèle à utiliser (défaut: gpt-4o-mini)
        """
        self.settings = get_settings()
        self.api_key = api_key or self.settings.openai_api_key
        self.model = model or self.settings.openai_model
        self._client = None
        
        # Seuil d'impression personnalisable
        self.print_threshold = self.DEFAULT_PRINT_THRESHOLD
    
    @property
    def is_configured(self) -> bool:
        """Vérifie si l'API est configurée."""
        return bool(self.api_key and self.api_key.startswith("sk-"))
    
    def _get_client(self):
        """Initialise le client OpenAI (lazy loading)."""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(api_key=self.api_key)
            except ImportError:
                raise LLMParserError("openai package not installed. Run: pip install openai")
        return self._client
    
    def _build_scoring_prompt(self, task: Task) -> str:
        """Construit le prompt pour scorer une tâche (non-email)."""
        
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        created_at = _normalize_datetime(task.created_at)
        due_date = _normalize_datetime(task.due_date)
        created_days_ago = (today - created_at).days if created_at else 0
        
        due_info = ""
        if due_date:
            days_until_due = (due_date - today).days
            if days_until_due < 0:
                due_info = f"OVERDUE by {abs(days_until_due)} days!"
            elif days_until_due == 0:
                due_info = "DUE TODAY!"
            elif days_until_due <= 3:
                due_info = f"Due in {days_until_due} days (SOON)"
            else:
                due_info = f"Due in {days_until_due} days"
        
        return f"""Score this task (0-100) and create a label.

    TODAY: {today_str}

TASK:
- Source: {task.source}
- Title: {task.title}
- Description: {task.description or "None"}
- Created: {created_days_ago} days ago
- Due: {due_info or "None"}
- Priority: {task.priority.value}

SCORING:
90-100: CRITICAL (overdue, blocking)
70-89: HIGH (due soon, important)
50-69: MEDIUM (normal)
0-49: LOW/SKIP

Rules: overdue +30, >7 days old +15, urgent keywords +20, no action -20

Respond JSON only:
{{"score": <0-100>, "priority": "<urgent|high|medium|low>", "reason": "<10 words max, same language>", "label_title": "<25 chars max, no filler words>", "label_description": "<280 chars max, specific action and key details>"}}
"""
    
    def _build_email_extraction_prompt(self, task: Task) -> str:
        """Construit le prompt pour extraire les tâches d'un email."""
        
        today = datetime.now()
        today_str = today.strftime("%Y-%m-%d")
        created_at = _normalize_datetime(task.created_at)
        created_days_ago = (today - created_at).days if created_at else 0
        
        # Récupérer plus de contexte depuis raw_data
        raw = task.raw_data or {}
        sender = raw.get("from", "Unknown")
        snippet = raw.get("snippet", task.description or "")
        
        return f"""Analyze this email and extract ACTIONABLE TASKS only.

    TODAY: {today_str}

EMAIL:
- Subject: {task.title}
- From: {sender}
- Content: {snippet}
- Received: {created_days_ago} days ago

RULES:
- Extract ONLY concrete actions I need to do (reply, review, attend, submit, etc.)
- NO task if email is: newsletter, promo, notification, FYI, spam, automated
- Use TODAY to judge relevance. If an event/date is in the past, create NO task only if there is nothing left to do.
- Past events can still produce tasks when follow-up makes sense (send minutes, reimburse, reschedule, post-mortem, ask for recording, etc.)
- ONE email can have 0, 1, or MULTIPLE tasks
- Each task must start with an ACTION verb (Reply/Confirm/Review/Submit/Attend/Call/etc.)
- Do NOT copy/paste the email subject as the task title
- Title must be SHORT and ACTIONABLE (max 25 chars)
- Description must be SPECIFIC and NON-EMPTY (max 280 chars): what to do + key details (who/what/when/where/link/doc)
- Prefer a LONGER description when information exists (target 120-200 chars), because the label can show multiple lines
- Use 1–2 short sentences; include concrete details and next step (ex: "Répondre à X", "Confirmer présence", "préparer Y")
- Avoid filler words and long phrases that add no meaning

EXAMPLES:
- "Meeting invite for Monday" → {{"tasks": [{{"title": "Confirmer réunion", "desc": "Confirmer présence lundi 14h", "score": 75, "reason": "Événement à confirmer"}}]}}
- "Newsletter" → {{"tasks": []}}
- "Event happened yesterday (no action)" → {{"tasks": []}}
- "Event happened yesterday, send notes" → {{"tasks": [{{"title": "Envoyer CR", "desc": "Envoyer compte-rendu aux participants + actions", "score": 65, "reason": "Suivi utile"}}]}}
- "Please review doc and sign contract" → {{"tasks": [{{"title": "Relire doc", "desc": "Lire PJ et noter points à corriger", "score": 70, "reason": "Action demandée"}}, {{"title": "Signer contrat", "desc": "Signer contrat et renvoyer à l'expéditeur", "score": 80, "reason": "Signature requise"}}]}}

Respond JSON only (same language as email):
{{"tasks": [{{"title": "<25 chars>", "desc": "<280 chars, non-empty>", "score": <0-100>, "priority": "<urgent|high|medium|low>", "reason": "<10 words max>"}}]}}
"""
    
    def extract_tasks_from_email(self, email_task: Task) -> list[tuple[Task, dict]]:
        """
        Extrait les vraies tâches actionnables d'un email.
        
        Args:
            email_task: Task représentant l'email brut
            
        Returns:
            Liste de (Task, scoring) pour chaque tâche extraite (peut être vide)
        """
        if not self.is_configured:
            # Sans LLM, on ne peut pas extraire intelligemment
            return []
        
        try:
            client = self._get_client()
            
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You extract actionable tasks from emails. Respond only with valid JSON."},
                    {"role": "user", "content": self._build_email_extraction_prompt(email_task)}
                ],
                temperature=0.3,
                max_completion_tokens=600,
            )
            
            content = response.choices[0].message.content.strip()
            result = json.loads(content)
            
            extracted_tasks = []
            tasks_data = result.get("tasks", [])

            raw = email_task.raw_data or {}
            fallback_snippet = (raw.get("snippet") or "").strip()
            
            for i, task_data in enumerate(tasks_data):
                score = max(0, min(100, int(task_data.get("score", 50))))

                title = (task_data.get("title") or "").strip()
                desc = (task_data.get("desc") or "").strip()

                # Garantir une description non vide (fallback)
                if not desc and fallback_snippet:
                    desc = fallback_snippet[:320]

                # Dernier fallback minimal
                if not title:
                    title = "Traiter email"
                if not desc:
                    desc = "Ouvrir l'email et effectuer l'action demandée"
                
                # Créer une nouvelle Task basée sur l'extraction
                new_task = Task(
                    id=f"{email_task.id}-task{i+1}",
                    source=email_task.source,
                    title=title,
                    description=desc,
                    priority=Priority.from_string(task_data.get("priority", "medium")),
                    created_at=email_task.created_at,
                    raw_data={
                        **email_task.raw_data,
                        "original_subject": email_task.title,
                        "extracted_from_email": True,
                    }
                )
                
                scoring = {
                    "score": score,
                    "priority": new_task.priority,
                    "reason": task_data.get("reason", ""),
                    "label_title": title,
                    "label_description": desc,
                    "should_print": score >= self.print_threshold,
                }
                
                extracted_tasks.append((new_task, scoring))
            
            return extracted_tasks
            
        except json.JSONDecodeError as e:
            print(f"    ⚠️ Email extraction JSON error: {e}")
            return []
        except Exception as e:
            print(f"    ⚠️ Email extraction error: {e}")
            return []
    
    def score_task(self, task: Task) -> dict:
        """
        Score une tâche individuellement.
        
        Args:
            task: Tâche à analyser
            
        Returns:
            dict avec score, priority, reason, should_print
        """
        if not self.is_configured:
            # Fallback sans LLM - scoring basique
            return self._score_without_llm(task)
        
        try:
            client = self._get_client()
            
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a task prioritization assistant. Respond only with valid JSON."},
                    {"role": "user", "content": self._build_scoring_prompt(task)}
                ],
                temperature=0.3,  # Plus déterministe
                max_completion_tokens=400,
            )
            
            content = response.choices[0].message.content.strip()
            
            # Parser la réponse JSON
            result = json.loads(content)
            
            # Valider et normaliser
            score = max(0, min(100, int(result.get("score", 50))))
            priority_str = result.get("priority", "medium").lower()

            label_title = (result.get("label_title") or "").strip() or task.title
            label_description = (result.get("label_description") or "").strip() or (task.description or "")
            
            return {
                "score": score,
                "priority": Priority.from_string(priority_str),
                "reason": result.get("reason", ""),
                "should_print": result.get("should_print", score >= self.print_threshold),
                "label_title": label_title,
                "label_description": label_description,
            }
            
        except json.JSONDecodeError as e:
            print(f"⚠️ LLM response not valid JSON: {e}")
            return self._score_without_llm(task)
        except Exception as e:
            print(f"⚠️ LLM error: {e}")
            return self._score_without_llm(task)
    
    def _score_without_llm(self, task: Task) -> dict:
        """
        Scoring de fallback sans LLM (basé sur des règles simples).
        """
        score = 50  # Score de base
        reasons = []
        
        today = datetime.now()
        
        # Règle 1: Tâche en retard
        due_date = _normalize_datetime(task.due_date)
        if due_date and due_date < today:
            days_overdue = (today - due_date).days
            score += min(30, days_overdue * 5)
            reasons.append(f"Overdue {days_overdue}d")
        
        # Règle 2: Échéance proche
        elif due_date:
            days_until = (due_date - today).days
            if days_until <= 1:
                score += 25
                reasons.append("Due very soon")
            elif days_until <= 3:
                score += 15
                reasons.append("Due soon")
        
        # Règle 3: Ancienneté (créée il y a longtemps)
        created_at = _normalize_datetime(task.created_at)
        if created_at:
            days_old = (today - created_at).days
            if days_old > 14:
                score += 15
                reasons.append(f"Old task ({days_old}d)")
            elif days_old > 7:
                score += 10
                reasons.append(f"Pending {days_old}d")
        
        # Règle 4: Priorité initiale
        priority_bonus = {
            Priority.URGENT: 20,
            Priority.HIGH: 10,
            Priority.MEDIUM: 0,
            Priority.LOW: -10,
        }
        score += priority_bonus.get(task.priority, 0)
        
        # Règle 5: Mots-clés urgents dans le titre
        urgent_keywords = ["urgent", "asap", "important", "critical", "deadline", "now"]
        title_lower = task.title.lower()
        if any(kw in title_lower for kw in urgent_keywords):
            score += 15
            reasons.append("Urgent keywords")
        
        # Normaliser le score
        score = max(0, min(100, score))
        
        # Déterminer la priorité
        if score >= 80:
            priority = Priority.URGENT
        elif score >= 65:
            priority = Priority.HIGH
        elif score >= 40:
            priority = Priority.MEDIUM
        else:
            priority = Priority.LOW
        
        # Pour les emails, générer un titre actionnable basique
        label_title = task.title
        label_description = task.description or ""
        if task.source.startswith("gmail") or task.source.startswith("email"):
            # Simplifier le titre d'email
            label_title = f"Email: {task.title[:40]}"
            label_description = task.category or "Voir email"
        
        return {
            "score": score,
            "priority": priority,
            "reason": "; ".join(reasons) if reasons else "Default scoring",
            "should_print": score >= self.print_threshold,
            "label_title": label_title,
            "label_description": label_description,
        }
    
    def score_tasks(self, tasks: TaskList) -> list[tuple[Task, dict]]:
        """
        Score plusieurs tâches.
        
        Args:
            tasks: Liste de tâches à analyser
            
        Returns:
            Liste de tuples (task, scoring_result)
        """
        results = []
        for task in tasks:
            scoring = self.score_task(task)
            results.append((task, scoring))
        return results
    
    def filter_for_printing(self, tasks: TaskList, threshold: Optional[int] = None) -> TaskList:
        """
        Filtre les tâches qui doivent être imprimées.
        
        Args:
            tasks: Liste de tâches
            threshold: Seuil minimum (utilise self.print_threshold si non spécifié)
            
        Returns:
            Liste des tâches à imprimer (triées par score décroissant)
        """
        threshold = threshold or self.print_threshold
        
        scored = self.score_tasks(tasks)
        
        # Filtrer et trier
        to_print = [
            (task, scoring) for task, scoring in scored
            if scoring["score"] >= threshold
        ]
        
        # Trier par score décroissant
        to_print.sort(key=lambda x: x[1]["score"], reverse=True)
        
        # Mettre à jour la priorité des tâches selon le scoring
        result = []
        for task, scoring in to_print:
            task.priority = scoring["priority"]
            result.append(task)
        
        return result


if __name__ == "__main__":
    from src.inputs.local_json import LocalJsonInput
    
    # Charger des tâches de test
    json_path = Path(__file__).parent.parent.parent / "data" / "sample_tasks.json"
    source = LocalJsonInput(json_path)
    
    if not source.connect():
        print(f"❌ Erreur: {source.last_error}")
        exit(1)
    
    tasks = source.fetch_tasks()
    
    # Créer le parser
    parser = LLMParser()
    print(f"LLM configuré: {parser.is_configured}")
    print(f"Seuil d'impression: {parser.print_threshold}")
    print("=" * 50)
    
    # Scorer les tâches
    for task in tasks:
        result = parser.score_task(task)
        print_marker = "🖨️" if result["should_print"] else "  "
        print(f"{print_marker} [{result['score']:3d}] {task.priority_symbol} {task.title}")
        print(f"        → {result['reason']}")
    
    print("\n" + "=" * 50)
    print("Tâches à imprimer:")
    to_print = parser.filter_for_printing(tasks)
    for task in to_print:
        print(f"  • {task.title}")
