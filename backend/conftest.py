"""Root conftest — ensures backend/ is on sys.path for all tests."""

import sys
from pathlib import Path

import pytest

# Add the backend directory to sys.path so that `from agents.base import ...` works
backend_dir = str(Path(__file__).resolve().parent)
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)


@pytest.fixture(autouse=True)
def _sem_chaves_de_llm(monkeypatch):
    """Nenhum teste chama o LLM real: sem chave, `llm_client.generate()`
    devolve None antes de abrir conexão. Testes que precisam de chave a
    definem com `monkeypatch.setenv` e simulam o cliente."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
