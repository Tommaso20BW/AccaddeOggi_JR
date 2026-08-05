# 👀🔙 AccaddeOggi JR

Bot Telegram per la rubrica quotidiana “Accadde Oggi” dedicata alla prima
squadra maschile della Juventus.

## Come funziona

1. Serper esegue più ricerche web sulla data del giorno.
2. Gemini analizza i risultati senza usare il Google Search integrato.
3. Serper esegue una seconda ricerca mirata sui candidati.
4. Gemini verifica date, fonti e importanza.
5. Il codice applica controlli deterministici e pubblica al massimo 3 eventi.

## Criterio editoriale

Passano soltanto eventi valutati 8, 9 o 10:

- trofei ufficiali e Scudetti;
- partite iconiche o memorabili;
- record storici positivi;
- debutti davvero iconici;
- traguardi individuali eccezionali;
- acquisti ufficiali realmente epocali o iconici.

Gli acquisti sono ammessi soltanto nella data dell’annuncio ufficiale della
Juventus. Visite mediche, aeroporto, indiscrezioni e presentazioni vengono
scartati.

Se esistono più di tre eventi validi, il codice sceglie prima i 10, poi i 9
e infine gli 8.

## Duplicati

`data/eventi_pubblicati.json` conserva gli eventi inviati. Lo stesso evento
non viene ripubblicato nello stesso anno, ma torna pubblicabile nell’anno
successivo.

## Secret GitHub richiesti

Configura in `Settings → Secrets and variables → Actions`:

| Secret | Uso |
|---|---|
| `GEMINI_API_KEY` | Analisi e verifica con Gemini |
| `SERPER_API_KEY` | Ricerca web tramite Serper |
| `TELEGRAM_TOKEN` | Token del bot Telegram |
| `TELEGRAM_CHAT_ID` | Chat o canale di destinazione |

## Modelli Gemini

Catena predefinita:

1. `gemini-3.6-flash`
2. `gemini-2.5-flash`
3. `gemini-3.5-flash-lite`

Su errori 429 o 503 il bot prova il modello successivo e può ripetere
l’intera catena fino a tre cicli.

## Test

```bash
python -m unittest discover -s tests -v
```

Il workflow esegue i test prima di avviare il bot.

## Avvio locale

```bash
python -m pip install -r requirements.txt
export GEMINI_API_KEY="..."
export SERPER_API_KEY="..."
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
python script.py
```

Progetto amatoriale, non affiliato con Juventus FC, Telegram, Google o Serper.


## Archivio degli eventi scartati

Gli eventi respinti vengono salvati in:

```text
data/eventi_scartati.json
```

Ogni voce contiene:

- `recorded_at`: momento dello scarto;
- `phase`: fase in cui è stato scartato;
- `reason`: motivo preciso;
- `event`: dati ricevuti per l’evento.

Vengono registrati sia gli eventi non validi sia i duplicati nello stesso run
o già pubblicati nell’anno corrente.
