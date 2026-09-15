"""Caricamento della configurazione (config.yaml) e dei segreti (.env / ambiente)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

log = logging.getLogger(__name__)

# Radice del progetto: config.yaml e state/ stanno qui accanto, non dentro il package.
RADICE = Path(__file__).resolve().parent.parent
PERCORSO_CONFIG = RADICE / "config.yaml"
PERCORSO_STATO = RADICE / "state" / "seen.json"
PERCORSO_ENV = RADICE / ".env"


@dataclass
class Provincia:
    """Una sede territoriale da monitorare."""

    nome: str
    base_url: str
    attiva: bool = True
    categoria_preferita: str | None = None

    @property
    def chiave(self) -> str:
        """Prefisso usato nelle chiavi di seen.json."""
        return self.nome.lower().replace(" ", "-")


@dataclass
class Config:
    province: list[Provincia]
    http: dict[str, Any] = field(default_factory=dict)
    esecuzione: dict[str, Any] = field(default_factory=dict)
    classi_di_concorso: dict[str, Any] = field(default_factory=dict)
    telegram: dict[str, Any] = field(default_factory=dict)

    @property
    def province_attive(self) -> list[Provincia]:
        return [p for p in self.province if p.attiva]

    @property
    def fuso(self) -> str:
        return self.esecuzione.get("fuso", "Europe/Rome")


def carica_config(percorso: Path | str | None = None) -> Config:
    """Legge config.yaml e valida il minimo indispensabile."""
    percorso = Path(percorso) if percorso else PERCORSO_CONFIG
    if not percorso.exists():
        raise FileNotFoundError(f"configurazione non trovata: {percorso}")

    dati = yaml.safe_load(percorso.read_text(encoding="utf-8")) or {}

    province_grezze = dati.get("province") or []
    if not province_grezze:
        raise ValueError("config.yaml: la lista 'province' e' vuota")

    province = []
    for voce in province_grezze:
        if not voce.get("nome") or not voce.get("base_url"):
            raise ValueError(f"config.yaml: provincia senza nome o base_url: {voce!r}")
        province.append(
            Provincia(
                nome=voce["nome"],
                base_url=voce["base_url"].rstrip("/"),
                attiva=bool(voce.get("attiva", True)),
                categoria_preferita=voce.get("categoria_preferita"),
            )
        )

    classi = dati.get("classi_di_concorso") or {}
    if not classi.get("codici") and not classi.get("parole"):
        raise ValueError("config.yaml: 'classi_di_concorso' non contiene ne' codici ne' parole")

    return Config(
        province=province,
        http=dati.get("http") or {},
        esecuzione=dati.get("esecuzione") or {},
        classi_di_concorso=classi,
        telegram=dati.get("telegram") or {},
    )


def carica_dotenv(percorso: Path | str | None = None) -> None:
    """Carica un .env locale nelle variabili d'ambiente (senza sovrascrivere quelle gia' presenti).

    Volutamente artigianale: la spec chiede requirements minimali, non vale una dipendenza
    in piu' per una decina di righe. In GitHub Actions il .env non esiste e i valori
    arrivano dai secret del repository.
    """
    percorso = Path(percorso) if percorso else PERCORSO_ENV
    if not percorso.exists():
        return
    for riga in percorso.read_text(encoding="utf-8").splitlines():
        riga = riga.strip()
        if not riga or riga.startswith("#") or "=" not in riga:
            continue
        chiave, _, valore = riga.partition("=")
        chiave = chiave.strip()
        valore = valore.strip().strip('"').strip("'")
        if chiave and chiave not in os.environ:
            os.environ[chiave] = valore


def segreti_telegram() -> tuple[str | None, str | None]:
    """Restituisce (token, chat_id) dall'ambiente. Il token non va MAI loggato."""
    carica_dotenv()
    return os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
