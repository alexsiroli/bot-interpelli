"""Comandi scritti in chat: /digest, /all, /stato, /aiuto."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import responses

from interpelli.comandi import (
    componi_stato,
    componi_tutti,
    esegui_comando,
    nome_comando,
    processa_comandi,
    raccogli_tutti,
)
from interpelli.config import Config, Provincia
from interpelli.fetch import Post, crea_sessione
from interpelli.parse import Interpello
from interpelli.state import Stato
from interpelli.telegram import Telegram

from conftest import carica_json

ROMA = ZoneInfo("Europe/Rome")
ADESSO = datetime(2026, 9, 15, 15, 0, tzinfo=ROMA)
TOKEN = "123456:FINTO-TOKEN"
INVIO = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
UPDATES = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
BASE = "https://fc.istruzioneer.gov.it"
CHAT = "111222333"


@pytest.fixture
def telegram():
    return Telegram(TOKEN, CHAT)


@pytest.fixture
def stato(tmp_path):
    return Stato.carica(tmp_path / "seen.json")


@pytest.fixture
def config_una_provincia(conf):
    return Config(
        province=[Provincia(nome="Forli-Cesena", base_url=BASE)],
        http=conf.http,
        esecuzione=conf.esecuzione,
        classi_di_concorso=conf.classi_di_concorso,
        telegram=conf.telegram,
    )


def aggiornamento(testo, update_id=1, chat_id=CHAT):
    return {"update_id": update_id, "message": {"chat": {"id": int(chat_id)}, "text": testo}}


@pytest.mark.parametrize(
    "testo, atteso",
    [
        ("/digest", "digest"),
        ("/all@inter_alex_bot", "all"),
        ("/AIUTO", "aiuto"),
        ("/stato con parole dopo", "stato"),
        ("buongiorno", None),
        ("", None),
    ],
)
def test_riconoscimento_del_comando(testo, atteso):
    assert nome_comando({"text": testo}) == atteso


@responses.activate
def test_digest_su_richiesta_con_interpelli_aperti(config_una_provincia, stato, telegram):
    responses.add(responses.POST, INVIO, json={"ok": True}, status=200)
    stato.registra_aperto(
        Interpello(
            provincia="forli-cesena", post_id="1", titolo="Interpello A041",
            link="https://x", provincia_nome="Forli-Cesena", classe="A-041",
            scuola="I.P. Ruffilli", scadenza=ADESSO + timedelta(days=2),
        )
    )
    stato.marca_visto("forli-cesena:1", ADESSO)

    assert esegui_comando("digest", config_una_provincia, stato, telegram, crea_sessione(config_una_provincia))
    corpo = responses.calls[0].request.body
    assert "Ruffilli" in corpo


@responses.activate
def test_digest_su_richiesta_risponde_anche_se_non_c_e_niente(config_una_provincia, stato, telegram):
    """Il digest automatico tace, quello chiesto a mano deve rispondere lo stesso."""
    responses.add(responses.POST, INVIO, json={"ok": True}, status=200)

    assert esegui_comando("digest", config_una_provincia, stato, telegram, crea_sessione(config_una_provincia))
    assert "Nessun+interpello" in responses.calls[0].request.body.replace("%20", "+")


@responses.activate
def test_comando_sconosciuto_riceve_l_elenco(config_una_provincia, stato, telegram):
    responses.add(responses.POST, INVIO, json={"ok": True}, status=200)
    assert not esegui_comando("pizza", config_una_provincia, stato, telegram, None)


def test_elenco_breve_una_riga_per_interpello():
    post = [
        Post(provincia="forli-cesena", post_id="31599",
             titolo_html="Interpello al 30.06.27 &#8211; c.d.c. BI02 Convers. Cinese &#8211; h 6",
             contenuto_html="", link="https://fc.istruzioneer.gov.it/p/31599",
             pubblicato=datetime(2026, 9, 15, 9, 13)),
        Post(provincia="forli-cesena", post_id="31512",
             titolo_html="Interpello n. 3 supplenze &#8211; c.d.c. A042 &#8211; I.S. Pascal Comandini",
             contenuto_html="", link="https://fc.istruzioneer.gov.it/p/31512",
             pubblicato=datetime(2026, 9, 10, 13, 52)),
    ]
    testo = componi_tutti({"Forli-Cesena": post}, "Europe/Rome")

    assert "ultimi 7 giorni</b> (2)" in testo
    assert "• 15/09 <b>BI02</b>" in testo
    assert "• 10/09 <b>A-042</b>" in testo
    assert 'href="https://fc.istruzioneer.gov.it/p/31599"' in testo
    # Una riga per interpello, niente blocchi di dettaglio.
    assert len([r for r in testo.splitlines() if r.startswith("•")]) == 2


def test_elenco_breve_tronca_i_titoli_lunghi():
    post = [Post(provincia="x", post_id="1", titolo_html="A" * 200, contenuto_html="",
                 link="https://x", pubblicato=datetime(2026, 9, 15))]
    riga = [r for r in componi_tutti({"X": post}, "Europe/Rome").splitlines() if r.startswith("•")][0]
    assert "..." in riga
    assert len(riga) < 160


def test_elenco_breve_senza_risultati():
    assert "Nessun interpello" in componi_tutti({"Forli-Cesena": []}, "Europe/Rome")


@responses.activate
def test_all_prende_solo_gli_ultimi_sette_giorni(config_una_provincia, monkeypatch):
    vecchio = dict(carica_json("post_fc_28301_a041.json"))  # dicembre 2025
    recente = dict(carica_json("post_fc_31599_bi02.json"))  # 15/09/2026
    responses.add(responses.GET, BASE + "/wp-json/wp/v2/categories",
                  json=carica_json("categories_fc.json"))
    responses.add(responses.GET, BASE + "/wp-json/wp/v2/posts", json=[recente, vecchio])
    monkeypatch.setattr("interpelli.comandi._adesso", lambda conf: ADESSO)

    per_provincia = raccogli_tutti(config_una_provincia, crea_sessione(config_una_provincia))

    ids = [p.post_id for p in per_provincia["Forli-Cesena"]]
    assert ids == ["31599"]


@responses.activate
def test_processa_comandi_ignora_le_chat_non_autorizzate(config_una_provincia, stato, telegram):
    responses.add(responses.GET, UPDATES,
                  json={"result": [aggiornamento("/digest", update_id=7, chat_id="999999")]})
    responses.add(responses.POST, INVIO, json={"ok": True}, status=200)

    assert processa_comandi(config_una_provincia, stato, telegram) == 0
    assert not [c for c in responses.calls if c.request.url.startswith(INVIO)]
    # L'update va comunque consumato, altrimenti resta in coda per sempre.
    assert stato.ultimo_update_id == 7


@responses.activate
def test_processa_comandi_non_risponde_due_volte(config_una_provincia, stato, telegram):
    responses.add(responses.GET, UPDATES, json={"result": [aggiornamento("/stato", update_id=12)]})
    responses.add(responses.POST, INVIO, json={"ok": True}, status=200)

    assert processa_comandi(config_una_provincia, stato, telegram) == 1
    assert stato.ultimo_update_id == 12

    # Al giro dopo l'offset salvato fa saltare del tutto l'update gia' servito.
    responses.replace(responses.GET, UPDATES, json={"result": []})
    assert processa_comandi(config_una_provincia, stato, telegram) == 0
    richiesta = [c for c in responses.calls if c.request.url.startswith(UPDATES)][-1]
    assert "offset=13" in richiesta.request.url


def test_stato_elenca_province_attive_e_spente(conf, stato):
    testo = componi_stato(conf, stato, ADESSO)
    assert "Forli-Cesena" in testo
    assert "Province spente" in testo and "Modena" in testo
