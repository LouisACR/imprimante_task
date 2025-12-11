# KanbanPrinter - Contexte du Projet

## 📋 Description

Application Python locale qui transforme des tâches numériques (Google Tasks, emails, etc.) en étiquettes physiques imprimées sur une imprimante thermique Munbyn ITPP941.

## 🎯 Objectifs

- Récupérer des tâches depuis plusieurs sources (Google Tasks, JSON local, emails)
- Parser et résumer les tâches via l'API OpenAI
- Générer des images d'étiquettes 6cm × 3cm avec Pillow
- Imprimer via les drivers Windows (win32print)

## 🛠️ Stack Technique

- **Langage**: Python 3.10+
- **OS cible**: Windows (win32print)
- **Génération d'images**: Pillow/PIL
- **LLM**: API OpenAI (ou compatible)
- **Imprimante**: Munbyn ITPP941 (thermique, étiquettes prédécoupées)

## 🖨️ Configuration Imprimante

- **Modèle**: Munbyn ITPP941
- **Type d'étiquettes**: Prédécoupées 6cm × 3cm
- **DPI**: 203
- **Résolution calculée**: ~480px × ~240px

## 📁 Structure du Projet

```text
kanban_printer/
├── PROJECT_CONTEXT.md       # Ce fichier
├── requirements.txt         # Dépendances Python
├── config/
│   ├── settings.py          # Configuration centralisée (Pydantic)
│   └── .env.example         # Template variables d'environnement
├── src/
│   ├── inputs/              # Sources de données
│   │   ├── base_input.py    # Classe abstraite + Registre
│   │   ├── local_json.py    # Source fichier JSON (test)
│   │   └── google_tasks.py  # (À faire) Google Tasks API
│   ├── processing/          # Traitement des tâches
│   │   ├── models.py        # Task, Label, Priority
│   │   └── llm_parser.py    # (À faire) OpenAI
│   └── output/              # Génération et impression
│       ├── label_generator.py # Génération images Pillow
│       └── printer.py       # Interface win32print
├── assets/fonts/            # Polices personnalisées
├── data/                    # Données locales (JSON, etc.)
└── output/                  # Images générées (debug)
```

## 📊 État d'Avancement

| Module | Fichier | Status | Notes |
|--------|---------|--------|-------|
| Config | settings.py | 🟢 Terminé | Pydantic + dotenv |
| Config | .env.example | 🟢 Terminé | |
| Models | models.py | 🟢 Terminé | Task, Label, Priority |
| Inputs | base_input.py | 🟢 Terminé | Classe abstraite + Registre |
| Inputs | local_json.py | 🟢 Terminé | Source de test |
| Inputs | google_tasks.py | 🔴 À faire | API Google |
| Processing | llm_parser.py | 🔴 À faire | OpenAI |
| Output | label_generator.py | 🟢 Terminé | Pillow |
| Output | printer.py | 🟢 Terminé | win32print |
| Main | main.py | 🔴 À faire | Point d'entrée CLI |

**Légende**: 🔴 À faire | 🟡 En cours | 🟢 Terminé | 🔵 En test

## ⚙️ Configuration Requise

### Variables d'environnement (.env)

```env
OPENAI_API_KEY=sk-...
PRINTER_NAME=Munbyn ITPP941
LABEL_WIDTH_MM=60
LABEL_HEIGHT_MM=30
PRINTER_DPI=203
```

### Dépendances principales

- pillow, pydantic, python-dotenv
- pywin32
- openai
- google-api-python-client

## 🔌 Ajouter une nouvelle source

1. Créer `src/inputs/ma_source.py`
2. Hériter de `BaseInput`
3. Implémenter `connect()`, `fetch_tasks()`, `is_configured()`
4. Appeler `InputRegistry.register(MaSourceInput)`

## 📝 Journal des Modifications

### 2025-12-11 - Core implémenté

- ✅ Structure du projet créée
- ✅ `config/settings.py` - Configuration Pydantic
- ✅ `src/processing/models.py` - Task, Label, Priority
- ✅ `src/output/label_generator.py` - Génération Pillow
- ✅ `src/output/printer.py` - Interface win32print
- ✅ `src/inputs/base_input.py` - Abstraction + Registre
- ✅ `src/inputs/local_json.py` - Source de test

## ❓ Prochaines Étapes

1. Tester le pipeline : JSON → Label → Image
2. Créer `main.py` (CLI)
3. Implémenter `llm_parser.py` (OpenAI)
4. Implémenter `google_tasks.py`

## 🔗 Ressources

- [Pillow Documentation](https://pillow.readthedocs.io/)
- [win32print](https://docs.microsoft.com/en-us/windows/win32/printdocs/printing)
- [Google Tasks API](https://developers.google.com/tasks)
- [OpenAI API](https://platform.openai.com/docs/)
