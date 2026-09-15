"""Stato persistente: deduplica, ritenzione, digest, allerta sul silenzio."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from interpelli.parse import Interpello
from interpelli.state import Stato

ROMA = ZoneInfo("Europe/Rome")
ADESSO = datetime(2026, 9, 15, 8, 0, tzinfo=ROMA)


@pytest.fixture
def percorso(tmp_path):
    return tmp_path / "seen.json"


def interpello(chiave_post="1", scadenza=None):
    return Interpello(
        provincia="forli-cesena",
        post_id=chiave_post,
        titolo="Interpello A041",
        link="https://fc.istruzioneer.gov.it/post",
        provincia_nome="Forli-Cesena",
        classe="A-041",
        scuola="I.P. Ruffilli",
        scadenza=scadenza,
    )


def test_stato_vuoto_se_il_file_non_esiste(percorso):
    stato = Stato.carica(percorso)
    assert stato.visti == {}


def test_salva_e_ricarica(percorso):
    stato = Stato.carica(percorso)
    stato.marca_visto("forli-cesena:31599", ADESSO)
    stato.salva()

    riletto = Stato.carica(percorso)
    assert riletto.e_visto("forli-cesena:31599")
    assert not riletto.e_visto("modena:1")


def test_file_illeggibile_non_blocca_il_run(percorso):
    percorso.write_text("{ questo non e' json", encoding="utf-8")
    assert Stato.carica(percorso).visti == {}


def test_migrazione_dal_formato_piatto(percorso):
    percorso.write_text(json.dumps({"forli-cesena:1": ADESSO.isoformat()}), encoding="utf-8")
    assert Stato.carica(percorso).e_visto("forli-cesena:1")


def test_prune_toglie_solo_le_voci_vecchie(percorso):
    stato = Stato.carica(percorso)
    stato.marca_visto("vecchio", ADESSO - timedelta(days=400))
    stato.marca_visto("recente", ADESSO - timedelta(days=10))
    assert stato.prune(12, ADESSO) == 1
    assert not stato.e_visto("vecchio")
    assert stato.e_visto("recente")


def test_prune_scarta_i_timestamp_corrotti(percorso):
    stato = Stato.carica(percorso)
    stato.visti["rotto"] = "non-una-data"
    assert stato.prune(12, ADESSO) == 1


def test_aperti_tiene_solo_quelli_non_scaduti(percorso):
    stato = Stato.carica(percorso)
    aperto = interpello("1", ADESSO + timedelta(days=2))
    scaduto = interpello("2", ADESSO - timedelta(hours=1))
    stato.registra_aperto(aperto)
    stato.registra_aperto(scaduto)
    vivi = stato.aperti_non_scaduti(ADESSO)
    assert [v["chiave"] for v in vivi] == ["forli-cesena:1"]


def test_aperti_senza_scadenza_scadono_dopo_una_settimana(percorso):
    stato = Stato.carica(percorso)
    stato.registra_aperto(interpello("3", None))
    stato.marca_visto("forli-cesena:3", ADESSO - timedelta(days=3))
    assert len(stato.aperti_non_scaduti(ADESSO)) == 1

    stato.marca_visto("forli-cesena:3", ADESSO - timedelta(days=9))
    assert stato.aperti_non_scaduti(ADESSO) == []


def test_pulisci_aperti(percorso):
    stato = Stato.carica(percorso)
    stato.registra_aperto(interpello("1", ADESSO + timedelta(days=1)))
    stato.registra_aperto(interpello("2", ADESSO - timedelta(days=1)))
    stato.pulisci_aperti(ADESSO)
    assert list(stato.aperti) == ["forli-cesena:1"]


def test_digest_una_volta_al_giorno_dopo_l_ora_stabilita(percorso):
    stato = Stato.carica(percorso)
    mezzogiorno = ADESSO.replace(hour=12)
    tredici = ADESSO.replace(hour=13)
    assert not stato.digest_dovuto(mezzogiorno, 13)
    assert stato.digest_dovuto(tredici, 13)

    stato.segna_digest(tredici.date())
    assert not stato.digest_dovuto(tredici.replace(hour=16), 13)
    assert stato.digest_dovuto(tredici + timedelta(days=1), 13)


def test_digest_saltato_nel_weekend(percorso):
    """15/09/2026 e' un martedi: sabato e domenica il riepilogo non parte."""
    stato = Stato.carica(percorso)
    feriali = [1, 2, 3, 4, 5]
    martedi = ADESSO.replace(hour=13)
    sabato = martedi + timedelta(days=4)
    domenica = martedi + timedelta(days=5)
    lunedi = martedi + timedelta(days=6)

    assert sabato.isoweekday() == 6 and domenica.isoweekday() == 7
    assert stato.digest_dovuto(martedi, 13, feriali)
    assert not stato.digest_dovuto(sabato, 13, feriali)
    assert not stato.digest_dovuto(domenica, 13, feriali)
    # Saltando il weekend non si segna niente, quindi il lunedi riparte regolarmente.
    assert stato.digest_dovuto(lunedi, 13, feriali)


def test_allerta_dopo_n_run_a_vuoto(percorso):
    stato = Stato.carica(percorso)
    for _ in range(2):
        stato.registra_esito_provincia("modena", 0)
    assert not stato.allerta_da_mandare("modena", 3)

    stato.registra_esito_provincia("modena", 0)
    assert stato.allerta_da_mandare("modena", 3)

    # Una volta mandata non si ripete a ogni run.
    stato.segna_allerta("modena")
    assert not stato.allerta_da_mandare("modena", 3)

    # Quando il sito torna a rispondere il contatore si azzera e l'allerta si riarma.
    stato.registra_esito_provincia("modena", 5)
    assert stato.run_vuoti["modena"] == 0
    for _ in range(3):
        stato.registra_esito_provincia("modena", 0)
    assert stato.allerta_da_mandare("modena", 3)
