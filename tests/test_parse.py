"""Estrazione dei campi dai post reali."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from conftest import carica_post
from interpelli.parse import (
    MANCANTE,
    costruisci_interpello,
    descrivi_tempo_rimanente,
    estrai_email,
    estrai_ore,
    estrai_pdf,
    estrai_periodo,
    estrai_scadenza,
)
# Alias: pytest raccoglierebbe 'testo_piano' come test, visto che inizia per 'test'.
from interpelli.parse import testo_piano as html_a_testo

ROMA = ZoneInfo("Europe/Rome")


def interpello_da(nome, provincia="test"):
    post = carica_post(nome)
    return costruisci_interpello(
        provincia=provincia,
        post_id=post["id"],
        titolo_html=post["title"]["rendered"],
        contenuto_html=post["content"]["rendered"],
        link=post["link"],
    )


def test_post_completo_forli_cesena():
    """Il caso migliore: il corpo del post contiene tutto."""
    i = interpello_da("post_fc_28301_a041.json")
    assert i.classe == "A-041"
    assert "Scienze" in i.disciplina
    assert i.ore == "18"
    assert (i.inizio, i.fine) == ("03/12/2025", "13/12/2025")
    assert i.scadenza == datetime(2025, 12, 2, 12, 0, tzinfo=ROMA)
    assert i.scuola == "I.P. Ruffilli"
    assert i.email == "forf040008@istruzione.it"
    assert i.pdf == [
        "https://fc.istruzioneer.gov.it/wp-content/uploads/sites/4/2025/12/ruffilli.pdf"
    ]


def test_post_minimo_modena_non_inventa_i_campi():
    """Modena spesso pubblica solo il link al PDF: i campi assenti restano MANCANTE."""
    i = interpello_da("post_mo_43921_a041.json")
    assert i.classe == "A-041"
    assert i.ore == MANCANTE
    assert i.scadenza is None
    assert i.scadenza_testo == MANCANTE
    assert i.pdf  # il PDF pero' c'e' sempre


def test_scadenza_con_ora_e_data_con_i_due_punti():
    i = interpello_da("post_fc_31599_bi02.json")
    assert i.scadenza == datetime(2026, 9, 18, 10, 0, tzinfo=ROMA)


def test_post_con_piu_posti_prende_le_ore_giuste():
    """Il post A042 elenca tre posti e contiene '2 ore a disposizione': non deve vincere."""
    i = interpello_da("post_fc_31512_a042.json")
    assert i.ore == "18"
    assert i.fine == "30/06/2027"  # dalla riga "Durata: 30/06/2027"


def test_pdf_senza_blocco_post_attachments():
    """Ravenna mette i PDF in una lista normale, senza <ul class='post-attachments'>."""
    post = carica_post("posts_ra_dsga_list.json")
    pdf = estrai_pdf(post["content"]["rendered"])
    assert len(pdf) == 2
    assert all(l.endswith(".pdf") for l in pdf)


def test_pdf_ignora_i_link_non_pdf():
    html = '<p><a href="https://scuola.edu.it/pagina">bando</a></p>'
    assert estrai_pdf(html) == []


@pytest.mark.parametrize(
    "testo, atteso",
    [
        ("ORARIO: 18 ORE DIURNO", "18"),
        ("n. ore 6 settimanali", "6"),
        ("Ore: 17 settimanali", "17"),
        ("cattedra 12/18", "12/18"),
        ("c.d.c. A041 - h 9 - I.I.S.", "9"),
        ("nessuna indicazione", MANCANTE),
    ],
)
def test_ore(testo, atteso):
    assert estrai_ore("", testo) == atteso


@pytest.mark.parametrize(
    "testo, atteso",
    [
        ("entro le ore 10:00 del 18/09/2026", datetime(2026, 9, 18, 10, 0, tzinfo=ROMA)),
        ("entro le ore 12.00 del 02/12/2025", datetime(2025, 12, 2, 12, 0, tzinfo=ROMA)),
        ("entro e non oltre le ore 9 del 01/10/2026", datetime(2026, 10, 1, 9, 0, tzinfo=ROMA)),
        ("candidarsi entro il 30/09/2026", datetime(2026, 9, 30, 23, 59, tzinfo=ROMA)),
        ("entro il 3 ottobre 2026", datetime(2026, 10, 3, 23, 59, tzinfo=ROMA)),
        ("Scadenza: 05/11/2026", datetime(2026, 11, 5, 23, 59, tzinfo=ROMA)),
    ],
)
def test_scadenze(testo, atteso):
    data, originale = estrai_scadenza(testo)
    assert data == atteso
    assert originale != MANCANTE


def test_scadenza_assente():
    assert estrai_scadenza("nessun termine indicato") == (None, MANCANTE)


def test_scadenza_con_data_impossibile_non_esplode():
    assert estrai_scadenza("entro il 32/13/2026")[0] is None


def test_periodo_dal_al():
    inizio, fine = estrai_periodo("", "SUPPLENZA DAL 03/12/2025 AL 13/12/2025.")
    assert (inizio, fine) == ("03/12/2025", "13/12/2025")


def test_periodo_solo_fine_con_anno_a_due_cifre():
    inizio, fine = estrai_periodo("Interpello al 30.06.27 - cdc A041", "")
    assert (inizio, fine) == (MANCANTE, "30/06/2027")


def test_email_preferisce_la_pec():
    html = '<a href="mailto:segreteria@istruzione.it">x</a> <a href="mailto:foic@pec.istruzione.it">y</a>'
    assert estrai_email(html, "") == "foic@pec.istruzione.it"


def test_email_dal_testo_quando_non_ci_sono_mailto():
    assert estrai_email("", "inviare a boic812007@istruzione.it entro") == "boic812007@istruzione.it"


def test_email_assente():
    assert estrai_email("", "nessun contatto") == MANCANTE


def test_testo_piano_conserva_le_maiuscole_e_va_a_capo():
    piano = html_a_testo("<p>DOCENTE: <strong>A041</strong><br />ORARIO: 18 ORE</p>")
    assert "DOCENTE: A041" in piano
    assert "\nORARIO: 18 ORE" in piano


@pytest.mark.parametrize(
    "scadenza, atteso",
    [
        (datetime(2026, 9, 18, 10, 0, tzinfo=ROMA), "fra 2 giorni e 19 ore"),
        (datetime(2026, 9, 15, 17, 30, tzinfo=ROMA), "fra 2 ore e 30 min"),
        (datetime(2026, 9, 15, 15, 10, tzinfo=ROMA), "fra 10 minuti"),
        (datetime(2026, 9, 14, 10, 0, tzinfo=ROMA), "SCADUTO"),
    ],
)
def test_tempo_rimanente(scadenza, atteso):
    adesso = datetime(2026, 9, 15, 15, 0, tzinfo=ROMA)
    assert descrivi_tempo_rimanente(scadenza, adesso) == atteso


def test_tempo_rimanente_senza_scadenza():
    assert descrivi_tempo_rimanente(None, datetime.now(ROMA)) == ""


def test_chiave_di_deduplica():
    i = interpello_da("post_fc_28301_a041.json", provincia="forli-cesena")
    assert i.chiave == "forli-cesena:28301"
