"""Prove sull'orchestrazione: deduplica, invii falliti, backfill, digest.

Qui si verifica il requisito critico della spec §3 e §6: nessun doppione, e un post
che non e' stato consegnato non deve mai risultare "gia' visto".
"""

import json

import pytest
import responses

from conftest import carica_json
from interpelli import main as modulo_main
from interpelli.config import Config, Provincia
from interpelli.state import Stato

TOKEN = "123456:FINTO-TOKEN"
URL_TELEGRAM = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
BASE = "https://fc.istruzioneer.gov.it"


@pytest.fixture
def stato_temporaneo(tmp_path, monkeypatch):
    percorso = tmp_path / "seen.json"
    monkeypatch.setattr(modulo_main, "PERCORSO_STATO", percorso)
    return percorso


@pytest.fixture
def segreti(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")


@pytest.fixture
def config_una_provincia(conf):
    """Una sola provincia, cosi' i mock restano leggibili."""
    return Config(
        province=[Provincia(nome="Forli-Cesena", base_url=BASE)],
        http=conf.http,
        esecuzione=conf.esecuzione,
        classi_di_concorso=conf.classi_di_concorso,
        telegram=conf.telegram,
    )


def mock_sito(post_fixtures):
    responses.add(
        responses.GET, BASE + "/wp-json/wp/v2/categories", json=carica_json("categories_fc.json")
    )
    responses.add(
        responses.GET,
        BASE + "/wp-json/wp/v2/posts",
        json=[carica_json(nome) for nome in post_fixtures],
    )


@responses.activate
def test_manda_solo_gli_interpelli_di_informatica(config_una_provincia, stato_temporaneo, segreti):
    mock_sito(["post_fc_28301_a041.json", "post_fc_31512_a042.json"])
    responses.add(responses.POST, URL_TELEGRAM, json={"ok": True}, status=200)

    codice = modulo_main.comando_run(config_una_provincia, config_una_provincia.province, "check")

    assert codice == 0
    invii = [c for c in responses.calls if c.request.url.startswith(URL_TELEGRAM)]
    assert len(invii) == 1
    stato = json.loads(stato_temporaneo.read_text(encoding="utf-8"))
    assert "forli-cesena:28301" in stato["visti"]
    assert "forli-cesena:31512" not in stato["visti"]  # A042: mai inviato, mai marcato


@responses.activate
def test_nessun_doppione_al_secondo_giro(config_una_provincia, stato_temporaneo, segreti):
    mock_sito(["post_fc_28301_a041.json"])
    responses.add(responses.POST, URL_TELEGRAM, json={"ok": True}, status=200)
    modulo_main.comando_run(config_una_provincia, config_una_provincia.province, "check")
    primi_invii = len([c for c in responses.calls if c.request.url.startswith(URL_TELEGRAM)])

    modulo_main.comando_run(config_una_provincia, config_una_provincia.province, "check")
    invii_totali = len([c for c in responses.calls if c.request.url.startswith(URL_TELEGRAM)])

    assert primi_invii == 1
    assert invii_totali == 1  # il secondo run non ha rimandato niente


@responses.activate
def test_invio_fallito_non_marca_il_post_come_visto(
    config_una_provincia, stato_temporaneo, segreti
):
    mock_sito(["post_fc_28301_a041.json"])
    responses.add(responses.POST, URL_TELEGRAM, json={"ok": False}, status=400)

    codice = modulo_main.comando_run(config_una_provincia, config_una_provincia.province, "check")

    assert codice == 1  # il run segnala il problema
    stato = Stato.carica(stato_temporaneo)
    assert not stato.e_visto("forli-cesena:28301")  # al prossimo giro si riprova


@responses.activate
def test_backfill_marca_tutto_senza_inviare(config_una_provincia, stato_temporaneo, segreti):
    mock_sito(["post_fc_28301_a041.json", "post_fc_31512_a042.json"])
    responses.add(responses.POST, URL_TELEGRAM, json={"ok": True}, status=200)

    modulo_main.comando_run(config_una_provincia, config_una_provincia.province, "backfill")

    assert not [c for c in responses.calls if c.request.url.startswith(URL_TELEGRAM)]
    stato = Stato.carica(stato_temporaneo)
    assert stato.e_visto("forli-cesena:28301")
    assert stato.e_visto("forli-cesena:31512")


@responses.activate
def test_dry_run_non_scrive_lo_stato(config_una_provincia, stato_temporaneo, segreti, capsys):
    mock_sito(["post_fc_28301_a041.json"])

    modulo_main.comando_run(config_una_provincia, config_una_provincia.province, "dry-run")

    assert not stato_temporaneo.exists()
    assert "Nuovo interpello" in capsys.readouterr().out


@responses.activate
def test_senza_segreti_il_run_non_fallisce(config_una_provincia, stato_temporaneo, monkeypatch):
    """Primo run del workflow, secret non ancora configurati: deve restare verde."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr("interpelli.config.PERCORSO_ENV", stato_temporaneo.parent / "assente.env")
    mock_sito(["post_fc_28301_a041.json"])

    codice = modulo_main.comando_run(config_una_provincia, config_una_provincia.province, "check")

    assert codice == 0
    assert not [c for c in responses.calls if c.request.url.startswith(URL_TELEGRAM)]


@responses.activate
def test_provincia_rotta_non_ferma_il_run(conf, stato_temporaneo, segreti):
    """Due province, la prima rotta: la seconda deve comunque essere elaborata."""
    rotta = Provincia(nome="Rotta", base_url="https://rotta.example.org")
    config = Config(
        province=[rotta, Provincia(nome="Forli-Cesena", base_url=BASE)],
        http=conf.http,
        esecuzione=conf.esecuzione,
        classi_di_concorso=conf.classi_di_concorso,
        telegram=conf.telegram,
    )
    responses.add(responses.GET, rotta.base_url + "/wp-json/wp/v2/categories", status=503)
    responses.add(responses.GET, rotta.base_url + "/feed/", status=503)
    mock_sito(["post_fc_28301_a041.json"])
    responses.add(responses.POST, URL_TELEGRAM, json={"ok": True}, status=200)

    codice = modulo_main.comando_run(config, config.province, "check")

    assert codice == 0
    assert Stato.carica(stato_temporaneo).e_visto("forli-cesena:28301")
