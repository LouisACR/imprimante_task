# KanbanPrinter - Project Context

## 📋 Description

Application Python qui transforme des tâches numériques (Gmail, Google Tasks) en étiquettes physiques imprimées sur une imprimante thermique Munbyn ITPP941. Utilise l'API OpenAI pour extraire intelligemment les tâches actionnables des emails.

## 🎯 Fonctionnalités

- ✅ Récupération depuis Gmail (OAuth2) avec extraction LLM des tâches
- ✅ Récupération depuis Google Tasks (OAuth2)
- ✅ Scoring intelligent des tâches (0-100) via OpenAI
- ✅ Génération d'étiquettes 6cm × 3cm optimisées pour impression thermique
- ✅ Base de données SQLite pour éviter les réimpressions
- ✅ Mode daemon pour exécution en arrière-plan
- ✅ Gestion robuste des erreurs (retry, circuit breaker)

## 🛠️ Stack Technique

| Composant | Technologie |
|-----------|-------------|
| Langage | Python 3.10+ |
| OS cible | Windows (win32print) |
| Images | Pillow/PIL |
| LLM | OpenAI API (gpt-4o-mini) |
| Base de données | SQLite |
| APIs | Gmail API, Google Tasks API |
| Imprimante | Munbyn ITPP941 (203 DPI) |

## 📁 Structure du Projet

```text
imprimante_task/
├── .github/instructions/    # Instructions GitHub Copilot
├── config/
│   ├── settings.py          # Configuration Pydantic
│   ├── google_credentials.json
│   └── gmail_token_*.pickle # Tokens OAuth
├── src/
│   ├── main.py              # Point d'entrée CLI
│   ├── inputs/              # Sources de données
│   │   ├── base_input.py    # Classe abstraite + Registre
│   │   ├── gmail_input.py   # Gmail API (OAuth2)
│   │   ├── google_tasks_input.py
│   │   └── local_json.py    # Source de test
│   ├── processing/
│   │   ├── models.py        # Task, Label, Priority
│   │   └── llm_parser.py    # OpenAI scoring & extraction
│   ├── output/
│   │   ├── label_generator.py # Génération Pillow
│   │   └── printer.py       # Interface win32print
│   ├── storage/
│   │   └── database.py      # SQLite (déduplication)
│   └── utils/
│       └── resilience.py    # Retry, circuit breaker
├── data/
│   ├── sample_tasks.json    # Données de test
│   └── printed_tasks.db     # Base SQLite
├── output/                  # Images générées
└── assets/fonts/            # Polices (Roboto)
```

## 📊 État d'Avancement

| Module | Status | Notes |
|--------|--------|-------|
| Config | 🟢 | Pydantic + dotenv |
| Models | 🟢 | Task, Label, Priority avec hash stable |
| Gmail Input | 🟢 | OAuth2, retry, refresh auto |
| Google Tasks | 🟢 | OAuth2, multi-comptes |
| LLM Parser | 🟢 | Extraction emails, scoring |
| Label Generator | 🟢 | Pillow, 480×240px |
| Printer | 🟢 | win32print |
| Database | 🟢 | SQLite, 2 tables |
| Daemon Mode | 🟢 | Circuit breaker, health monitor |
| CLI | 🟢 | argparse complet |

## 🚀 Utilisation

```bash
# Exécution unique
python src/main.py --gmail pro --threshold 70

# Mode daemon (arrière-plan)
python src/main.py --gmail pro --daemon --interval 300 --auto-print

# Test sans impression
python src/main.py --gmail pro --dry-run

# Statistiques base de données
python src/main.py --db-stats
```

## ⚙️ Configuration

### Variables d'environnement (.env)
```env
OPENAI_API_KEY=sk-...
PRINTER_NAME=Munbyn ITPP941
LABEL_WIDTH_MM=60
LABEL_HEIGHT_MM=30
PRINTER_DPI=203
```

### Google OAuth
1. Créer projet sur [Google Cloud Console](https://console.cloud.google.com/)
2. Activer APIs Gmail et Tasks
3. Créer identifiants OAuth 2.0 (Application de bureau)
4. Placer `credentials.json` dans `config/google_credentials.json`

## 🔧 Architecture

### Flux de traitement
```text
Sources (Gmail/Tasks) 
    → Déduplication (SQLite) 
    → LLM (extraction/scoring) 
    → Filtrage (seuil) 
    → Génération (Pillow) 
    → Impression (win32print)
    → Enregistrement (SQLite)
```

### Tables SQLite

**printed_tasks** - Tâches imprimées
- `task_hash` (PK) - Hash stable du contenu
- `source`, `original_title`, `label_title`, `score`, `printed_at`

**processed_sources** - Emails traités
- `source_hash` (PK) - Hash de source+id
- `source`, `source_id`, `original_title`, `tasks_extracted`, `processed_at`

## 📝 Changelog

### 2025-12-11
- ✅ Mode daemon avec circuit breaker
- ✅ Retry avec backoff exponentiel
- ✅ Base SQLite pour déduplication emails
- ✅ Hash stable basé sur gmail_id (pas contenu LLM)
- ✅ Refresh automatique tokens OAuth

### Initial
- ✅ Structure projet complète
- ✅ Gmail et Google Tasks inputs
- ✅ LLM parser avec extraction emails
- ✅ Génération étiquettes Pillow
- ✅ Interface impression win32print
