"""Estrazione dei campi di un interpello dal testo del post.

Regola dalla spec: se un campo non si trova si scrive MANCANTE, non lo si inventa.
I post sono scritti a mano da segreterie diverse, quindi ogni campo ha piu' pattern
provati in ordine di affidabilita'. Le fixture in tests/fixtures/ sono post veri:
qualunque modifica alle regex va verificata li'.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

MANCANTE = "—"  # trattino lungo

_TAG = re.compile(r"<[^>]+>")
_SPAZI = re.compile(r"[ \t\r\f\v]+")

# Data numerica: 03/12/2025, 30.6.27, 18-09-2026
_DATA = r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})\s*[/.\-]\s*(\d{2,4})"
_MESI = {
    "gennaio": 1, "febbraio": 2, "marzo": 3, "aprile": 4, "maggio": 5, "giugno": 6,
    "luglio": 7, "agosto": 8, "settembre": 9, "ottobre": 10, "novembre": 11, "dicembre": 12,
}
_DATA_ESTESA = r"(\d{1,2})\s+(" + "|".join(_MESI) + r")\s+(\d{4})"


@dataclass
class Interpello:
    """Un interpello gia' filtrato e con i campi estratti."""

    provincia: str  # chiave stabile usata in seen.json (es. "forli-cesena")
    post_id: str
    titolo: str
    link: str
    provincia_nome: str = ""  # nome leggibile per i messaggi (es. "Forli-Cesena")
    pubblicato: datetime | None = None
    classe: str = MANCANTE
    disciplina: str = MANCANTE
    ore: str = MANCANTE
    inizio: str = MANCANTE
    fine: str = MANCANTE
    scadenza: datetime | None = None
    scadenza_testo: str = MANCANTE
    scuola: str = MANCANTE
    email: str = MANCANTE
    pdf: list[str] = field(default_factory=list)
    motivo_filtro: str = ""

    @property
    def chiave(self) -> str:
        """Chiave di deduplica in seen.json."""
        return f"{self.provincia}:{self.post_id}"


def testo_piano(html_grezzo: str) -> str:
    """HTML -> testo leggibile, mantenendo maiuscole e accenti (servono all'estrazione)."""
    if not html_grezzo:
        return ""
    testo = re.sub(r"(?i)<br\s*/?>", "\n", html_grezzo)
    testo = re.sub(r"(?i)</p>", "\n", testo)
    testo = _TAG.sub(" ", testo)
    testo = html.unescape(html.unescape(testo)).replace("\xa0", " ")
    testo = _SPAZI.sub(" ", testo)
    return "\n".join(riga.strip() for riga in testo.splitlines()).strip()


def _anno_completo(anno: int) -> int:
    return anno + 2000 if anno < 100 else anno


def _data_da_match(m: re.Match) -> datetime | None:
    """Costruisce una data da un match numerico (g, m, a) o esteso (g, mese, a)."""
    try:
        giorno = int(m.group(1))
        mese_grezzo = m.group(2)
        mese = _MESI[mese_grezzo.lower()] if mese_grezzo.isalpha() else int(mese_grezzo)
        return datetime(_anno_completo(int(m.group(3))), mese, giorno)
    except (ValueError, KeyError):
        return None


def formatta_data(data: datetime | None) -> str:
    return data.strftime("%d/%m/%Y") if data else MANCANTE


def estrai_classe_e_disciplina(titolo: str, corpo: str) -> tuple[str, str]:
    """Codice della classe di concorso (normalizzato in A-041) e disciplina che lo segue.

    Due famiglie di codici: quelli numerici (A041, B016, con o senza trattino/spazio) e
    quelli delle conversazioni in lingua straniera (BI02 cinese, BA02 francese...), che
    hanno una lettera al posto dello zero. Questi ultimi si accettano solo tutti attaccati
    e in maiuscolo, altrimenti "AL 30" dentro "dal 03/12 AL 30/06" verrebbe letto come codice.
    """
    codice = re.compile(r"\bB[A-Z]\d{2}\b|\b([ABab])\s?-?\s?(\d{2,3})\b")
    for testo in (titolo, corpo):
        m = codice.search(testo)
        if not m:
            continue
        if m.group(1) is None:  # forma BI02: si riporta com'e'
            classe = m.group(0).upper()
        else:
            classe = f"{m.group(1).upper()}-{m.group(2).zfill(3)}"
        coda = testo[m.end():m.end() + 80]
        # La disciplina e' quello che segue il codice fino al primo separatore forte.
        coda = re.split(r"[\n–—|,;]|\s-\s|\bh\s*\d|\bdal\b|\bal\b", coda, maxsplit=1)[0]
        coda = coda.strip(" .:-–—\t")
        disciplina = coda if len(coda) >= 4 else MANCANTE
        return classe, disciplina
    return MANCANTE, MANCANTE


