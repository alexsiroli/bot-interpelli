"""Orchestrazione e riga di comando del bot.

    python -m interpelli.main --check          run normale (fetch, filtro, invio)
    python -m interpelli.main --dry-run        stampa a video invece di inviare
    python -m interpelli.main --backfill       marca tutto come visto, non invia nulla
    python -m interpelli.main --test-telegram  manda un messaggio di prova
    python -m interpelli.main --discover       ricognizione dei siti (quale metodo funziona)
    python -m interpelli.main --comandi        esegue i comandi arrivati in chat (una passata)
    python -m interpelli.main --ascolta        risponde ai comandi in tempo reale
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from . import __version__
from .comandi import MENU, processa_comandi
from .config import PERCORSO_STATO, Config, Provincia, carica_config, segreti_telegram
from .fetch import Esito, crea_sessione, post_da_rest, recupera, risolvi_categoria, scopri_feed_categoria
from .filters import FiltroClassi
from .parse import Interpello, costruisci_interpello
from .state import Stato
from .telegram import (
    Telegram,
    componi_allerta,
    componi_digest,
    componi_digest_vuoto,
    componi_messaggio,
)

log = logging.getLogger("interpelli")


def _prepara_console() -> None:
    """La console di Windows e' cp1252 e le emoji dei messaggi la farebbero esplodere."""
    for flusso in (sys.stdout, sys.stderr):
        try:
            flusso.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _configura_log(verboso: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verboso else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )


def _province_scelte(conf: Config, filtro_nome: str | None) -> list[Provincia]:
    province = conf.province_attive
    if filtro_nome:
        cercato = filtro_nome.lower()
        province = [p for p in province if cercato in p.nome.lower() or cercato in p.chiave]
        if not province:
            raise SystemExit(f"nessuna provincia attiva corrisponde a '{filtro_nome}'")
    return province


def _interpelli_da_esito(
    esito: Esito, provincia: Provincia, filtro: FiltroClassi, stato: Stato, conf: Config, tutti: bool
) -> list[Interpello]:
    """Post -> interpelli filtrati e parsati, saltando quelli gia' visti."""
    trovati = []
    for post in esito.post:
        chiave = f"{post.provincia}:{post.post_id}"
        if not tutti and stato.e_visto(chiave):
            continue
        valutazione = filtro.valuta(post.titolo_html, post.contenuto_html)
        if not valutazione.passa:
            continue
        trovati.append(
            costruisci_interpello(
                provincia=post.provincia,
                post_id=post.post_id,
                titolo_html=post.titolo_html,
                contenuto_html=post.contenuto_html,
                link=post.link,
                provincia_nome=provincia.nome,
                pubblicato=post.pubblicato,
                fuso=conf.fuso,
                motivo_filtro=valutazione.motivo,
            )
        )
    return trovati


def _stampa_interpello(interpello: Interpello, adesso: datetime) -> None:
    print("-" * 72)
    print(componi_messaggio(interpello, adesso).replace("<b>", "").replace("</b>", "")
          .replace("<i>", "").replace("</i>", "").replace("<code>", "").replace("</code>", ""))
    print(f"   [filtro: {interpello.motivo_filtro}]")


