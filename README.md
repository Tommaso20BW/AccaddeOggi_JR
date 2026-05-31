# 👀🔙 AccaddeOggi JR

> Bot Telegram per la rubrica quotidiana **"Accadde Oggi"** della Juventus — generata con AI e pubblicata automaticamente su GitHub Actions.

---

## 📌 Panoramica

**AccaddeOggi JR** utilizza l'API **Google Gemini** per cercare e raccontare i 3 eventi storici più significativi della Juventus accaduti in questo giorno nel corso degli anni: trofei, partite storiche, grandi rimonte, record. Il testo generato viene formattato in HTML per Telegram e pubblicato sul canale **@Juventus_Reborn**.

---

## 🗂️ Struttura del repository

```
AccaddeOggi_JR/
├── script.py                          # Script principale
└── .github/workflows/
    └── accadde_oggi.yml               # Workflow GitHub Actions
```

---

## ✨ Funzionalità

- **Ricerca storica AI** — Gemini cerca automaticamente gli eventi più iconici della Juventus per la data odierna (trofei, record, big match, rimonte)
- **Filtro automatico** — i compleanni di giocatori e allenatori sono esclusi tassativamente dal prompt; vengono selezionati al massimo 3 eventi
- **Anni in emoji** — tutti gli anni a 4 cifre nel testo vengono convertiti automaticamente in numeri emoji (es. `1️⃣9️⃣8️⃣4️⃣`) per uno stile visivo riconoscibile
- **Formattazione HTML** — il testo segue una struttura precisa con titolo in grassetto (`<b>`) e descrizione in corsivo (`<i>`), ottimizzata per Telegram
- **Gestione giorni vuoti** — se Gemini non trova eventi rilevanti per quella data, l'invio viene annullato automaticamente e non viene pubblicato nulla
- **Retry esponenziale** — in caso di errore 503 (servizio Gemini non disponibile), lo script riprova fino a 5 volte con attesa crescente

---

## 📐 Formato del messaggio

```
👀🔙 ACCADDE OGGI | 15 MAGGIO

ANNO - Titolo Evento
Descrizione breve in corsivo.

ANNO - Secondo Evento
Altra descrizione in corsivo.

👉 @Juventus_Reborn
```

---

## ⚙️ Configurazione dei Secrets

Aggiungi i seguenti secret nelle impostazioni della repository (`Settings → Secrets and variables → Actions`):

| Secret | Descrizione |
|---|---|
| `GEMINI_API_KEY` | Chiave API Google Gemini |
| `TELEGRAM_TOKEN` | Token del bot Telegram |
| `TELEGRAM_CHAT_ID` | Chat ID del canale di destinazione |

---

## 🚀 Utilizzo

1. Fai il **fork** del repository
2. Configura i secret elencati sopra
3. Avvia il workflow manualmente da `Actions → Rubrica Accade Oggi → Run workflow`

> Per automatizzare l'invio quotidiano, modifica il trigger nel file `accadde_oggi.yml` aggiungendo uno schedule cron:
> ```yaml
> on:
>   schedule:
>     - cron: '0 8 * * *'   # ogni giorno alle 08:00 UTC
>   workflow_dispatch:
> ```

---

## 🛠️ Stack tecnico

`Python 3.10` · `google-genai` · `urllib` · `GitHub Actions`

---

## 🤖 Modello AI

[Google Gemini](https://ai.google.dev/) — modello `gemini-3.5-flash` con temperatura `0.1` per massimizzare la precisione storica.

---

*Progetto amatoriale. Non affiliato con la Juventus FC, Telegram o Google.*