def estrai_ore(titolo: str, corpo: str) -> str:
    """Ore settimanali. Ordine di priorita' dal pattern piu' esplicito al piu' ambiguo."""
    pattern = (
        r"\b(\d{1,2})\s*/\s*18\b",                  # 12/18 (spezzone su cattedra piena)
        r"\bore\s*:?\s*(\d{1,2})\s*settimanal",     # Ore: 18 settimanali
        r"n\.?\s*ore\s*:?\s*(\d{1,2})\b",           # n. ore 6
        r"\bore\s*:\s*(\d{1,2})\b",                 # Ore: 18
        r"\b(\d{1,2})\s*ore\b",                     # 18 ORE / 7 ore
        r"\bh\.?\s*(\d{1,2})\b",                    # h 18
    )
    for testo in (titolo, corpo):
        for p in pattern:
            m = re.search(p, testo, re.IGNORECASE)
            if m:
                return m.group(0).strip() if "/" in m.group(0) else m.group(1)
    return MANCANTE


def estrai_periodo(titolo: str, corpo: str) -> tuple[str, str]:
    """Date di inizio e fine supplenza."""
    for testo in (corpo, titolo):
        m = re.search(rf"\bdal\s+{_DATA}\s+al\s+{_DATA}", testo, re.IGNORECASE)
        if m:
            try:
                inizio = datetime(_anno_completo(int(m.group(3))), int(m.group(2)), int(m.group(1)))
                fine = datetime(_anno_completo(int(m.group(6))), int(m.group(5)), int(m.group(4)))
            except ValueError:
                continue
            return formatta_data(inizio), formatta_data(fine)
    # Caso frequente nei titoli: solo la fine ("al 30.06.27", "supplenza al 05/06/2026"),
    # oppure nel corpo la riga "Durata: 30/06/2027".
    for testo in (titolo, corpo):
        for p in (rf"\bal\s+{_DATA}", rf"\bdurata\s*:?\s*(?:fino\s+al\s+)?{_DATA}"):
            m = re.search(p, testo, re.IGNORECASE)
            if m:
                fine = _data_da_match(m)
                if fine:
                    return MANCANTE, formatta_data(fine)
    return MANCANTE, MANCANTE


def estrai_scadenza(corpo: str, fuso: str = "Europe/Rome") -> tuple[datetime | None, str]:
    """Scadenza della candidatura: il campo piu' importante del messaggio.

    Restituisce (datetime con fuso italiano, testo originale trovato). Se nel testo c'e'
    anche l'ora la si tiene, altrimenti si assume la fine della giornata (23:59): meglio
    sbagliare per eccesso che dichiarare scaduto un interpello ancora valido.
    """
    tz = ZoneInfo(fuso)
    pattern = (
        # entro le ore 12.00 del 02/12/2025 / entro e non oltre le ore 10:00 del 18-09-2026
        rf"entro\s+(?:e\s+non\s+oltre\s+)?le\s+ore\s+(\d{{1,2}})[.:](\d{{2}})\s*(?:del|di)?\s*{_DATA}",
        rf"entro\s+(?:e\s+non\s+oltre\s+)?le\s+ore\s+(\d{{1,2}})\s*(?:del|di)\s*{_DATA}",
        rf"entro\s+(?:e\s+non\s+oltre\s+)?(?:il|le|la)?\s*{_DATA}",
        rf"entro\s+(?:e\s+non\s+oltre\s+)?(?:il|le|la)?\s*{_DATA_ESTESA}",
        rf"scadenza\s*:?\s*{_DATA}",
    )
    for i, p in enumerate(pattern):
        m = re.search(p, corpo, re.IGNORECASE)
        if not m:
            continue
        g = m.groups()
        if i == 0:
            ora, minuto = int(g[0]), int(g[1])
            giorno, mese, anno = int(g[2]), int(g[3]), int(g[4])
        elif i == 1:
            ora, minuto = int(g[0]), 0
            giorno, mese, anno = int(g[1]), int(g[2]), int(g[3])
        elif i == 3:
            ora, minuto = 23, 59
            giorno, mese, anno = int(g[0]), _MESI[g[1].lower()], int(g[2])
        else:
            ora, minuto = 23, 59
            giorno, mese, anno = int(g[0]), int(g[1]), int(g[2])
        try:
            data = datetime(_anno_completo(anno), mese, giorno, ora, minuto, tzinfo=tz)
        except ValueError:
            continue
        return data, _SPAZI.sub(" ", m.group(0)).strip()
    return None, MANCANTE


_ISTITUTO = re.compile(
    r"\b(?:I\.?I\.?S\.?S?\.?|I\.?T\.?C\.?G?\.?|I\.?T\.?E\.?S?\.?|I\.?T\.?I\.?S?\.?|"
    r"I\.?P\.?S?\.?[A-Z]{0,3}\.?|I\.?C\.?|I\.?S\.?|Liceo|Istituto|Convitto)"
    r"\s+[\"“']?[A-Z][\w'’.”\" -]{2,45}"
)


