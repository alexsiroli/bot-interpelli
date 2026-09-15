"""Recupero dei post dai siti degli UST, con la catena di fallback prevista dalla spec.

Ordine: REST API di WordPress -> feed RSS della categoria -> feed RSS generale del sito.
Al 2026-09-15 tutte e cinque le province hanno le REST API attive (vedi README), ma la
catena resta perche' il giorno che una le spegne il bot deve continuare a funzionare.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import feedparser
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import Config, Provincia

log = logging.getLogger(__name__)

METODO_REST = "rest"
METODO_RSS_CATEGORIA = "rss-categoria"
METODO_RSS_GENERALE = "rss-generale"


@dataclass
class Post:
    """Un post grezzo, prima del filtro sulle classi di concorso."""

    provincia: str
    post_id: str
    titolo_html: str
    contenuto_html: str
    link: str
    pubblicato: datetime | None = None


@dataclass
class Esito:
    """Cosa e' successo su una provincia in un run."""

    provincia: str
    metodo: str | None = None
    dettaglio: str = ""
    post: list[Post] = field(default_factory=list)
    errore: str | None = None

    @property
    def ok(self) -> bool:
        return self.errore is None


def crea_sessione(conf: Config) -> requests.Session:
    """Sessione HTTP con retry, backoff e User-Agent identificabile."""
    http = conf.http
    sessione = requests.Session()
    retry = Retry(
        total=int(http.get("retry", 2)),
        backoff_factor=float(http.get("backoff", 1.0)),
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry)
    sessione.mount("https://", adapter)
    sessione.mount("http://", adapter)
    sessione.headers.update({"User-Agent": http.get("user_agent", "bot-interpelli/1.0")})
    return sessione


def _get(sessione: requests.Session, url: str, conf: Config) -> requests.Response:
    risposta = sessione.get(url, timeout=float(conf.http.get("timeout", 10)))
    risposta.raise_for_status()
    return risposta


def anno_scolastico(oggi: datetime) -> tuple[int, int]:
    """Anno scolastico corrente: da settembre in poi si guarda gia' a quello nuovo."""
    return (oggi.year, oggi.year + 1) if oggi.month >= 9 else (oggi.year - 1, oggi.year)


def _regex_anno(oggi: datetime) -> re.Pattern:
    """Regex sul nome categoria per l'anno scolastico corrente.

    I nomi cambiano da sito a sito: "Interpelli a.s. 2026/27" (Forli-Cesena),
    "Interpelli docenti 2026/2027" (Rimini), "Interpelli personale docente 2026/27" (Modena).
    """
    inizio, fine = anno_scolastico(oggi)
    return re.compile(rf"{inizio}\s*[/\-_]\s*({fine}|{str(fine)[2:]})")


def risolvi_categoria(
    sessione: requests.Session, provincia: Provincia, conf: Config, oggi: datetime | None = None
) -> dict[str, Any] | None:
    """Trova la categoria "interpelli" da leggere su un sito.

    Non si puo' hardcodare ne' l'id ne' lo slug: Forli-Cesena usa
    'interpelli-a-s-2026-27', Rimini 'interpelli-docenti-2026-2027', Ravenna una sola
    categoria 'inter' senza anno, e a settembre 2026 Bologna non aveva ancora creato
    quella del nuovo anno. Quindi: si cercano tutte le categorie che contengono
    "interpell", si prende quella dell'anno scolastico corrente e, se non esiste, quella
    con l'id piu' alto (la piu' recente creata).
    """
    oggi = oggi or datetime.now(timezone.utc)
    url = (
        f"{provincia.base_url}/wp-json/wp/v2/categories"
        "?search=interpell&per_page=50&_fields=id,name,slug,count"
    )
    categorie = _get(sessione, url, conf).json()
    if not isinstance(categorie, list) or not categorie:
        return None

    # Su alcuni siti la ricerca restituisce anche categorie non pertinenti: si tengono
    # solo quelle che hanno davvero "interpell" nel nome o nello slug.
    candidate = [
        c
        for c in categorie
        if "interpell" in f"{c.get('name', '')} {c.get('slug', '')}".lower() or c.get("slug") == "inter"
    ]
    if not candidate:
        return None

    if provincia.categoria_preferita:
        preferita = re.compile(provincia.categoria_preferita, re.IGNORECASE)
        scelte = [c for c in candidate if preferita.search(c.get("name", ""))]
        if scelte:
            return {**scelte[0], "criterio": "categoria_preferita da config.yaml"}

    anno = _regex_anno(oggi)
    scelte = [c for c in candidate if anno.search(c.get("name", ""))]
    if scelte:
        return {**scelte[0], "criterio": "anno scolastico corrente"}

    piu_recente = max(candidate, key=lambda c: c.get("id", 0))
    inizio, fine = anno_scolastico(oggi)
    criterio = (
        "categoria unica senza anno"
        if len(candidate) == 1
        else f"nessuna categoria {inizio}/{str(fine)[2:]}: ripiego sulla piu' recente"
    )
    return {**piu_recente, "criterio": criterio}


def _id_da_guid(guid: str, link: str) -> str:
    """WordPress usa guid del tipo https://sito/?p=31599: l'id numerico e' lo stesso
    restituito dalle REST API, quindi REST e RSS producono la stessa chiave di deduplica."""
    for candidato in (guid or "", link or ""):
        m = re.search(r"[?&]p=(\d+)", candidato)
        if m:
            return m.group(1)
    return (guid or link or "").strip()


