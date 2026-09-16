"""Comandi che si possono scrivere direttamente nella chat Telegram.

Il bot non ha un server in ascolto: i comandi vengono letti con getUpdates da un
workflow schedulato (ogni 30 minuti nella fascia diurna) oppure, se serve una risposta
immediata, dalla modalita' --ascolta lanciata a mano sul PC.

Regola di sicurezza: si risponde SOLO alla chat configurata in TELEGRAM_CHAT_ID.
Chiunque altro trovi il bot e gli scriva viene ignorato, con una riga di log.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from .config import Config
from .fetch import Post, crea_sessione, recupera
from .parse import MANCANTE, estrai_classe_e_disciplina, testo_piano
from .state import Stato
from .telegram import Telegram, componi_digest, componi_digest_vuoto, esc

log = logging.getLogger(__name__)

GIORNI_ALL = 7
# Per coprire sette giorni su una provincia prolifica servono piu' post di quelli che
# bastano al controllo normale.
POST_PER_ALL = 60

MENU = [
    ("digest", "Interpelli di informatica ancora aperti"),
    ("all", f"Tutti gli interpelli degli ultimi {GIORNI_ALL} giorni"),
    ("stato", "Stato del bot e province monitorate"),
    ("aiuto", "Elenco dei comandi"),
]

AIUTO = (
    "🤖 <b>Comandi disponibili</b>\n\n"
    "/digest — gli interpelli di <b>informatica ancora aperti</b> che ti ho gia' segnalato\n"
    f"/all — <b>tutti</b> gli interpelli degli ultimi {GIORNI_ALL} giorni, anche quelli di altre "
    "classi di concorso, in elenco breve\n"
    "/stato — province monitorate, memoria del bot, ultimo riepilogo\n"
    "/aiuto — questo messaggio\n\n"
    "<i>Gli avvisi sui nuovi interpelli arrivano da soli, non serve chiedere nulla. "
    "I comandi vengono letti ogni mezz'ora circa, quindi la risposta puo' non essere immediata.</i>"
)


def _adesso(conf: Config) -> datetime:
    return datetime.now(ZoneInfo(conf.fuso))


def _aware(quando: datetime | None, fuso: str) -> datetime | None:
    """Le REST API restituiscono date senza fuso: si assumono ora italiana."""
    if quando is None:
        return None
    return quando.replace(tzinfo=ZoneInfo(fuso)) if quando.tzinfo is None else quando


def nome_comando(messaggio: dict) -> str | None:
    """Estrae il comando da un messaggio Telegram, gestendo la forma /comando@nomebot."""
    testo = (messaggio.get("text") or "").strip()
    if not testo.startswith("/"):
        return None
    primo = testo.split()[0]
    return primo[1:].split("@")[0].lower()


def _riga_breve(post: Post, fuso: str) -> str:
    """Una riga sola per il comando /all: data, codice classe, titolo cliccabile."""
    titolo = testo_piano(post.titolo_html)
    classe, _ = estrai_classe_e_disciplina(titolo, "")
    quando = _aware(post.pubblicato, fuso)
    data = quando.strftime("%d/%m") if quando else "--/--"
    breve = titolo if len(titolo) <= 70 else titolo[:67].rstrip() + "..."
    etichetta = f"<b>{esc(classe)}</b> · " if classe != MANCANTE else ""
    if post.link:
        return f"• {data} {etichetta}<a href=\"{esc(post.link)}\">{esc(breve)}</a>"
    return f"• {data} {etichetta}{esc(breve)}"


def componi_tutti(
    per_provincia: dict[str, list[Post]], fuso: str, giorni: int = GIORNI_ALL
) -> str:
    """Elenco abbreviato di tutti gli interpelli, raggruppati per provincia."""
    totale = sum(len(p) for p in per_provincia.values())
    if not totale:
        return f"📰 Nessun interpello pubblicato negli ultimi {giorni} giorni."
    righe = [f"📰 <b>Tutti gli interpelli — ultimi {giorni} giorni</b> ({totale})"]
    for provincia_nome, post in per_provincia.items():
        if not post:
            continue
        righe.append("")
        righe.append(f"<b>{esc(provincia_nome)}</b> ({len(post)})")
        righe.extend(_riga_breve(p, fuso) for p in post)
    righe.append("")
    righe.append("<i>Elenco completo, senza filtro sulle classi di concorso.</i>")
    return "\n".join(righe)


def raccogli_tutti(
    conf: Config, sessione: requests.Session, giorni: int = GIORNI_ALL
) -> dict[str, list[Post]]:
    """Tutti i post delle province attive pubblicati negli ultimi `giorni` giorni."""
    limite = _adesso(conf) - timedelta(days=giorni)
    risultato: dict[str, list[Post]] = {}
    for provincia in conf.province_attive:
        esito = recupera(sessione, provincia, conf, quanti=POST_PER_ALL)
        recenti = []
        for post in esito.post:
            quando = _aware(post.pubblicato, conf.fuso)
            # Un post senza data leggibile si tiene: meglio una riga in piu' che perderlo.
            if quando is None or quando >= limite:
                recenti.append(post)
        recenti.sort(key=lambda p: _aware(p.pubblicato, conf.fuso) or limite, reverse=True)
        risultato[provincia.nome] = recenti
    return risultato


def componi_stato(conf: Config, stato: Stato, adesso: datetime) -> str:
    aperti = stato.aperti_non_scaduti(adesso)
    province = ", ".join(p.nome for p in conf.province_attive)
    spente = [p.nome for p in conf.province if not p.attiva]
    righe = [
        "📊 <b>Stato del bot</b>",
        f"Province monitorate: {esc(province)}",
    ]
    if spente:
        righe.append(f"Province spente: {esc(', '.join(spente))}")
    righe += [
        f"Post gia' visti in memoria: {len(stato.visti)}",
        f"Interpelli di informatica ancora aperti: {len(aperti)}",
        f"Ultimo riepilogo automatico: {esc(stato.ultimo_digest or 'mai')}",
        f"Ora italiana adesso: {adesso.strftime('%d/%m/%Y %H:%M')}",
    ]
    return "\n".join(righe)


def esegui_comando(
    comando: str, conf: Config, stato: Stato, telegram: Telegram, sessione: requests.Session
) -> bool:
    """Esegue un comando e manda la risposta. False se il comando non esiste."""
    adesso = _adesso(conf)

    if comando in ("digest", "aperti"):
        aperti = stato.aperti_non_scaduti(adesso)
        if aperti:
            testo = componi_digest(aperti, adesso)
        else:
            # A differenza del digest automatico, qui il silenzio non va bene: l'utente
            # ha chiesto, quindi merita una risposta anche quando non c'e' niente.
            testo = (
                "📋 Nessun interpello di informatica aperto al momento.\n"
                "<i>Appena ne esce uno te lo mando da solo.</i>"
            )
        return telegram.invia(testo)

    if comando in ("all", "tutti"):
        telegram.invia(f"⏳ Cerco tutti gli interpelli degli ultimi {GIORNI_ALL} giorni...")
        per_provincia = raccogli_tutti(conf, sessione)
        return telegram.invia(componi_tutti(per_provincia, conf.fuso))

    if comando == "stato":
        return telegram.invia(componi_stato(conf, stato, adesso))

    if comando in ("aiuto", "help", "start"):
        return telegram.invia(AIUTO)

    return False


def processa_comandi(
    conf: Config,
    stato: Stato,
    telegram: Telegram,
    sessione: requests.Session | None = None,
    attesa: int = 0,
) -> int:
    """Legge i comandi arrivati in chat e li esegue. Restituisce quanti ne ha eseguiti.

    L'offset di getUpdates viene salvato nello stato: e' quello che impedisce di
    rispondere due volte allo stesso comando al run successivo.
    """
    sessione = sessione or crea_sessione(conf)
    offset = stato.ultimo_update_id + 1 if stato.ultimo_update_id else None
    aggiornamenti = telegram.leggi_aggiornamenti(offset=offset, attesa=attesa)
    eseguiti = 0

    for aggiornamento in aggiornamenti:
        stato.ultimo_update_id = aggiornamento.get("update_id", stato.ultimo_update_id)
        messaggio = aggiornamento.get("message") or aggiornamento.get("edited_message") or {}
        chat_id = str((messaggio.get("chat") or {}).get("id", ""))
        if chat_id != str(telegram.chat_id):
            log.warning("comando ignorato: arriva dalla chat %s, non autorizzata", chat_id)
            continue
        comando = nome_comando(messaggio)
        if not comando:
            continue
        log.info("comando ricevuto: /%s", comando)
        if esegui_comando(comando, conf, stato, telegram, sessione):
            eseguiti += 1
        else:
            telegram.invia(f"❓ Comando <code>/{esc(comando)}</code> sconosciuto.\n\n{AIUTO}")

    return eseguiti