def comando_discover(conf: Config, province: list[Provincia]) -> int:
    """Ricognizione: per ogni sito dice quale metodo funziona e quale URL usare."""
    sessione = crea_sessione(conf)
    print(f"Ricognizione dei siti (bot-interpelli {__version__})\n")
    problemi = 0
    for provincia in province:
        print(f"== {provincia.nome} ({provincia.base_url})")
        categoria = None
        try:
            categoria = risolvi_categoria(sessione, provincia, conf)
        except Exception as e:  # noqa: BLE001
            print(f"   REST API categorie: NON disponibile ({e})")
        if categoria:
            print(f"   REST API categorie: OK -> id {categoria['id']} \"{categoria['name']}\" "
                  f"(slug {categoria['slug']}, {categoria.get('count', '?')} post)")
            print(f"   criterio di scelta: {categoria['criterio']}")
            try:
                post = post_da_rest(sessione, provincia, categoria["id"], conf)
                print(f"   REST API post:      OK -> {len(post)} post, ultimo: "
                      f"{post[0].titolo_html[:60] if post else '(nessuno)'}")
                print(f"   URL consigliato:    {provincia.base_url}/wp-json/wp/v2/posts"
                      f"?categories={categoria['id']}&per_page=20")
            except Exception as e:  # noqa: BLE001
                problemi += 1
                print(f"   REST API post:      FALLITO ({e})")
            try:
                feed = scopri_feed_categoria(sessione, provincia, categoria["slug"], conf)
                print(f"   feed di categoria:  {feed or 'non dichiarato nella pagina'}")
            except Exception as e:  # noqa: BLE001
                print(f"   feed di categoria:  non raggiungibile ({e})")
        else:
            problemi += 1
            print("   nessuna categoria 'interpelli' trovata: si usera' il feed generale")
            print(f"   feed generale:      {provincia.base_url}/feed/")
        print()
    print("Ricognizione completata." if not problemi else f"Ricognizione completata con {problemi} problemi.")
    return 0


