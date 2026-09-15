"""Risoluzione della categoria e catena di fallback REST -> RSS categoria -> RSS generale."""

from datetime import datetime, timezone

import pytest
import responses

from conftest import carica_json, carica_testo
from interpelli.config import Provincia
from interpelli.fetch import (
    METODO_REST,
    METODO_RSS_CATEGORIA,
    anno_scolastico,
    crea_sessione,
    post_da_feed,
    recupera,
    risolvi_categoria,
    scopri_feed_categoria,
)

FC = Provincia(nome="Forli-Cesena", base_url="https://fc.istruzioneer.gov.it")
BO = Provincia(nome="Bologna", base_url="https://bo.istruzioneer.gov.it")
RA = Provincia(nome="Ravenna", base_url="https://ra.istruzioneer.gov.it")
SETTEMBRE_2026 = datetime(2026, 9, 15, tzinfo=timezone.utc)

URL_CATEGORIE = "/wp-json/wp/v2/categories"
URL_POST = "/wp-json/wp/v2/posts"


@pytest.fixture
def sessione(conf):
    return crea_sessione(conf)


def mock_categorie(provincia, fixture):
    responses.add(
        responses.GET, provincia.base_url + URL_CATEGORIE, json=carica_json(fixture), status=200
    )


@pytest.mark.parametrize(
    "oggi, atteso",
    [
        (datetime(2026, 9, 1), (2026, 2027)),   # da settembre si guarda al nuovo anno
        (datetime(2026, 12, 31), (2026, 2027)),
        (datetime(2026, 1, 15), (2025, 2026)),  # a gennaio si e' ancora nell'anno precedente
    ],
)
def test_anno_scolastico(oggi, atteso):
    assert anno_scolastico(oggi) == atteso


@responses.activate
def test_categoria_dell_anno_corrente(sessione, conf):
    mock_categorie(FC, "categories_fc.json")
    categoria = risolvi_categoria(sessione, FC, conf, SETTEMBRE_2026)
    assert categoria["id"] == 720
    assert categoria["criterio"] == "anno scolastico corrente"


@responses.activate
def test_bologna_ripiega_sulla_categoria_piu_recente(sessione, conf):
    """A settembre 2026 Bologna non aveva ancora creato la categoria del nuovo anno."""
    mock_categorie(BO, "categories_bo.json")
    categoria = risolvi_categoria(sessione, BO, conf, SETTEMBRE_2026)
    assert categoria["id"] == 1156
    assert "ripiego" in categoria["criterio"]


@responses.activate
def test_ravenna_categoria_unica_senza_anno(sessione, conf):
    mock_categorie(RA, "categories_ra.json")
    categoria = risolvi_categoria(sessione, RA, conf, SETTEMBRE_2026)
    assert categoria["id"] == 204
    assert categoria["criterio"] == "categoria unica senza anno"


@responses.activate
def test_categoria_preferita_da_configurazione(sessione, conf):
    provincia = Provincia(
        nome="Forli-Cesena", base_url=FC.base_url, categoria_preferita=r"2024/25"
    )
    mock_categorie(provincia, "categories_fc.json")
    categoria = risolvi_categoria(sessione, provincia, conf, SETTEMBRE_2026)
    assert categoria["id"] == 613


@responses.activate
def test_nessuna_categoria_trovata(sessione, conf):
    responses.add(responses.GET, FC.base_url + URL_CATEGORIE, json=[], status=200)
    assert risolvi_categoria(sessione, FC, conf, SETTEMBRE_2026) is None


@responses.activate
def test_feed_di_categoria_letto_dalla_pagina_html(sessione, conf):
    """Lo slug del feed non si costruisce a mano: si legge il <link rel=alternate>."""
    responses.add(
        responses.GET,
        f"{FC.base_url}/category/interpelli-a-s-2026-27/",
        body=carica_testo("pagina_categoria_fc.html"),
        status=200,
    )
    url = scopri_feed_categoria(sessione, FC, "interpelli-a-s-2026-27", conf)
    assert url == "https://fc.istruzioneer.gov.it/category/interpelli-a-s-2026-27/feed/"


@responses.activate
def test_post_da_feed_usa_lo_stesso_id_delle_rest_api(sessione, conf):
    """Il guid WordPress e' https://sito/?p=31599: la chiave di deduplica coincide."""
    url = f"{FC.base_url}/category/interpelli-a-s-2026-27/feed/"
    responses.add(responses.GET, url, body=carica_testo("feed_fc_categoria.xml"), status=200)
    post = post_da_feed(sessione, FC, url, conf)
    assert post[0].post_id == "31599"
    assert post[0].provincia == "forli-cesena"
    assert "BI02" in post[0].titolo_html
    assert post[0].contenuto_html


@responses.activate
def test_fallback_su_rss_quando_le_rest_api_dei_post_falliscono(sessione, conf):
    mock_categorie(FC, "categories_fc.json")
    responses.add(responses.GET, FC.base_url + URL_POST, status=500)
    responses.add(
        responses.GET,
        f"{FC.base_url}/category/interpelli-a-s-2026-27/",
        body=carica_testo("pagina_categoria_fc.html"),
        status=200,
    )
    responses.add(
        responses.GET,
        f"{FC.base_url}/category/interpelli-a-s-2026-27/feed/",
        body=carica_testo("feed_fc_categoria.xml"),
        status=200,
    )
    esito = recupera(sessione, FC, conf, SETTEMBRE_2026)
    assert esito.ok
    assert esito.metodo == METODO_RSS_CATEGORIA
    assert esito.post


@responses.activate
def test_rest_api_preferite_quando_funzionano(sessione, conf):
    mock_categorie(FC, "categories_fc.json")
    responses.add(
        responses.GET, FC.base_url + URL_POST, json=[carica_json("post_fc_28301_a041.json")], status=200
    )
    esito = recupera(sessione, FC, conf, SETTEMBRE_2026)
    assert esito.metodo == METODO_REST
    assert esito.post[0].post_id == "28301"
    assert "720" in esito.dettaglio


@responses.activate
def test_sito_irraggiungibile_non_solleva(sessione, conf):
    """Spec §6: una provincia rotta non deve far fallire il run delle altre."""
    responses.add(responses.GET, FC.base_url + URL_CATEGORIE, status=503)
    responses.add(responses.GET, f"{FC.base_url}/feed/", status=503)
    esito = recupera(sessione, FC, conf, SETTEMBRE_2026)
    assert not esito.ok
    assert esito.post == []
    assert esito.errore
