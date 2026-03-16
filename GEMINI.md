# GEMINI.md

This file provides an architectural map and developmental context for the **ductor** project.

## Project Overview

**ductor** is a versatile bot interface that allows users to control official provider CLIs (Anthropic's `claude`, OpenAI's `codex`, and Google's `gemini`) via messengers like Telegram and Matrix. It operates by running these CLIs as subprocesses on the host machine, ensuring that all interactions use the user's official subscriptions and local environment.

### Core Architecture
- **Multi-Agent Supervisor:** Manages the main agent and any dynamically created sub-agents.
- **Orchestrator:** The central routing hub that dispatches messages to commands, conversation flows, or background tasks.
- **CLI Service:** A unified interface for interacting with various AI providers, handling subprocess execution, stream parsing, and process lifecycle.
- **Messenger Protocol:** An abstraction layer allowing multiple transports (Telegram, Matrix) to coexist and share core logic.
- **Persistent State:** All configuration, session history, memory, and scheduled tasks are stored as plain JSON or Markdown files in `~/.ductor/`.

### Tech Stack
- **Language:** Python 3.11+
- **Asynchronous Framework:** `asyncio`
- **Messenger Libraries:** `aiogram` (Telegram), `matrix-nio` (Matrix)
- **Data Validation:** `pydantic`
- **Build System:** `hatchling`
- **UI/CLI:** `rich`, `questionary`

## Building and Running

### Setup Environment
```bash
# Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# or: .venv\Scripts\activate  # Windows

# Install the project in editable mode with development dependencies
pip install -e ".[dev]"
```

### Running the Bot
```bash
# Start the bot (runs onboarding wizard if not configured)
ductor

# Start with verbose logging
ductor -v
```

### Testing
```bash
# Run the full test suite
pytest

# Run specific tests
pytest tests/bot/test_app.py
```

## Development Conventions

### Coding Standards
- **Linting & Formatting:** The project uses `ruff` for both linting and formatting.
- **Type Checking:** `mypy` is used in `strict` mode to ensure type safety.
- **Line Length:** Maximum line length is set to 100 characters.

### Project Structure
| Directory | Description |
|---|---|
| `ductor_bot/bot/` | Messenger-specific handlers and UI logic. |
| `ductor_bot/orchestrator/` | Message routing, command registry, and execution flows. |
| `ductor_bot/cli/` | Provider-specific wrappers and subprocess management. |
| `ductor_bot/messenger/` | Transport implementations (Telegram, Matrix). |
| `ductor_bot/session/` | Session management and state persistence. |
| `ductor_bot/workspace/` | Filesystem initialization and path management. |
| `ductor_bot/infra/` | Low-level infrastructure (PID locks, Docker, Service managers). |

### Key Patterns
- **Subprocess Isolation:** Providers are executed as subprocesses, often within an optional Docker sandbox.
- **Streaming Output:** Real-time response streaming is achieved through asynchronous generators and messenger-specific delivery mechanisms.
- **Heartbeat System:** Proactive checks ensure that long-running processes are still alive and that the user is updated on progress.
- **Shared Memory:** `MAINMEMORY.md` and `SHAREDMEMORY.md` provide persistent context across conversations.

## Maintenance and Operations
- **Service Management:** `ductor service install` sets up the bot as a system service (systemd, launchd, or Task Scheduler).
- **Docker Sandbox:** `ductor docker enable` configures an isolated environment for executing untrusted code or tools.
- **Hot-Reload:** Configuration changes in `config.json` are monitored and applied without requiring a full restart.
