import sys
from pathlib import Path

# Ensure the project root is on sys.path so `import app` works regardless of
# how pytest is invoked (CLI, VSCode test runner, etc.).
sys.path.insert(0, str(Path(__file__).parent))
