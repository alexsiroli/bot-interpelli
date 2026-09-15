# Bot Telegram – monitoraggio interpelli USR Emilia-Romagna (informatica)

## Obiettivo

Creare un repository GitHub che, tramite GitHub Actions su cron, controlli i feed RSS
degli Uffici Scolastici Territoriali dell'Emilia-Romagna, individui i nuovi **interpelli**
per le classi di concorso di **informatica**, e mi mandi su **Telegram** un messaggio
riassuntivo con scadenza, ore, sede e link al PDF.

Vincoli: costo zero, nessun server da mantenere, nessun doppione, nessun falso silenzio.

---

## 1. Fonti dati

Sono tutti siti WordPress dello stesso multisito (`*.istruzioneer.gov.it`), quindi
espongono feed RSS e con ogni probabilità anche le REST API di WordPress.

Province da monitorare:

| Provincia | Sito |
|---|---|
| Forlì-Cesena | `https://fc.istruzioneer.gov.it` |
| Rimini | `https://rn.istruzioneer.gov.it` |
| Ravenna | `https://ra.istruzioneer.gov.it` |
| Bologna | `https://bo.istruzioneer.gov.it` |
| Modena | `https://mo.istruzioneer.gov.it` |

**Prima di scrivere il codice definitivo, fai una fase di ricognizione** su ciascun sito e
riportami in console cosa hai trovato. Per ognuno, in quest'ordine di preferenza:

1. **REST API WordPress** (preferita, restituisce JSON pulito e più di 10 elementi):
   - `GET /wp-json/wp/v2/categories?search=interpelli` → prendi l'`id` della categoria
     dell'anno scolastico corrente (es. "Interpelli a.s. 2026/27")
   - `GET /wp-json/wp/v2/posts?categories=<id>&per_page=20&_fields=id,date,link,title,content`
2. **Feed RSS di categoria** (fallback): la pagina di categoria HTML contiene un
   `<link rel="alternate" type="application/rss+xml">` con l'URL esatto del feed — usalo,
   non costruirlo a mano, perché lo slug cambia da sito a sito.
   Per Forlì-Cesena dovrebbe essere
   `https://fc.istruzioneer.gov.it/category/interpelli-a-s-2026-27/feed/`.
3. **Feed generale del sito** (ultima spiaggia): `https://<sito>/feed/`, filtrando gli item
   sull'elemento `<category>` che contiene "Interpell". Attenzione: tiene solo gli ultimi
   10 post e il sito ne pubblica ~50 al mese, quindi con questo fallback la frequenza di
   controllo deve essere alta (≤ 2 ore).

Nota utile: sia il feed che le REST API restituiscono il **testo integrale** del post
(`content:encoded` / `content.rendered`), inclusi i link agli allegati PDF. Non serve
scaricare né fare parsing dei PDF, e non serve scraping HTML delle pagine.

La lista dei siti e le relative modalità di accesso vanno in un file di configurazione
separato (`config.yaml` o `config.py`), così posso aggiungere province senza toccare la logica.

## 2. Filtro classi di concorso

Deve passare un post se il testo (titolo + contenuto, normalizzato) contiene almeno uno di:

- codice **A-041** nelle varianti: `A041`, `A-041`, `A 041`, `A41`, `A-41`
- codice **B-016** nelle varianti: `B016`, `B-016`, `B 016`, `B16`, `B-16`
- le stringhe (case-insensitive, senza accenti): `informatic` (copre informatica/informatiche/informatico),
  `sistemi e reti`, `tecnologie informatiche`

Usa regex con `\b` ai bordi per evitare che `A041` matchi dentro altri numeri, e assicurati
che **non** ci siano falsi positivi con A-040, A-042, A-034 (classi vicine che compaiono
spesso sugli stessi siti). Scrivi un test con casi reali presi dai feed, inclusi almeno un
A042 e un A044 che devono essere **scartati**.

Il filtro va in configurazione, non hardcoded, con un commento che spiega cosa sono i codici.

## 3. Deduplica (requisito critico)

- Stato persistente in `state/seen.json`: mappa `{ "<provincia>:<guid o post id>": "<ISO timestamp>" }`.
- Il workflow committa il file aggiornato nel repo a fine run (`permissions: contents: write`,
  commit con `github-actions[bot]`, `[skip ci]` nel messaggio).
- **Primo avvio**: modalità `--backfill` che marca come già visti tutti i post esistenti
  **senza inviare niente**, altrimenti al primo run mi arriva tutto lo storico.
  Deve essere un comando esplicito da lanciare a mano una volta sola.
- Fai pulizia delle voci più vecchie di 12 mesi per non far crescere il file all'infinito.

## 4. Messaggi Telegram

API: `POST https://api.telegram.org/bot<TOKEN>/sendMessage`, `parse_mode=HTML`,
`disable_web_page_preview=true`. Fai l'escape di `&`, `<`, `>` nel testo estratto.
Limite 4096 caratteri: se un messaggio sfora, spezzalo; se ci sono più interpelli nuovi,
manda **un messaggio per interpello** (sono rari, meglio leggibili separati).

Dal testo del post estrai con regex, e se non trovi un campo scrivi "—" invece di inventarlo:

