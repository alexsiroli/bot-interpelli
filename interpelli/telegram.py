"""Composizione e invio dei messaggi Telegram.

Il token non compare mai nei log: viene mascherato anche negli URL degli errori,
perche' requests mette l'URL completo nel messaggio delle eccezioni.
"""

from __future__ import annotations

import html
import json
import logging
import time
from datetime import datetime
from typing import Any

import requests

from .parse import MANCANTE, Interpello, descrivi_tempo_rimanente

log = logging.getLogger(__name__)

LIMITE_TELEGRAM = 4096


def esc(testo: Any) -> str:
    """Escape dei caratteri che Telegram interpreta come markup HTML."""
    if testo is None:
        return MANCANTE
    return html.escape(str(testo), quote=False)


def _riga_scadenza(interpello: Interpello, adesso: datetime) -> str:
    if interpello.scadenza is None:
        return f"⚠️ Scadenza: {esc(interpello.scadenza_testo)}"
    quanto = descrivi_tempo_rimanente(interpello.scadenza, adesso)
    scadenza = interpello.scadenza.strftime("%d/%m/%Y alle %H:%M")
    if quanto == "SCADUTO":
        return f"⛔ Scadenza: {esc(scadenza)} — <b>GIA' SCADUTA</b>"
    return f"⚠️ Scadenza: {esc(scadenza)} <i>({esc(quanto)})</i>"


def componi_messaggio(interpello: Interpello, adesso: datetime) -> str:
    """Il messaggio di avviso per un singolo interpello (formato della spec §4)."""
    righe = [
        f"🔔 <b>Nuovo interpello — {esc(interpello.provincia_nome)}</b>",
        f"<b>{esc(interpello.classe)}</b> · {esc(interpello.disciplina)}",
        f"🏫 {esc(interpello.scuola)}",
    ]
    ore = f"{esc(interpello.ore)} ore" if interpello.ore != MANCANTE else MANCANTE
    righe.append(f"⏱ {ore} · dal {esc(interpello.inizio)} al {esc(interpello.fine)}")
    righe.append(_riga_scadenza(interpello, adesso))
    if interpello.email != MANCANTE:
        righe.append(f"✉️ {esc(interpello.email)}")
    for pdf in interpello.pdf[:3]:
        righe.append(f"📎 {esc(pdf)}")
    righe.append(f"🔗 {esc(interpello.link)}")
    return "\n".join(righe)


def componi_digest(aperti: list[dict[str, Any]], adesso: datetime) -> str:
    """Riepilogo del mattino degli interpelli ancora aperti gia' segnalati."""
    righe = [f"📋 <b>Interpelli ancora aperti</b> ({len(aperti)})", ""]
    for voce in sorted(aperti, key=lambda v: v.get("scadenza") or "9999"):
        scadenza = voce.get("scadenza")
        quando = MANCANTE
        if scadenza:
            data = datetime.fromisoformat(scadenza)
            quando = f"{data.strftime('%d/%m %H:%M')} ({descrivi_tempo_rimanente(data, adesso)})"
        righe.append(
            f"• <b>{esc(voce.get('classe', MANCANTE))}</b> — {esc(voce.get('scuola', MANCANTE))} "
            f"[{esc(voce.get('provincia', ''))}]"
        )
        righe.append(f"  ⚠️ {esc(quando)} · 🔗 {esc(voce.get('link', ''))}")
    return "\n".join(righe)


def componi_digest_vuoto(adesso: datetime) -> str:
    """Il riepilogo quando non c'e' nessun interpello aperto.

    Serve a due cose: rispondere a chi scrive /digest, e dare la conferma giornaliera
    che il bot sta girando (i run schedulati di GitHub ogni tanto saltano, e senza
    questo messaggio il silenzio sarebbe indistinguibile da un guasto).
    """
    return (
        "📋 <b>Nessun interpello di informatica aperto</b>\n"
        f"Controllato adesso, {adesso.strftime('%d/%m alle %H:%M')}. "
        "Appena ne esce uno te lo mando subito."
    )


def componi_allerta(provincia: str, run_vuoti: int) -> str:
    """Allerta sul fallimento silenzioso: un feed che non restituisce piu' nulla."""
    return (
        "🛑 <b>Possibile guasto del bot</b>\n"
        f"La provincia <b>{esc(provincia)}</b> non restituisce post da {run_vuoti} run consecutivi.\n"
        "Probabile cambio di struttura del sito o categoria rinominata: "
        "controlla con <code>--discover</code>."
    )


