"""Il filtro sulle classi di concorso: il cuore del bot.

Un falso negativo qui significa perdere un interpello, un falso positivo significa
notifiche inutili. I casi negativi sono post reali di classi vicine (A-042 meccanica,
A-044 tessile, BI02 conversazione cinese) che sui siti compaiono negli stessi elenchi.
"""

import pytest

from conftest import carica_post
from interpelli.filters import FiltroClassi, normalizza


@pytest.fixture
def filtro(conf):
    return FiltroClassi(conf.classi_di_concorso)


def valuta_fixture(filtro, nome):
    post = carica_post(nome)
    return filtro.valuta(post["title"]["rendered"], post["content"]["rendered"])


@pytest.mark.parametrize(
    "fixture",
    [
        "post_fc_28301_a041.json",  # A041 con corpo completo (Forli-Cesena)
        "post_mo_43921_a041.json",  # A041 con corpo minimo (Modena)
    ],
)
def test_post_di_informatica_passano(filtro, fixture):
    assert valuta_fixture(filtro, fixture).passa


@pytest.mark.parametrize(
    "fixture",
    [
        "post_fc_31512_a042.json",   # A042 scienze e tecnologie meccaniche
        "post_fc_31494_a044.json",   # A044 scienze e tecnologie tessili
        "post_fc_31599_bi02.json",   # BI02 conversazione cinese
        "posts_ra_dsga_list.json",   # graduatoria DSGA (nemmeno un interpello docenti)
    ],
)
def test_post_di_altre_classi_vengono_scartati(filtro, fixture):
    assert not valuta_fixture(filtro, fixture).passa


@pytest.mark.parametrize(
    "testo",
    ["c.d.c. A041", "cdc A-041", "classe A 041", "A41 informatica", "A-41", "b016", "B-16", "B 016"],
)
def test_varianti_dei_codici(filtro, testo):
    assert filtro.valuta(testo).passa


@pytest.mark.parametrize("testo", ["A-040", "A042", "A 044", "A034", "BI02", "A066xyz", "AA041"])
def test_codici_vicini_non_matchano(filtro, testo):
    assert not filtro.valuta(testo).passa


@pytest.mark.parametrize(
    "testo",
    ["Scienze e tecnologie INFORMATICHE", "laboratorio di informatica", "Sistemi e Reti", "Informatico"],
)
def test_parole_chiave(filtro, testo):
    assert filtro.valuta(testo).passa


def test_le_esclusioni_hanno_la_precedenza():
    filtro = FiltroClassi(
        {"codici": [r"\ba[-\s]?0?41\b"], "parole": ["informatic"], "esclusioni": [r"\bnon\s+valido\b"]}
    )
    assert filtro.valuta("interpello A041 informatica").passa
    assert not filtro.valuta("interpello A041 informatica NON VALIDO").passa


def test_normalizzazione_toglie_tag_entita_e_accenti():
    grezzo = "<p>Attivit&agrave; di <strong>INFORMATICA</strong>&#8211;pi&ugrave;</p>"
    assert normalizza(grezzo) == "attivita di informatica –piu"


def test_normalizzazione_regge_il_doppio_escape():
    assert "informatica" in normalizza("&amp;lt;b&amp;gt;informatica")


def test_filtro_su_testo_vuoto(filtro):
    assert not filtro.valuta("", None).passa
