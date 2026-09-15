"""Stato persistente del bot: cosa ho gia' visto, cosa e' ancora aperto, chi e' muto.

Il file vive in state/seen.json e viene committato nel repo dal workflow a fine run:
e' l'unica memoria del bot, non esiste un database. Regola di ferro (spec §6):
una chiave si scrive SOLO dopo un invio riuscito, altrimenti un errore di Telegram
farebbe perdere l'interpello per sempre.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

VERSIONE = 1


class Stato:
    """Wrapper su seen.json. Tiene tutto in memoria e scrive solo su salva()."""

    def __init__(self, percorso: Path, dati: dict[str, Any] | None = None) -> None:
        self.percorso = Path(percorso)
        dati = dati or {}
        self.visti: dict[str, str] = dati.get("visti", {})
        self.aperti: dict[str, dict[str, Any]] = dati.get("aperti", {})
        meta = dati.get("meta", {})
        self.ultimo_digest: str | None = meta.get("ultimo_digest")
        self.run_vuoti: dict[str, int] = meta.get("run_vuoti", {})
        self.allerta_inviata: dict[str, bool] = meta.get("allerta_inviata", {})
        # Id dell'ultimo comando Telegram gia' eseguito: senza questo, a ogni run il bot
        # rileggerebbe gli stessi comandi e risponderebbe all'infinito.
        self.ultimo_update_id: int | None = meta.get("ultimo_update_id")

    # --- caricamento e salvataggio ------------------------------------------------

    @classmethod
    def carica(cls, percorso: Path | str) -> "Stato":
        percorso = Path(percorso)
        if not percorso.exists():
            log.info("stato non presente (%s): parto da zero", percorso)
            return cls(percorso)
        try:
            dati = json.loads(percorso.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError:
            # Meglio ripartire da zero che far fallire il run: al massimo si rimanda
            # qualcosa gia' mandato, che e' un danno molto minore di un bot fermo.
            log.error("stato illeggibile (%s): riparto da zero", percorso)
            return cls(percorso)
        if dati and "visti" not in dati:
            # Formato piatto {chiave: timestamp} della primissima versione.
            dati = {"visti": dati}
        return cls(percorso, dati)

    def salva(self) -> None:
        self.percorso.parent.mkdir(parents=True, exist_ok=True)
        corpo = {
            "versione": VERSIONE,
            "visti": dict(sorted(self.visti.items())),
            "aperti": self.aperti,
            "meta": {
                "ultimo_digest": self.ultimo_digest,
                "run_vuoti": self.run_vuoti,
                "allerta_inviata": self.allerta_inviata,
                "ultimo_update_id": self.ultimo_update_id,
            },
        }
        testo = json.dumps(corpo, ensure_ascii=False, indent=2, sort_keys=False)
        self.percorso.write_text(testo + "\n", encoding="utf-8")

    # --- deduplica ----------------------------------------------------------------

    def e_visto(self, chiave: str) -> bool:
        return chiave in self.visti

    def marca_visto(self, chiave: str, quando: datetime | None = None) -> None:
        quando = quando or datetime.now(ZoneInfo("Europe/Rome"))
        self.visti[chiave] = quando.isoformat()

    def prune(self, mesi: int, adesso: datetime) -> int:
        """Elimina le voci piu' vecchie di tot mesi. Restituisce quante ne ha tolte."""
        limite = adesso - timedelta(days=30 * mesi)
        da_togliere = []
        for chiave, valore in self.visti.items():
            try:
                quando = datetime.fromisoformat(valore)
            except ValueError:
                da_togliere.append(chiave)
                continue
            if quando.tzinfo is None:
                quando = quando.replace(tzinfo=adesso.tzinfo)
            if quando < limite:
                da_togliere.append(chiave)
        for chiave in da_togliere:
            del self.visti[chiave]
            self.aperti.pop(chiave, None)
        return len(da_togliere)

    # --- interpelli ancora aperti (per il digest) ---------------------------------

    def registra_aperto(self, interpello: Any) -> None:
        """Memorizza un interpello segnalato, per poterlo ricordare nel digest."""
        self.aperti[interpello.chiave] = {
            "provincia": interpello.provincia,
            "titolo": interpello.titolo,
            "link": interpello.link,
            "classe": interpello.classe,
            "scuola": interpello.scuola,
            "ore": interpello.ore,
            "scadenza": interpello.scadenza.isoformat() if interpello.scadenza else None,
            "scadenza_testo": interpello.scadenza_testo,
        }

    def aperti_non_scaduti(self, adesso: datetime) -> list[dict[str, Any]]:
        """Interpelli gia' segnalati la cui scadenza non e' ancora passata.

        Quelli senza scadenza riconosciuta restano in lista per 7 giorni dalla
        segnalazione: e' l'unico modo per non perderli, visto che non sappiamo quando
        scadono, ma senza tenerli in eterno.
        """
        vivi = []
        for chiave, voce in self.aperti.items():
            scadenza = voce.get("scadenza")
            if scadenza:
                try:
                    if datetime.fromisoformat(scadenza) > adesso:
                        vivi.append({"chiave": chiave, **voce})
                except ValueError:
                    continue
            else:
                visto = self.visti.get(chiave)
                if not visto:
                    continue
                try:
                    if datetime.fromisoformat(visto) > adesso - timedelta(days=7):
                        vivi.append({"chiave": chiave, **voce})
                except ValueError:
                    continue
        return vivi

    def pulisci_aperti(self, adesso: datetime) -> None:
        """Toglie dagli 'aperti' quelli ormai scaduti (restano fra i 'visti')."""
        vivi = {v["chiave"] for v in self.aperti_non_scaduti(adesso)}
        self.aperti = {k: v for k, v in self.aperti.items() if k in vivi}

    # --- digest del mattino -------------------------------------------------------

    def digest_dovuto(
        self, adesso: datetime, ora_digest: int, giorni: list[int] | None = None
    ) -> bool:
        """Vero al primo run della giornata dopo l'ora indicata (ora italiana).

        `giorni` sono i giorni ammessi in formato ISO (1 = lunedi ... 7 = domenica):
        nei giorni esclusi il digest non parte e non viene nemmeno segnato come fatto,
        quindi il primo giorno utile successivo riparte regolarmente.
        """
        if giorni and adesso.isoweekday() not in giorni:
            return False
        if adesso.hour < ora_digest:
            return False
        return self.ultimo_digest != adesso.date().isoformat()

    def segna_digest(self, giorno: date) -> None:
        self.ultimo_digest = giorno.isoformat()

    # --- allerta sul silenzio -----------------------------------------------------

    def registra_esito_provincia(self, provincia: str, post_trovati: int) -> int:
        """Aggiorna il contatore dei run a vuoto e restituisce il valore corrente.

        Un feed che smette di restituire post e' il guaio peggiore: non fallisce niente,
        semplicemente non arriva piu' nulla. Il contatore serve a farlo notare.
        """
        if post_trovati > 0:
            self.run_vuoti[provincia] = 0
            self.allerta_inviata[provincia] = False
        else:
            self.run_vuoti[provincia] = self.run_vuoti.get(provincia, 0) + 1
        return self.run_vuoti.get(provincia, 0)

    def allerta_da_mandare(self, provincia: str, soglia: int) -> bool:
        return self.run_vuoti.get(provincia, 0) >= soglia and not self.allerta_inviata.get(
            provincia, False
        )

    def segna_allerta(self, provincia: str) -> None:
        self.allerta_inviata[provincia] = True