- classe di concorso e disciplina
- ore settimanali (pattern tipo `n. ore 6`, `h 18`, `ore 18 settimanali`, `12/18`)
- date di inizio e fine supplenza
- **scadenza candidatura** (pattern tipo `entro le ore 10:00 del 18/09/2026`,
  `entro il ...`) — è il campo più importante
- scuola / sede di servizio
- indirizzo PEC a cui candidarsi
- link al PDF allegato (i link dentro `<ul class="post-attachments">`)

Formato del messaggio:

```
🔔 Nuovo interpello — <provincia>
<classe di concorso> · <disciplina>
🏫 <scuola>
⏱ <ore> ore · dal <inizio> al <fine>
⚠️ Scadenza: <scadenza>   (<quanti giorni/ore mancano>)
📎 <link PDF>
🔗 <link al post>
```

Il conteggio del tempo rimanente va calcolato in `Europe/Rome` con `zoneinfo`.
Se la scadenza è già passata al momento dell'invio, il messaggio lo dice esplicitamente.

**Digest del mattino**: oltre agli avvisi immediati, una volta al giorno al primo run
dopo le 7:00 ora italiana manda un riepilogo degli interpelli **ancora aperti** (scadenza
non passata) già segnalati nei giorni precedenti. Se non c'è nulla di aperto e nulla di
nuovo, non mandare niente: niente messaggi "nessuna novità".

## 5. Schedulazione

- Workflow `.github/workflows/interpelli.yml` con `schedule` **e** `workflow_dispatch`.
- Cron ogni 2-3 ore nella fascia diurna italiana. Ricorda che **il cron di GitHub Actions è in UTC**:
  scrivi gli orari in UTC e metti un commento con l'equivalente italiano, segnalando che
  d'inverno (CET) slittano di un'ora. Esempio: `0 5,8,11,14,17 * * *` UTC = 7/10/13/16/19 CEST.
- GitHub può ritardare i job schedulati di parecchi minuti negli orari di punta: è accettabile,
  ma non fare logica che dipenda dall'orario esatto di esecuzione.
- Attenzione: GitHub **disattiva i workflow schedulati dopo 60 giorni di inattività del repo**.
  Verifica se i commit automatici del bot bastano a evitarlo; se non è chiaro, aggiungi al
  README una nota con la mitigazione (push manuale periodico, oppure commit fatti con un PAT
  invece che con `GITHUB_TOKEN`).

## 6. Robustezza

- Se un sito è irraggiungibile o risponde male, **non far fallire tutto il run**: logga,
  salta quella provincia, continua con le altre. Lo stato di quella provincia resta invariato
  così al giro dopo recupera.
- Timeout HTTP espliciti (10s) e un retry con backoff.
- `User-Agent` identificabile e non aggressivo; un solo giro di richieste per run.
- Se l'invio a Telegram fallisce, **non** marcare il post come già visto (altrimenti lo perdo
  per sempre). Salva lo stato solo dopo invio riuscito.
- Se la struttura del sito cambia e un feed non restituisce più nulla per N run consecutivi,
  mandami un messaggio di allerta: il fallimento silenzioso è il rischio peggiore qui.

## 7. Segreti e configurazione

Secrets del repo: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
Il codice li legge da variabili d'ambiente; in locale da un `.env` che va in `.gitignore`.
Mai loggare il token.

## 8. Struttura del progetto

```
.
├── .github/workflows/interpelli.yml
├── config.yaml              # province, feed, regex classi di concorso
├── interpelli/
│   ├── __init__.py
│   ├── fetch.py             # REST API / RSS, con fallback
│   ├── parse.py             # estrazione campi dal testo del post
│   ├── filters.py           # match classi di concorso
│   ├── telegram.py          # invio messaggi
│   └── main.py              # orchestrazione, CLI
├── state/seen.json
├── tests/                   # pytest, con fixture di feed reali salvati
├── requirements.txt         # minimale: requests, feedparser, pyyaml
└── README.md
```

CLI richiesta:

- `python -m interpelli.main --check` — run normale
- `python -m interpelli.main --dry-run` — stampa a video invece di inviare
- `python -m interpelli.main --backfill` — marca tutto come visto, non invia
- `python -m interpelli.main --test-telegram` — manda un messaggio di prova
- `python -m interpelli.main --discover` — la ricognizione del punto 1, stampa per ogni sito
  quale metodo funziona e quale URL usare

## 9. Come procedere

1. Fai prima la ricognizione (`--discover` a mano, anche con `curl`) e **mostrami i risultati**
   prima di scrivere il resto: voglio sapere quali province hanno le REST API attive e qual è
   lo slug esatto della categoria interpelli su ciascuna.
2. Salva 2-3 risposte reali dei feed come fixture nei test, incluso l'interpello BI02 del
   15/09/2026 di Forlì-Cesena (deve essere scartato) e almeno un interpello informatica se
   ne trovi uno nello storico delle categorie degli anni precedenti
   (`Interpelli a.s. 2025/26` è ricca, 338 post).
3. Scrivi codice e test, fai girare i test.
4. Verifica end-to-end con `--dry-run` e poi `--test-telegram`.
5. README con: setup in 5 passi, come ottenere token e chat ID da @BotFather e
   `getUpdates`, come aggiungere una provincia, come cambiare le classi di concorso.

Codice commentato in italiano, messaggi di log chiari. Python 3.11+.
