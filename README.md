<div align="center">

# 👀🔙 AccaddeOggi JR

**Rubrica Telegram sugli eventi più importanti accaduti nella storia della Juventus in questo giorno.**

[![Python 3.14](https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![GitHub Actions](https://github.com/Tommaso20BW/AccaddeOggi_JR/actions/workflows/accadde_oggi.yml/badge.svg)](https://github.com/Tommaso20BW/AccaddeOggi_JR/actions/workflows/accadde_oggi.yml)

</div>

## Panoramica

AccaddeOggi JR combina ricerca web, verifica con Gemini e regole deterministiche per creare una rubrica quotidiana dedicata alla prima squadra maschile della Juventus.

```text
Serper: ricerca iniziale
        ↓
Gemini: selezione dei candidati
        ↓
Serper: ricerca mirata di conferma
        ↓
Gemini: fact-check e valutazione
        ↓
controlli deterministici e storico
        ↓
Telegram
```

Il bot pubblica al massimo tre eventi e non invia nulla quando non trova episodi abbastanza importanti, verificati e nuovi.

## Criterio editoriale

Sono ammesse soltanto queste categorie:

- trofei ufficiali e Scudetti conquistati;
- partite iconiche o memorabili vinte;
- record storici positivi;
- debutti davvero iconici;
- traguardi individuali eccezionali;
- acquisti ufficiali realmente epocali o iconici.

Ogni evento deve:

1. corrispondere esattamente al giorno e al mese correnti;
2. riguardare la prima squadra maschile;
3. avere fonti verificabili;
4. raggiungere un'importanza di almeno `8/10`;
5. avere un esito coerente con la categoria.

Gli acquisti passano soltanto nella data dell'annuncio ufficiale della Juventus. Visite mediche, arrivi in aeroporto, indiscrezioni e semplici presentazioni vengono scartati.

Se esistono più di tre eventi validi, il codice ordina prima i `10`, poi i `9` e infine gli `8`.

## Ricerca e verifica

`script.py` esegue due passaggi distinti:

- **scoperta**: Serper raccoglie risultati per la data e Gemini costruisce i candidati;
- **fact-check**: nuove query Serper cercano conferme mirate e Gemini restituisce eventi strutturati con fonti ed esito.

La catena Gemini predefinita è:

1. `gemini-3.6-flash`;
2. `gemini-2.5-flash`;
3. `gemini-3.5-flash-lite`.

Su limiti `429`, indisponibilità o errori temporanei il bot prova il modello successivo e può ripetere l'intera catena fino a tre cicli. Un modello non più disponibile o con quota giornaliera esaurita viene escluso dai tentativi seguenti.

## Duplicati e archivi

| File | Ruolo |
| --- | --- |
| `data/eventi_pubblicati.json` | Eventi già inviati con data di pubblicazione |
| `data/eventi_scartati.json` | Candidati respinti, fase e motivo dello scarto |

Lo stesso evento non viene ripubblicato nello stesso anno, anche quando titolo o identificativo cambiano leggermente. Torna pubblicabile nell'anno successivo.

L'archivio degli scarti registra:

- `recorded_at`: istante della registrazione;
- `phase`: fact-check, validazione, storico o deduplicazione;
- `reason`: motivo preciso;
- `event`: dati ricevuti per il candidato.

Le scritture usano file temporanei e sostituzione finale, riducendo il rischio di JSON incompleti.

## Messaggio Telegram

La rubrica usa HTML Telegram e contiene:

- data italiana in maiuscolo;
- anno composto con emoji numeriche;
- titolo dell'evento in grassetto;
- descrizione in corsivo;
- firma `@Juventus_Reborn`.

Lo storico degli eventi pubblicati viene aggiornato soltanto dopo la conferma dell'invio Telegram.

## Struttura

```text
AccaddeOggi_JR/
├── script.py
├── requirements.txt
├── data/
│   ├── eventi_pubblicati.json
│   └── eventi_scartati.json
├── tests/
│   └── test_script.py
└── .github/workflows/
    └── accadde_oggi.yml
```

## Requisiti

- Python 3.14, come nel workflow GitHub Actions;
- accesso a Gemini, Serper e Telegram.

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## Configurazione

Configura in **Settings → Secrets and variables → Actions**:

| Secret | Uso |
| --- | --- |
| `GEMINI_API_KEY` | Analisi e verifica con Gemini |
| `SERPER_API_KEY` | Ricerca web tramite Serper |
| `TELEGRAM_TOKEN` | Token del bot Telegram |
| `TELEGRAM_CHAT_ID` | Chat o canale di destinazione |

Impostazioni opzionali:

| Variabile | Default | Effetto |
| --- | ---: | --- |
| `GEMINI_MODELS` | catena predefinita | Lista di modelli separati da virgola |
| `GEMINI_MODEL` | vuota | Modello legacy aggiunto in testa alla catena |
| `MAX_CICLI_GEMINI` | `3` | Numero massimo di cicli sui modelli |
| `ATTESA_503_GEMINI` | `20` | Attesa iniziale tra i cicli |
| `SERPER_NUM_RISULTATI` | `10` | Risultati per ricerca, limitati tra 5 e 20 |
| `SERPER_TIMEOUT` | `20` | Timeout Serper in secondi, minimo 5 |
| `EVENT_HISTORY_FILE` | `data/eventi_pubblicati.json` | Percorso alternativo dello storico |
| `REJECTED_EVENTS_FILE` | `data/eventi_scartati.json` | Percorso alternativo degli scarti |

## Avvio locale

### Linux e macOS

```bash
export GEMINI_API_KEY="..."
export SERPER_API_KEY="..."
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python script.py
```

### PowerShell

```powershell
$env:GEMINI_API_KEY = "..."
$env:SERPER_API_KEY = "..."
$env:TELEGRAM_TOKEN = "..."
$env:TELEGRAM_CHAT_ID = "..."
python script.py
```

Se il processo parte nei 15 minuti precedenti le **07:30 Europe/Rome**, attende l'orario esatto; se parte prima, dopo o con un ritardo maggiore, procede subito.

## Test

```bash
python -m unittest discover -s tests -v
```

I test coprono fallback Gemini, ricerca, validazione, formattazione, deduplicazione e persistenza.

## GitHub Actions

Il workflow `.github/workflows/accadde_oggi.yml`:

- è avviabile manualmente con `workflow_dispatch`;
- usa Python 3.14;
- impedisce esecuzioni sovrapposte sullo stesso branch;
- esegue i test prima del bot;
- committa storico e scarti soltanto dopo un'esecuzione riuscita e quando cambiano;
- elimina i propri run completati dalla cronologia.

Nel repository non è configurato uno `schedule`. Errori previsti di quota o indisponibilità esterna terminano senza invio e senza modificare lo storico, lasciando il tentativo al run seguente.

## Limiti noti

- La qualità del risultato dipende dalle pagine restituite da Serper e dalla verifica dei modelli.
- La soglia editoriale è intenzionalmente severa e può produrre giornate senza messaggio.
- Fonti storiche incomplete o date ambigue possono causare lo scarto di un evento corretto.
- I controlli automatici riducono, ma non eliminano, il rischio di errori generativi.

---

Progetto amatoriale, non affiliato con Juventus Football Club, Telegram, Google o Serper.
