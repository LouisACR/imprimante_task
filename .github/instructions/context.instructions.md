# KanbanPrinter - GitHub Copilot Instructions

## Project Overview

KanbanPrinter is a Python application that transforms digital tasks (Gmail, Google Tasks) into physical labels printed on a Munbyn ITPP941 thermal printer. It uses OpenAI's LLM to intelligently extract actionable tasks from emails and score task importance.

## Tech Stack

- **Language**: Python 3.10+
- **OS**: Windows (win32print for printing)
- **Image Generation**: Pillow/PIL
- **LLM**: OpenAI API (gpt-4o-mini)
- **Database**: SQLite (task deduplication)
- **APIs**: Gmail API, Google Tasks API (OAuth2)

## Project Structure

```
src/
├── main.py              # CLI entry point + KanbanPrinter orchestrator
├── inputs/              # Data sources
│   ├── base_input.py    # Abstract base class + InputRegistry
│   ├── gmail_input.py   # Gmail API source (with retry/backoff)
│   ├── google_tasks_input.py  # Google Tasks API source
│   └── local_json.py    # Local JSON source (testing)
├── processing/          # Task processing
│   ├── models.py        # Task, Label, Priority dataclasses
│   └── llm_parser.py    # OpenAI scoring & email extraction
├── output/              # Generation & printing
│   ├── label_generator.py  # Pillow image generation (480x240px)
│   └── printer.py       # win32print interface
├── storage/             # Persistence
│   └── database.py      # SQLite for printed tasks & processed emails
└── utils/               # Utilities
    └── resilience.py    # Retry with backoff, circuit breaker, health monitor
```

## Key Concepts

### Task Flow

1. **Fetch**: Sources (Gmail, Google Tasks) retrieve raw data
2. **Dedupe**: Check SQLite if email/task already processed
3. **LLM Parse**: Extract actionable tasks from emails, score importance (0-100)
4. **Filter**: Keep tasks above threshold (default: 70)
5. **Generate**: Create label images with Pillow
6. **Print**: Send to thermal printer via win32print
7. **Store**: Mark as printed in SQLite to avoid reprints

### Email Processing

- Emails are processed ONCE - marked in `processed_sources` table
- LLM extracts 0-N actionable tasks from each email
- Hash is based on `gmail_id` (not LLM-generated content) for stable deduplication

### Daemon Mode

- Runs continuously with configurable interval (`--daemon --interval 600`)
- Circuit breaker pattern: sources with repeated failures are temporarily disabled
- Auto-refresh of OAuth tokens for long-running sessions

## Coding Conventions

### Error Handling

- Use `src/utils/resilience.py` for retry logic
- Classify errors: TRANSIENT (network), RECOVERABLE (auth), FATAL (config)
- Never crash in daemon mode - log and continue

### Database

- Always use `TaskDatabase` class from `src/storage/database.py`
- Two tables: `printed_tasks` (labels printed) and `processed_sources` (emails analyzed)
- Hash-based deduplication to avoid reprocessing

### Adding New Sources

1. Create `src/inputs/my_source.py`
2. Inherit from `BaseInput`
3. Implement: `connect()`, `fetch_tasks()`, `is_configured()`
4. Register: `InputRegistry.register(MySourceInput)`

### Label Generation

- Target: 6cm × 3cm thermal labels (480×240px at 203 DPI)
- Use grayscale mode ("L") for thermal printing
- Fonts: Roboto or system Arial fallback

## CLI Usage

```bash
# Single run with Gmail
python src/main.py --gmail pro --threshold 70

# Daemon mode (background)
python src/main.py --gmail pro --daemon --interval 300 --auto-print

# Dry run (no printing)
python src/main.py --gmail pro --dry-run

# Database stats
python src/main.py --db-stats

# Force reprint (ignore database)
python src/main.py --gmail pro --reprint
```

## Configuration

### Environment Variables (.env)

```env
OPENAI_API_KEY=sk-...
PRINTER_NAME=Munbyn ITPP941
LABEL_WIDTH_MM=60
LABEL_HEIGHT_MM=30
```

### Google OAuth

- Credentials: `config/google_credentials.json`
- Tokens: `config/gmail_token_{account}.pickle`

## Testing Tips

- Use `--dry-run` to test without printing
- Use `--json data/sample_tasks.json` for offline testing
- Check `output/` folder for generated label images
- Use `--db-stats` to verify deduplication is working