def estrai_scuola(titolo: str, corpo: str) -> str:
    """Istituto di servizio: prima dal corpo (piu' preciso), poi dalla coda del titolo."""
    m = _ISTITUTO.search(corpo)
    if m:
        return _pulisci_scuola(m.group(0))
    # Alcune segreterie scrivono una riga "Sede di servizio: ..." invece del nome istituto.
    m = re.search(r"sede\s+(?:di\s+servizio|di\s+prestazione)?\s*:\s*(.+)", corpo, re.IGNORECASE)
    if m:
        return _pulisci_scuola(m.group(1)[:60])
    # Nei titoli la scuola sta quasi sempre in coda, dopo l'ultimo trattino.
    pezzi = [p.strip() for p in re.split(r"–|—|\s-\s|(?<=\d)-|-(?=[A-Z])", titolo) if p.strip()]
    if pezzi:
        coda = pezzi[-1]
        # Scarta code che sono solo ore o date ("h 18", "al 30.06.27") e code troppo lunghe,
        # che sono titoli interi e non nomi di scuola.
        if not re.fullmatch(r"[\d\s/.:hore]{1,12}", coda, re.IGNORECASE) and len(coda) <= 60:
            m = _ISTITUTO.search(coda)
            return _pulisci_scuola(m.group(0) if m else coda)
    return MANCANTE


def _pulisci_scuola(valore: str) -> str:
    valore = _SPAZI.sub(" ", valore).strip(" .,;:-–—")
    return valore if valore else MANCANTE


def estrai_email(html_grezzo: str, corpo: str) -> str:
    """Indirizzo a cui candidarsi: prima i mailto:, poi le email nel testo (PEC in priorita')."""
    mailto = re.findall(r"mailto:([^\"'?>\s]+)", html_grezzo or "", re.IGNORECASE)
    trovate = [e.strip().rstrip(".,;") for e in mailto]
    if not trovate:
        trovate = re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", corpo or "")
    trovate = [e for e in trovate if "@" in e]
    if not trovate:
        return MANCANTE
    for e in trovate:
        if "pec" in e.lower():
            return e
    return trovate[0]


def estrai_pdf(html_grezzo: str) -> list[str]:
    """Link agli allegati.

    Priorita' al blocco <ul class="post-attachments"> (Forli-Cesena, Modena, Rimini);
    Ravenna e Bologna a volte non lo usano e mettono i PDF in una lista normale,
    quindi il fallback prende qualunque href che finisce in .pdf.
    """
    if not html_grezzo:
        return []
    blocco = re.search(
        r"<ul[^>]*class=\"[^\"]*post-attachments[^\"]*\"[^>]*>(.*?)</ul>",
        html_grezzo,
        re.IGNORECASE | re.DOTALL,
    )
    ambito = blocco.group(1) if blocco else html_grezzo
    link = re.findall(r"href=\"([^\"]+)\"", ambito, re.IGNORECASE)
    pdf = [l for l in link if l.lower().split("?")[0].endswith(".pdf")]
    if not pdf and blocco:
        pdf = link
    # Deduplica mantenendo l'ordine.
    visti: list[str] = []
    for l in pdf:
        if l not in visti:
            visti.append(l)
    return visti


def costruisci_interpello(
    provincia: str,
    post_id: str,
    titolo_html: str,
    contenuto_html: str,
    link: str,
    provincia_nome: str = "",
    pubblicato: datetime | None = None,
    fuso: str = "Europe/Rome",
    motivo_filtro: str = "",
) -> Interpello:
    """Mette insieme tutti gli estrattori su un singolo post."""
    titolo = testo_piano(titolo_html)
    corpo = testo_piano(contenuto_html)
    classe, disciplina = estrai_classe_e_disciplina(titolo, corpo)
    ore = estrai_ore(titolo, corpo)
    inizio, fine = estrai_periodo(titolo, corpo)
    scadenza, scadenza_testo = estrai_scadenza(corpo + "\n" + titolo, fuso)
    return Interpello(
        provincia=provincia,
        post_id=str(post_id),
        titolo=titolo,
        link=link,
        provincia_nome=provincia_nome or provincia,
        pubblicato=pubblicato,
        classe=classe,
        disciplina=disciplina,
        ore=ore,
        inizio=inizio,
        fine=fine,
        scadenza=scadenza,
        scadenza_testo=scadenza_testo,
        scuola=estrai_scuola(titolo, corpo),
        email=estrai_email(contenuto_html, corpo),
        pdf=estrai_pdf(contenuto_html),
        motivo_filtro=motivo_filtro,
    )


def descrivi_tempo_rimanente(scadenza: datetime | None, adesso: datetime) -> str:
    """'fra 2 giorni e 3 ore' / 'SCADUTO' - sempre calcolato in ora italiana."""
    if scadenza is None:
        return ""
    delta = scadenza - adesso
    if delta <= timedelta(0):
        return "SCADUTO"
    giorni = delta.days
    ore = delta.seconds // 3600
    minuti = (delta.seconds % 3600) // 60
    if giorni:
        return f"fra {giorni} giorn{'o' if giorni == 1 else 'i'} e {ore} or{'a' if ore == 1 else 'e'}"
    if ore:
        return f"fra {ore} or{'a' if ore == 1 else 'e'} e {minuti} min"
    return f"fra {minuti} minuti"
