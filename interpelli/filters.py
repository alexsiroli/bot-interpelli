"""Filtro sulle classi di concorso: decide se un post riguarda l'informatica."""

from __future__ import annotations

import html
import logging
import re
import unicodedata
from dataclasses import dataclass

log = logging.getLogger(__name__)

_TAG = re.compile(r"<[^>]+>")
_SPAZI = re.compile(r"\s+")


def normalizza(testo: str) -> str:
    """Porta il testo in una forma confrontabile: niente tag, niente entita', niente accenti.

    I siti scrivono la stessa cosa in dieci modi ("Informatiche", "INFORMATICHE",
    "informatiche" dentro un <strong>, con &#8211; al posto del trattino): normalizzare
    prima del match evita di dover replicare quelle varianti in ogni regex.
    """
    if not testo:
        return ""
    testo = _TAG.sub(" ", testo)
    # Due giri di unescape: alcuni feed fanno il doppio escape (&amp;#8211;).
    testo = html.unescape(html.unescape(testo))
    testo = testo.replace("\xa0", " ")
    # NFKD separa le lettere dai segni diacritici, poi si buttano i segni (categoria Mn).
    testo = unicodedata.normalize("NFKD", testo)
    testo = "".join(c for c in testo if not unicodedata.combining(c))
    return _SPAZI.sub(" ", testo).strip().lower()


@dataclass
class Esito:
    """Risultato del filtro: se ha passato e perche' (comodo nei log e in --dry-run)."""

    passa: bool
    motivo: str = ""


class FiltroClassi:
    """Compila le regex di config.yaml e le applica al testo normalizzato."""

    def __init__(self, conf_classi: dict) -> None:
        codici = conf_classi.get("codici") or []
        parole = conf_classi.get("parole") or []
        esclusioni = conf_classi.get("esclusioni") or []
        self.codici = [(p, re.compile(p, re.IGNORECASE)) for p in codici]
        # Le parole sono sottostringhe, non regex: si escapano per non sorprendere
        # chi aggiunge una voce con un punto o una parentesi.
        self.parole = [(p, re.compile(re.escape(normalizza(p)), re.IGNORECASE)) for p in parole]
        self.esclusioni = [(p, re.compile(p, re.IGNORECASE)) for p in esclusioni]

    def valuta(self, *pezzi: str) -> Esito:
        """Applica il filtro all'unione dei pezzi di testo passati (di norma titolo + contenuto)."""
        testo = normalizza(" ".join(p for p in pezzi if p))
        if not testo:
            return Esito(False, "testo vuoto")

        for sorgente, regex in self.esclusioni:
            if regex.search(testo):
                return Esito(False, f"esclusione {sorgente}")

        for sorgente, regex in self.codici:
            trovato = regex.search(testo)
            if trovato:
                return Esito(True, f"codice {sorgente} -> '{trovato.group(0)}'")

        for sorgente, regex in self.parole:
            trovato = regex.search(testo)
            if trovato:
                return Esito(True, f"parola '{sorgente}'")

        return Esito(False, "nessun codice o parola di informatica")
