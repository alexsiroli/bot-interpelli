"""Composizione e invio dei messaggi."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
import responses

from interpelli.parse import MANCANTE, Interpello
from interpelli.telegram import (
    LIMITE_TELEGRAM,
    Telegram,
    componi_allerta,
    componi_digest,
    componi_messaggio,
    esc,
    spezza,
)

ROMA = ZoneInfo("Europe/Rome")
ADESSO = datetime(2026, 9, 15, 15, 0, tzinfo=ROMA)
TOKEN = "123456:FINTO-TOKEN"
URL = f"https://api.telegram.org/bot{TOKEN}/sendMessage"


def interpello(**kwargs):
    base = dict(
        provincia="forli-cesena",
        post_id="31599",
        titolo="Interpello A041",
        link="https://fc.istruzioneer.gov.it/post",
        provincia_nome="Forli-Cesena",
        classe="A-041",
        disciplina="Scienze e tecnologie informatiche",
        ore="18",
        inizio="01/10/2026",
        fine="30/06/2027",
        scadenza=ADESSO + timedelta(days=2),
        scadenza_testo="entro le ore 10:00 del 17/09/2026",
        scuola="I.P. Ruffilli & C.",
        email="fo@istruzione.it",
        pdf=["https://fc.istruzioneer.gov.it/x.pdf"],
    )
    base.update(kwargs)
    return Interpello(**base)


def test_messaggio_contiene_tutti_i_campi():
    testo = componi_messaggio(interpello(), ADESSO)
    assert "Nuovo interpello — Forli-Cesena" in testo
    assert "A-041" in testo and "Scienze e tecnologie informatiche" in testo
    assert "18 ore · dal 01/10/2026 al 30/06/2027" in testo
    assert "fra 2 giorni" in testo
    assert "x.pdf" in testo and "fc.istruzioneer.gov.it/post" in testo


def test_escape_dei_caratteri_html():
    testo = componi_messaggio(interpello(scuola="Liceo <A> & B"), ADESSO)
    assert "Liceo &lt;A&gt; &amp; B" in testo
    assert "<A>" not in testo


def test_scadenza_gia_passata_lo_dice():
    testo = componi_messaggio(interpello(scadenza=ADESSO - timedelta(hours=3)), ADESSO)
    assert "GIA' SCADUTA" in testo


def test_scadenza_non_trovata_riporta_il_trattino():
    testo = componi_messaggio(interpello(scadenza=None, scadenza_testo=MANCANTE), ADESSO)
    assert f"Scadenza: {MANCANTE}" in testo


def test_esc_su_valore_nullo():
    assert esc(None) == MANCANTE


def test_digest_ordina_per_scadenza():
    aperti = [
        {"chiave": "a", "classe": "A-041", "scuola": "Tardi", "link": "l2",
         "scadenza": (ADESSO + timedelta(days=5)).isoformat(), "provincia": "modena"},
        {"chiave": "b", "classe": "B-016", "scuola": "Presto", "link": "l1",
         "scadenza": (ADESSO + timedelta(days=1)).isoformat(), "provincia": "rimini"},
    ]
    testo = componi_digest(aperti, ADESSO)
    assert testo.index("Presto") < testo.index("Tardi")
    assert "Interpelli ancora aperti</b> (2)" in testo


def test_allerta_nomina_la_provincia():
    assert "Modena" in componi_allerta("Modena", 4)


def test_spezza_solo_se_serve():
    assert spezza("corto") == ["corto"]


def test_spezza_su_confine_di_riga():
    riga = "x" * 100
    pezzi = spezza("\n".join([riga] * 100), limite=1000)
    assert len(pezzi) > 1
    assert all(len(p) <= 1000 for p in pezzi)
    assert "".join(p.replace("\n", "") for p in pezzi) == riga * 100


def test_spezza_riga_singola_piu_lunga_del_limite():
    pezzi = spezza("y" * 9000, limite=LIMITE_TELEGRAM)
    assert len(pezzi) == 3
    assert all(len(p) <= LIMITE_TELEGRAM for p in pezzi)


@responses.activate
def test_invio_riuscito():
    responses.add(responses.POST, URL, json={"ok": True}, status=200)
    assert Telegram(TOKEN, "42").invia("ciao")
    inviato = responses.calls[0].request.body
    assert "parse_mode=HTML" in inviato
    assert "disable_web_page_preview=True" in inviato


@responses.activate
def test_messaggio_lungo_diventa_piu_chiamate():
    responses.add(responses.POST, URL, json={"ok": True}, status=200)
    assert Telegram(TOKEN, "42").invia("riga\n" * 2000)
    assert len(responses.calls) > 1


@responses.activate
def test_errore_400_non_viene_ritentato():
    responses.add(responses.POST, URL, json={"ok": False, "description": "Bad Request"}, status=400)
    assert not Telegram(TOKEN, "42").invia("ciao")
    assert len(responses.calls) == 1


@responses.activate
def test_rate_limit_viene_rispettato_e_poi_riprova(monkeypatch):
    monkeypatch.setattr("interpelli.telegram.time.sleep", lambda _: None)
    responses.add(responses.POST, URL, json={"parameters": {"retry_after": 1}}, status=429)
    responses.add(responses.POST, URL, json={"ok": True}, status=200)
    assert Telegram(TOKEN, "42").invia("ciao")
    assert len(responses.calls) == 2


@responses.activate
def test_il_token_non_finisce_nei_log(caplog):
    responses.add(responses.POST, URL, json={"ok": False, "description": f"url {TOKEN}"}, status=500)
    telegram = Telegram(TOKEN, "42")
    telegram._invia_pezzo(URL, "ciao", tentativi=1)
    assert TOKEN not in caplog.text
    assert "***TOKEN***" in caplog.text


@responses.activate
def test_verifica_token():
    responses.add(
        responses.GET,
        f"https://api.telegram.org/bot{TOKEN}/getMe",
        json={"ok": True, "result": {"username": "interpelli_bot"}},
        status=200,
    )
    ok, dettaglio = Telegram(TOKEN, "42").verifica()
    assert ok and dettaglio == "@interpelli_bot"