def comando_run(conf: Config, province: list[Provincia], modalita: str) -> int:
    """Cuore del bot: --check, --dry-run e --backfill passano tutti di qui.

    modalita: "check" (invia), "dry-run" (stampa), "backfill" (marca visto e basta).
    """
    tz = ZoneInfo(conf.fuso)
    adesso = datetime.now(tz)
    stato = Stato.carica(PERCORSO_STATO)
    filtro = FiltroClassi(conf.classi_di_concorso)
    sessione = crea_sessione(conf)

    telegram = None
    if modalita == "check":
        token, chat_id = segreti_telegram()
        if not token or not chat_id:
            # Succede al primo run del workflow, prima che i secret siano configurati:
            # meglio un run verde che spiega cosa manca di un fallimento criptico.
            log.warning(
                "TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID mancanti: proseguo in sola lettura "
                "(nessun invio, nessuna modifica allo stato). Vedi README §Setup."
            )
            modalita = "dry-run"
        else:
            telegram = Telegram(token, chat_id, timeout=float(conf.http.get("timeout", 10)),
                                limite=int(conf.telegram.get("max_caratteri", 4096)))

    inviati = 0
    falliti = 0
    chiavi_di_oggi: set[str] = set()

    for provincia in province:
        esito = recupera(sessione, provincia, conf, adesso)
        if not esito.ok:
            # Lo stato di questa provincia resta invariato: al giro dopo recupera.
            log.warning("[%s] saltata: %s", provincia.nome, esito.errore)
            vuoti = stato.registra_esito_provincia(provincia.chiave, 0)
        else:
            log.info("[%s] %s: %d post (%s)", provincia.nome, esito.metodo, len(esito.post), esito.dettaglio)
            vuoti = stato.registra_esito_provincia(provincia.chiave, len(esito.post))

        soglia = int(conf.esecuzione.get("run_vuoti_prima_di_allerta", 3))
        if stato.allerta_da_mandare(provincia.chiave, soglia):
            log.error("[%s] nessun post da %d run consecutivi: mando l'allerta", provincia.nome, vuoti)
            if telegram and telegram.invia(componi_allerta(provincia.nome, vuoti)):
                stato.segna_allerta(provincia.chiave)
            elif not telegram:
                print(f"[allerta non inviata, modalita' {modalita}] {provincia.nome}: {vuoti} run a vuoto")

        if not esito.ok:
            continue

        if modalita == "backfill":
            for post in esito.post:
                stato.marca_visto(f"{post.provincia}:{post.post_id}", adesso)
            log.info("[%s] backfill: %d post marcati come gia' visti", provincia.nome, len(esito.post))
            continue

        interpelli = _interpelli_da_esito(esito, provincia, filtro, stato, conf, tutti=False)
        if interpelli:
            log.info("[%s] %d interpello/i di informatica da segnalare", provincia.nome, len(interpelli))
        for interpello in interpelli:
            if telegram is None:
                _stampa_interpello(interpello, adesso)
                inviati += 1
                continue
            if telegram.invia(componi_messaggio(interpello, adesso)):
                # Solo ORA il post diventa "visto": se l'invio fallisce si riprova al giro dopo.
                stato.marca_visto(interpello.chiave, adesso)
                stato.registra_aperto(interpello)
                chiavi_di_oggi.add(interpello.chiave)
                inviati += 1
            else:
                falliti += 1
                log.error("[%s] invio fallito per %s: NON lo marco come visto",
                          provincia.nome, interpello.link)

    # Digest del mattino: solo roba gia' segnalata nei giorni precedenti e ancora aperta.
    if modalita != "backfill":
        ora_digest = int(conf.esecuzione.get("ora_digest", 13))
        giorni_digest = conf.esecuzione.get("giorni_digest") or None
        if stato.digest_dovuto(adesso, ora_digest, giorni_digest):
            aperti = [v for v in stato.aperti_non_scaduti(adesso) if v["chiave"] not in chiavi_di_oggi]
            anche_vuoto = bool(conf.esecuzione.get("digest_anche_vuoto", False))
            testo = ""
            if aperti:
                log.info("digest: %d interpelli ancora aperti", len(aperti))
                testo = componi_digest(aperti, adesso)
            elif anche_vuoto:
                # Un messaggio al giorno anche a mani vuote: e' la conferma che il bot
                # ha girato davvero, visto che GitHub qualche run schedulato lo salta.
                log.info("digest: niente di aperto, mando la conferma giornaliera")
                testo = componi_digest_vuoto(adesso)
            else:
                log.info("digest: niente di aperto, non mando nulla")

            if testo:
                if telegram is None:
                    print("-" * 72)
                    print(testo)
                elif telegram.invia(testo):
                    inviati += 1
                else:
                    falliti += 1
            stato.segna_digest(adesso.date())

    # I comandi arrivati in chat si smaltiscono anche qui: non costa una riga di piu' di
    # CI e chi scrive /digest poco prima di un controllo viene servito subito.
    if telegram is not None:
        try:
            eseguiti = processa_comandi(conf, stato, telegram, sessione)
            if eseguiti:
                log.info("%d comando/i eseguito/i dalla chat", eseguiti)
        except Exception as e:  # noqa: BLE001 - i comandi non devono far fallire il controllo
            log.warning("comandi non elaborati: %s", e)

    tolti = stato.prune(int(conf.esecuzione.get("ritenzione_mesi", 12)), adesso)
    stato.pulisci_aperti(adesso)
    if tolti:
        log.info("pulizia: %d voci piu' vecchie della ritenzione rimosse", tolti)

    if modalita == "dry-run":
        log.info("dry-run: %d messaggi sarebbero stati inviati, stato NON salvato", inviati)
        return 0

    stato.salva()
    log.info("stato salvato in %s (%d voci)", PERCORSO_STATO, len(stato.visti))
    if modalita == "backfill":
        log.info("backfill completato: nessun messaggio inviato, adesso il bot e' allineato")
    else:
        log.info("run completato: %d inviati, %d falliti", inviati, falliti)
    return 1 if falliti else 0