def spezza(testo: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    """Spezza un messaggio troppo lungo sul confine di riga (Telegram taglia a 4096)."""
    if len(testo) <= limite:
        return [testo]
    pezzi: list[str] = []
    corrente = ""
    for riga in testo.split("\n"):
        while len(riga) > limite:  # riga singola piu' lunga del limite: taglio netto
            pezzi.append(riga[:limite])
            riga = riga[limite:]
        if len(corrente) + len(riga) + 1 > limite:
            pezzi.append(corrente.rstrip("\n"))
            corrente = ""
        corrente += riga + "\n"
    if corrente.strip():
        pezzi.append(corrente.rstrip("\n"))
    return pezzi


class Telegram:
    """Client minimale per sendMessage."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        sessione: requests.Session | None = None,
        timeout: float = 10.0,
        limite: int = LIMITE_TELEGRAM,
    ) -> None:
        self._token = token
        self.chat_id = chat_id
        self.sessione = sessione or requests.Session()
        self.timeout = timeout
        self.limite = limite

    def _maschera(self, testo: str) -> str:
        return testo.replace(self._token, "***TOKEN***") if self._token else testo

    def invia(self, testo: str) -> bool:
        """Invia un messaggio (spezzandolo se serve). False se anche un solo pezzo fallisce."""
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        for pezzo in spezza(testo, self.limite):
            if not self._invia_pezzo(url, pezzo):
                return False
        return True

    def _invia_pezzo(self, url: str, testo: str, tentativi: int = 3) -> bool:
        payload = {
            "chat_id": self.chat_id,
            "text": testo,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for tentativo in range(1, tentativi + 1):
            try:
                risposta = self.sessione.post(url, data=payload, timeout=self.timeout)
            except requests.RequestException as e:
                log.warning("invio fallito (tentativo %d): %s", tentativo, self._maschera(str(e)))
                time.sleep(2 * tentativo)
                continue
            if risposta.status_code == 200:
                return True
            if risposta.status_code == 429:
                # Telegram dice quanti secondi aspettare: rispettarlo evita il ban temporaneo.
                attesa = 5
                try:
                    attesa = int(risposta.json().get("parameters", {}).get("retry_after", 5))
                except ValueError:
                    pass
                log.warning("Telegram rate limit: attendo %ss", attesa)
                time.sleep(attesa)
                continue
            log.error(
                "Telegram ha risposto %s: %s",
                risposta.status_code,
                self._maschera(risposta.text[:300]),
            )
            # 400/403 non si risolvono ritentando (chat_id sbagliato, bot bloccato, HTML rotto).
            if risposta.status_code in (400, 401, 403, 404):
                return False
            time.sleep(2 * tentativo)
        return False

    def leggi_aggiornamenti(self, offset: int | None = None, attesa: int = 0) -> list[dict]:
        """getUpdates: i messaggi arrivati da quando li abbiamo letti l'ultima volta.

        `attesa` > 0 attiva il long polling (la richiesta resta aperta finche' non arriva
        qualcosa): si usa in modalita' --ascolta, mentre il workflow schedulato passa 0.
        """
        url = f"https://api.telegram.org/bot{self._token}/getUpdates"
        parametri: dict[str, int] = {"timeout": attesa}
        if offset is not None:
            parametri["offset"] = offset
        try:
            risposta = self.sessione.get(url, params=parametri, timeout=self.timeout + attesa)
        except requests.RequestException as e:
            log.warning("lettura dei comandi fallita: %s", self._maschera(str(e)))
            return []
        if risposta.status_code != 200:
            log.warning("getUpdates ha risposto %s", risposta.status_code)
            return []
        return risposta.json().get("result", []) or []

    def imposta_menu_comandi(self, comandi: list[tuple[str, str]]) -> bool:
        """setMyCommands: fa comparire il menu dei comandi accanto alla casella di testo."""
        url = f"https://api.telegram.org/bot{self._token}/setMyCommands"
        payload = {
            "commands": json.dumps(
                [{"command": nome, "description": descrizione} for nome, descrizione in comandi]
            )
        }
        try:
            risposta = self.sessione.post(url, data=payload, timeout=self.timeout)
        except requests.RequestException as e:
            log.warning("setMyCommands fallito: %s", self._maschera(str(e)))
            return False
        return risposta.status_code == 200

    def verifica(self) -> tuple[bool, str]:
        """getMe: conferma che il token e' valido, senza mandare niente in chat."""
        url = f"https://api.telegram.org/bot{self._token}/getMe"
        try:
            risposta = self.sessione.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            return False, self._maschera(str(e))
        if risposta.status_code != 200:
            return False, self._maschera(risposta.text[:200])
        nome = risposta.json().get("result", {}).get("username", "?")
        return True, f"@{nome}"
