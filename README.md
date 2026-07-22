# 👀🔙 AccaddeOggi JR

Bot Telegram che prepara la rubrica quotidiana **“Accadde Oggi”** dedicata alla prima squadra maschile della Juventus.

Il bot usa Google Gemini con Google Search grounding per cercare e verificare gli eventi avvenuti nella data corrente, seleziona fino a tre risultati positivi e li pubblica su Telegram in formato HTML.

## Cosa fa davvero

1. Se il processo parte nei 15 minuti precedenti le **07:30 (Europe/Rome)**, attende l’orario esatto; se parte prima, dopo o con un ritardo maggiore procede subito.
2. Chiede a `gemini-2.5-flash` eventi Juventus avvenuti nello stesso giorno e verificati tramite ricerca Google.
3. Esclude compleanni, sconfitte, eliminazioni, Juventus Women, Next Gen, Primavera e settore giovanile.
4. Mantiene al massimo tre eventi, ordinati dal più vecchio al più recente.
5. Converte gli anni a quattro cifre in emoji numeriche e prepara il messaggio HTML.
6. Se Gemini restituisce `VUOTO`, termina senza pubblicare.
7. Altrimenti invia il messaggio tramite Telegram Bot API.

Per gli errori `503`/`UNAVAILABLE` di Gemini sono previsti fino a cinque tentativi con attesa esponenziale.

## Esempio di output

```text
👀🔙 ACCADDE OGGI | 15 MAGGIO

1️⃣9️⃣8️⃣4️⃣ - Titolo sintetico
Descrizione breve dell’evento.

👉 @Juventus_Reborn
```

Il messaggio reale usa `<b>` e `<i>` con `parse_mode=HTML`.

## Automazione GitHub Actions

Il workflow [`.github/workflows/accadde_oggi.yml`](.github/workflows/accadde_oggi.yml):

- è avviabile **solo manualmente** con `workflow_dispatch`;
- usa Python 3.12;
- installa l’ultima versione di `google-genai`;
- imposta `TZ=Europe/Rome`;
- esegue `python script.py`.

Non è presente uno `schedule` nel repository. Un servizio esterno può avviare il workflow poco prima delle 07:30; l’attesa interna copre soltanto una finestra massima di 15 minuti.

## Configurazione

Configura questi secret in **Settings → Secrets and variables → Actions**:

| Secret | Obbligatorio | Uso |
|---|---:|---|
| `GEMINI_API_KEY` | sì | Accesso a Google Gemini. |
| `TELEGRAM_TOKEN` | sì | Token del bot Telegram. |
| `TELEGRAM_CHAT_ID` | sì | Chat o canale di destinazione. |

## Avvio

### Da GitHub

Apri **Actions → Rubrica Accade Oggi → Run workflow**.

### In locale

Richiede Python 3.10+ (consigliato 3.12):

```bash
python -m pip install --upgrade google-genai
python script.py
```

Prima dell’avvio esporta le tre variabili d’ambiente elencate sopra. Il repository non contiene un `requirements.txt`: il workflow installa direttamente `google-genai`.

## Struttura

```text
AccaddeOggi_JR/
├── script.py
└── .github/workflows/
    └── accadde_oggi.yml
```

## Note operative

- La qualità e la completezza degli eventi dipendono da Gemini e dai risultati disponibili tramite Google Search.
- Il codice richiede eventi verificati, ma non pubblica le fonti nel messaggio Telegram.
- Nel blocco finale gli errori vengono stampati nei log; attualmente non viene forzato un codice di uscita diverso da zero per ogni errore di esecuzione.

---

Progetto amatoriale, non affiliato con Juventus FC, Telegram o Google.