def comando_comandi(conf: Config, ascolta: bool = False) -> int:
    """Legge ed esegue i comandi scritti in chat.

    Con ascolta=False fa una passata sola (e' quello che lancia il workflow schedulato);
    con ascolta=True resta in long polling e risponde all'istante, finche' non si preme
    Ctrl+C: comodo da tenere aperto sul PC quando si sta seguendo un interpello.
    """
    token, chat_id = segreti_telegram()
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID non configurati (vedi README)")
        return 1
    telegram = Telegram(token, chat_id, timeout=float(conf.http.get("timeout", 10)),
                        limite=int(conf.telegram.get("max_caratteri", 4096)))
    sessione = crea_sessione(conf)
    stato = Stato.carica(PERCORSO_STATO)

    if not ascolta:
        eseguiti = processa_comandi(conf, stato, telegram, sessione)
        stato.salva()
        log.info("comandi eseguiti: %d", eseguiti)
        return 0

    log.info("in ascolto dei comandi (Ctrl+C per uscire)")
    try:
        while True:
            eseguiti = processa_comandi(conf, stato, telegram, sessione, attesa=30)
            if eseguiti:
                stato.salva()
    except KeyboardInterrupt:
        stato.salva()
        log.info("ascolto interrotto, stato salvato")
    return 0


def comando_test_telegram(conf: Config) -> int:
    token, chat_id = segreti_telegram()
    if not token or not chat_id:
        log.error("TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID non configurati (vedi README)")
        return 1
    telegram = Telegram(token, chat_id, timeout=float(conf.http.get("timeout", 10)))
    ok, dettaglio = telegram.verifica()
    if not ok:
        log.error("token non valido: %s", dettaglio)
        return 1
    log.info("bot riconosciuto: %s", dettaglio)
    if telegram.imposta_menu_comandi(MENU):
        log.info("menu dei comandi registrato su Telegram: %s", ", ".join("/" + n for n, _ in MENU))
    adesso = datetime.now(ZoneInfo(conf.fuso))
    testo = (
        "✅ <b>bot-interpelli</b> e' configurato correttamente.\n"
        f"Province monitorate: {', '.join(p.nome for p in conf.province_attive)}\n"
        f"Ora italiana: {adesso.strftime('%d/%m/%Y %H:%M')}"
    )
    if telegram.invia(testo):
        log.info("messaggio di prova inviato")
        return 0
    log.error("invio del messaggio di prova fallito")
    return 1


def costruisci_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m interpelli.main",
        description="Monitoraggio degli interpelli di informatica sui siti degli UST dell'Emilia-Romagna.",
    )
    gruppo = parser.add_mutually_exclusive_group(required=True)
    gruppo.add_argument("--check", action="store_true", help="run normale: cerca e invia")
    gruppo.add_argument("--dry-run", action="store_true", help="stampa a video invece di inviare")
    gruppo.add_argument("--backfill", action="store_true",
                        help="marca come gia' visti tutti i post esistenti, senza inviare nulla")
    gruppo.add_argument("--test-telegram", action="store_true", help="manda un messaggio di prova")
    gruppo.add_argument("--discover", action="store_true",
                        help="ricognizione: quale metodo e quale URL funzionano per ogni sito")
    gruppo.add_argument("--comandi", action="store_true",
                        help="legge ed esegue i comandi arrivati in chat (una passata sola)")
    gruppo.add_argument("--ascolta", action="store_true",
                        help="resta in ascolto dei comandi e risponde subito (Ctrl+C per uscire)")
    parser.add_argument("--provincia", help="limita l'esecuzione a una provincia (anche parziale)")
    parser.add_argument("-v", "--verboso", action="store_true", help="log di debug")
    return parser


def main(argv: list[str] | None = None) -> int:
    _prepara_console()
    argomenti = costruisci_parser().parse_args(argv)
    _configura_log(argomenti.verboso)

    conf = carica_config()
    province = _province_scelte(conf, argomenti.provincia)

    if argomenti.discover:
        return comando_discover(conf, province)
    if argomenti.test_telegram:
        return comando_test_telegram(conf)
    if argomenti.comandi:
        return comando_comandi(conf, ascolta=False)
    if argomenti.ascolta:
        return comando_comandi(conf, ascolta=True)
    if argomenti.backfill:
        return comando_run(conf, province, "backfill")
    if argomenti.dry_run:
        return comando_run(conf, province, "dry-run")
    return comando_run(conf, province, "check")


if __name__ == "__main__":
    raise SystemExit(main())
