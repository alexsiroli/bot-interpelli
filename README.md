# bot-interpelli

Bot Telegram che controlla ogni 2-3 ore i siti degli Uffici Scolastici Territoriali
dell'Emilia-Romagna e avvisa quando esce un **interpello per le classi di concorso di
informatica** (A-041 e B-016), con scadenza, ore, sede e link al PDF.

Gira su GitHub Actions: nessun server, nessun costo, nessun database (lo stato e' un file
JSON committato nel repository).

```
🔔 Nuovo interpello — Forli-Cesena
A-041 · Scienze e tecnologie informatiche
🏫 I.P. Ruffilli
⏱ 18 ore · dal 03/12/2025 al 13/12/2025
⚠️ Scadenza: 02/12/2025 alle 12:00 (fra 1 giorno e 4 ore)
✉️ forf040008@istruzione.it
📎 https://fc.istruzioneer.gov.it/.../ruffilli.pdf
🔗 https://fc.istruzioneer.gov.it/2025/12/01/interpello-...
```

## Setup in 5 passi

1. **Crea il bot.** Su Telegram scrivi a [@BotFather](https://t.me/BotFather), manda
   `/newbot`, scegli nome e username: ti risponde con un token tipo
   `123456789:AAE...`. Quello e' il `TELEGRAM_BOT_TOKEN`.
2. **Trova la tua chat id.** Apri una chat con il bot appena creato e mandagli un
   messaggio qualsiasi (deve iniziare la conversazione tu, altrimenti non puo' scriverti).
   Poi apri nel browser `https://api.telegram.org/bot<TOKEN>/getUpdates` e cerca
   `"chat":{"id":123456789`. Quel numero e' il `TELEGRAM_CHAT_ID`.
3. **Metti i due secret nel repository:**
   ```bash
   gh secret set TELEGRAM_BOT_TOKEN --body "123456789:AAE..."
   gh secret set TELEGRAM_CHAT_ID  --body "123456789"
   ```
   (oppure a mano: Settings → Secrets and variables → Actions → New repository secret)
4. **Verifica che il bot ti scriva:**
   ```bash
   gh workflow run "Controllo interpelli"   # oppure, in locale, con il .env:
   python -m interpelli.main --test-telegram
   ```
5. **Fatto.** Il workflow gira da solo alle 7, 10, 13, 16 e 19 (ora italiana legale).

> Il primo allineamento (`--backfill`) e' gia' stato fatto: gli interpelli pubblicati
> prima dell'installazione sono marcati come visti e non verranno rimandati. Se cambi
> le classi di concorso in `config.yaml` e vuoi ripartire pulito, cancella
> `state/seen.json` e rilancia `--backfill`.

## Comandi

```bash
python -m interpelli.main --check           # run normale: cerca e invia
python -m interpelli.main --dry-run         # stampa a video, non invia, non salva lo stato
python -m interpelli.main --backfill        # marca tutto come visto senza inviare (una volta sola)
python -m interpelli.main --test-telegram   # messaggio di prova
python -m interpelli.main --discover        # ricognizione: che metodo e che URL usa ogni sito
python -m interpelli.main --check --provincia modena -v   # debug su una sola provincia
```

In locale servono le dipendenze (`pip install -r requirements-dev.txt`) e un file `.env`
copiato da `.env.example`. Il `.env` e' in `.gitignore` e il token non viene mai loggato.

## Come funziona

1. **Fonti.** Per ogni provincia si prova, in ordine: REST API di WordPress → feed RSS
   della categoria (l'URL si legge dal `<link rel="alternate">` della pagina, non si
   costruisce a mano) → feed RSS generale del sito filtrato sulla categoria.
2. **Categoria.** L'id non e' scritto da nessuna parte nel codice: a ogni run si cercano
   le categorie che contengono "interpell" e si prende quella dell'anno scolastico
   corrente. Se non esiste ancora, si ripiega sulla piu' recente. Cosi' a settembre, quando
   gli USR creano la categoria del nuovo anno, il bot ci si sposta da solo.
3. **Filtro.** Il testo viene normalizzato (niente tag, niente entita' HTML, niente
   accenti, tutto minuscolo) e confrontato con le regex di `config.yaml`.
4. **Deduplica.** `state/seen.json` tiene le chiavi `provincia:id-post`. Una chiave si
   scrive **solo dopo un invio riuscito**: se Telegram e' irraggiungibile l'interpello
   viene riprovato al giro dopo invece di essere perso.
5. **Digest.** Al primo run dopo le 7:00 arriva il riepilogo degli interpelli ancora
   aperti gia' segnalati nei giorni precedenti. Se non c'e' niente di aperto e niente di
   nuovo, il bot tace: nessun messaggio "nessuna novita'".
6. **Allerta.** Se una provincia non restituisce piu' nessun post per 3 run consecutivi
   arriva un avviso: il fallimento silenzioso (sito cambiato, categoria rinominata) e' il
   rischio peggiore per un bot come questo.

### Stato della ricognizione (15/09/2026)

Tutte e cinque le province hanno le REST API attive, quindi i fallback RSS non vengono
mai usati oggi. Rilanciare `--discover` quando qualcosa smette di funzionare.

| Provincia | Sito | Categoria usata | Note |
|---|---|---|---|
| Forli-Cesena | `fc.istruzioneer.gov.it` | 720 `Interpelli a.s. 2026/27` | post con testo completo |
| Rimini | `rn.istruzioneer.gov.it` | 452 `Interpelli docenti 2026/2027` | feed annidato sotto `/category/personale-docente/` |
| Ravenna | `ra.istruzioneer.gov.it` | 204 `Interpelli` | categoria unica senza anno, contiene anche ATA/DSGA |
| Bologna | `bo.istruzioneer.gov.it` | 1156 `Interpelli personale docente 2025/26` | la categoria 2026/27 non esiste ancora |
| Modena | `mo.istruzioneer.gov.it` | 2470 `Interpelli personale docente 2026/27` | provincia piu' prolifica (~70 post/mese) |

Molti post di Modena, Ravenna e Bologna contengono solo il link al PDF: in quei casi i
campi che il bot non trova nel testo restano `—`, e il PDF va aperto a mano. Il bot non
scarica ne' analizza i PDF.

## Personalizzazione

**Aggiungere una provincia** — basta una voce in `config.yaml`, il codice non si tocca:

```yaml
province:
  - nome: "Ferrara"
    base_url: "https://fe.istruzioneer.gov.it"
    attiva: true
    # opzionale: regex sul nome della categoria, se la scelta automatica sbaglia
    # categoria_preferita: "Interpelli docenti"
```

Poi `python -m interpelli.main --discover --provincia ferrara` per controllare che il sito
risponda, e `--backfill` per non farsi arrivare tutto lo storico.

**Cambiare le classi di concorso** — sempre in `config.yaml`, blocco
`classi_di_concorso`: `codici` sono regex (le varianti A041/A-041/A 041/A41 sono gia'
coperte da `\ba[-\s]?0?41\b`), `parole` sono sottostringhe cercate nel testo normalizzato,
`esclusioni` scarta un post anche se ha fatto match.

Nota sul rumore: la parola `informatic` fa passare anche qualche post di **A-066**
(Trattamento testi e dati), che cita l'informatica pur essendo un'altra classe. Se da'
fastidio, aggiungi `'\ba[-\s]?0?66\b'` alle `esclusioni`.

## Manutenzione

- **Workflow schedulati disattivati dopo 60 giorni.** GitHub disattiva i cron dei
  repository inattivi, e i commit fatti con il `GITHUB_TOKEN` di Actions **non** contano
  come attivita'. Il bot commita `state/seen.json` a ogni novita', ma con il token di
  default questo potrebbe non bastare. Due mitigazioni, in ordine di comodita':
  1. creare un PAT (fine-grained, permesso *Contents: read and write* su questo
     repository) e salvarlo nel secret `STATE_PUSH_TOKEN`: il workflow lo usa in automatico
     al posto del `GITHUB_TOKEN` e i suoi commit contano come attivita';
  2. oppure, quando arriva la mail di preavviso di GitHub, lanciare a mano
     `gh workflow run "Controllo interpelli"` o fare un commit qualsiasi.
- **Consumo Actions.** Circa 5 run al giorno da ~40 secondi: ~150 minuti al mese, dentro i
  2000 gratuiti dei repository privati.
- **Test.** `python -m pytest` (108 test). Le fixture in `tests/fixtures/` sono risposte
  vere dei siti scaricate il 15/09/2026, compresi i casi che devono essere **scartati**
  (A-042, A-044, BI02, graduatoria DSGA) e due interpelli A-041 veri.

## Struttura

```
config.yaml                     province, classi di concorso, parametri
interpelli/
  config.py                     caricamento config e segreti
  fetch.py                      REST API, feed di categoria, feed generale
  filters.py                    normalizzazione del testo e match delle classi
  parse.py                      estrazione di scadenza, ore, date, scuola, PEC, PDF
  state.py                      seen.json: deduplica, ritenzione, digest, allerte
  telegram.py                   composizione e invio dei messaggi
  main.py                       CLI e orchestrazione
state/seen.json                 memoria del bot, committata dal workflow
tests/                          pytest + fixture di post reali
.github/workflows/              interpelli.yml (cron) e ci.yml (test)
SPEC-bot-interpelli.md          la specifica di partenza
```

`state/seen.json` contiene, oltre alle chiavi viste, gli interpelli ancora aperti (per il
digest) e i contatori usati per l'allerta: e' un'estensione del formato descritto nella
specifica, che prevedeva la sola mappa `chiave -> timestamp`.
