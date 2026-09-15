"""Utilita' condivise dai test.

Le fixture in tests/fixtures/ sono risposte VERE dei siti degli UST, scaricate il
2026-09-15. Non vanno "aggiustate" a mano: se un test non passa piu' e' il parser
che deve adattarsi al mondo reale, non il contrario.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def carica_json(nome: str):
    return json.loads((FIXTURES / nome).read_text(encoding="utf-8"))


def carica_post(nome: str) -> dict:
    """Restituisce un singolo post, sia che la fixture sia un oggetto sia una lista."""
    dati = carica_json(nome)
    return dati[0] if isinstance(dati, list) else dati


def carica_testo(nome: str) -> str:
    return (FIXTURES / nome).read_text(encoding="utf-8", errors="replace")


@pytest.fixture
def conf():
    from interpelli.config import carica_config

    return carica_config()
