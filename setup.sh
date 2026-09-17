#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

echo "Creating virtualenv..."
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt

echo "Running test suite..."
python3 -m pytest tests/ -v

echo "Initializing database and warming the embedding model..."
python3 - <<'PY'
from lib.db import get_connection
from lib.embeddings import embed_text

conn = get_connection()
conn.close()
print("Database initialized at store.db")

embed_text("warmup")
print("Embedding model ready.")
PY

chmod +x capture.py summarize_trigger.py summarize_worker.py digest.py search.py knowledge_agent.py

echo "Installing mem-search skill..."
mkdir -p "$HOME/.claude/skills"
ln -sfn "$HERE/skills/mem-search" "$HOME/.claude/skills/mem-search"

echo "Installing knowledge-agent skill..."
ln -sfn "$HERE/skills/knowledge-agent" "$HOME/.claude/skills/knowledge-agent"

echo ""
echo "Setup complete."
echo "Add the following to ~/.claude/settings.json under its top-level \"hooks\" key"
echo "(merge with any hooks you already have there — do not overwrite the file):"
echo ""
cat settings-snippet.json