def post_da_rest(
    sessione: requests.Session, provincia: Provincia, categoria_id: int, conf: Config
) -> list[Post]:
    quanti = int(conf.http.get("max_post_per_provincia", 20))
    url = (
        f"{provincia.base_url}/wp-json/wp/v2/posts?categories={categoria_id}"
        f"&per_page={quanti}&_fields=id,date,link,title,content"
    )
    dati = _get(sessione, url, conf).json()
    post = []
    for voce in dati:
        pubblicato = None
        if voce.get("date"):
            try:
                pubblicato = datetime.fromisoformat(voce["date"])
            except ValueError:
                pubblicato = None
        post.append(
            Post(
                provincia=provincia.chiave,
                post_id=str(voce.get("id")),
                titolo_html=(voce.get("title") or {}).get("rendered", ""),
                contenuto_html=(voce.get("content") or {}).get("rendered", ""),
                link=voce.get("link", ""),
                pubblicato=pubblicato,
            )
        )
    return post


def scopri_feed_categoria(
    sessione: requests.Session, provincia: Provincia, slug: str, conf: Config
) -> str | None:
    """Legge l'URL del feed dalla pagina HTML della categoria.

    La spec e' esplicita: lo slug del feed non si costruisce a mano perche' cambia da
    sito a sito. Nella <head> ci sono piu' <link rel="alternate">: quello buono e'
    l'unico che punta a /category/.
    """
    url = f"{provincia.base_url}/category/{slug}/"
    html_pagina = _get(sessione, url, conf).text
    for m in re.finditer(r"<link[^>]+application/rss\+xml[^>]*>", html_pagina, re.IGNORECASE):
        tag = m.group(0)
        href = re.search(r'href=["\']([^"\']+)["\']', tag)
        if href and "/category/" in href.group(1):
            return href.group(1)
    return None


def post_da_feed(
    sessione: requests.Session, provincia: Provincia, url: str, conf: Config, solo_interpelli: bool = False
) -> list[Post]:
    """Legge un feed RSS. Con solo_interpelli=True tiene solo gli item la cui categoria
    contiene "interpell" (serve per il feed generale del sito, che contiene di tutto)."""
    risposta = _get(sessione, url, conf)
    feed = feedparser.parse(risposta.content)
    post = []
    for voce in feed.entries[: int(conf.http.get("max_post_per_provincia", 20))]:
        categorie = " ".join(t.get("term", "") for t in voce.get("tags", []) or [])
        if solo_interpelli and "interpell" not in categorie.lower():
            continue
        contenuto = ""
        if voce.get("content"):
            contenuto = voce["content"][0].get("value", "")
        contenuto = contenuto or voce.get("summary", "")
        pubblicato = None
        if voce.get("published_parsed"):
            pubblicato = datetime(*voce["published_parsed"][:6], tzinfo=timezone.utc)
        post.append(
            Post(
                provincia=provincia.chiave,
                post_id=_id_da_guid(voce.get("id", ""), voce.get("link", "")),
                titolo_html=voce.get("title", ""),
                contenuto_html=contenuto,
                link=voce.get("link", ""),
                pubblicato=pubblicato,
            )
        )
    return post


def recupera(
    sessione: requests.Session, provincia: Provincia, conf: Config, oggi: datetime | None = None
) -> Esito:
    """Prova i tre metodi in ordine e restituisce il primo che porta a casa dei post.

    Non solleva mai: un sito rotto diventa un Esito con errore, cosi' il run continua
    sulle altre province e lo stato di questa resta invariato (spec §6).
    """
    esito = Esito(provincia=provincia.chiave)
    categoria = None

    try:
        categoria = risolvi_categoria(sessione, provincia, conf, oggi)
        if categoria:
            post = post_da_rest(sessione, provincia, categoria["id"], conf)
            if post:
                esito.metodo = METODO_REST
                esito.dettaglio = f"categoria {categoria['id']} \"{categoria['name']}\" ({categoria['criterio']})"
                esito.post = post
                return esito
            log.warning("[%s] REST API ok ma nessun post nella categoria", provincia.nome)
    except Exception as e:  # noqa: BLE001 - qualunque guaio qui deve degradare, non fermare
        log.warning("[%s] REST API non utilizzabile: %s", provincia.nome, e)

    try:
        if categoria and categoria.get("slug"):
            url_feed = scopri_feed_categoria(sessione, provincia, categoria["slug"], conf)
            if url_feed:
                post = post_da_feed(sessione, provincia, url_feed, conf)
                if post:
                    esito.metodo = METODO_RSS_CATEGORIA
                    esito.dettaglio = url_feed
                    esito.post = post
                    return esito
    except Exception as e:  # noqa: BLE001
        log.warning("[%s] feed di categoria non utilizzabile: %s", provincia.nome, e)

    try:
        url_feed = f"{provincia.base_url}/feed/"
        post = post_da_feed(sessione, provincia, url_feed, conf, solo_interpelli=True)
        if post:
            esito.metodo = METODO_RSS_GENERALE
            esito.dettaglio = f"{url_feed} (filtrato su categoria 'interpell')"
            esito.post = post
            return esito
        esito.errore = "nessun post trovato con nessuno dei tre metodi"
    except Exception as e:  # noqa: BLE001
        esito.errore = f"tutti i metodi falliti, ultimo errore: {e}"

    log.warning("[%s] %s", provincia.nome, esito.errore)
    return esito
