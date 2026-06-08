<div align="center">

# 👀🔙 AccaddeOggi JR

**Bot Telegram per la rubrica quotidiana “Accadde Oggi” della Juventus, generata con AI.**

Ogni giorno racconta gli eventi storici più iconici dei bianconeri accaduti in questa data — pubblicato in automatico su GitHub Actions.

`Python 3.10` · `Google Gemini` · `Telegram Bot API` · `GitHub Actions`

</div>

-----

## Indice

- [Cos’è](#cosè)
- [Come funziona](#come-funziona)
- [Funzionalità](#funzionalità)
- [Formato del messaggio](#formato-del-messaggio)
- [Struttura del repository](#struttura-del-repository)
- [Configurazione](#configurazione)
- [Avvio](#avvio)
- [Stack tecnico](#stack-tecnico)
- [Modello AI](#modello-ai)

-----

## Cos’è

AccaddeOggi JR utilizza l’API **Google Gemini** per cercare e raccontare i 3 eventi storici più significativi della Juventus accaduti in questo giorno nel corso degli anni: trofei, partite storiche, grandi rimonte, record. Il testo generato viene formattato in HTML per Telegram e pubblicato sul canale **@Juventus_Reborn**.

-----

## Come funziona

```
                ┌──────────────────────┐
                │   GitHub Actions      │  ← avvio manuale (workflow_dispatch)
                │   accadde_oggi.yml    │
                └──────────┬───────────┘
                           │
                           ▼
        ┌──────────────────────────────────────┐
        │                script.py              │
        │  1. chiede a Gemini i 3 eventi Juve   │
        │     storici di oggi                   │
        │  2. converte gli anni in emoji        │
        │  3. formatta in HTML e pubblica       │
        └────────────────┬──────────┬────────────┘
                         │          │
                         ▼          ▼
                   ┌─────────┐  ┌──────────┐
                   │ Gemini  │  │ Telegram │
                   │  (AI)   │  │ (output) │
                   └─────────┘  └──────────┘
```

Se Gemini non trova eventi rilevanti per quella data, l’invio viene annullato automaticamente e non viene pubblicato nulla.

-----

## Funzionalità

- **Ricerca storica AI** — Gemini cerca automaticamente gli eventi più iconici della Juventus per la data odierna (trofei, record, big match, rimonte).
- **Filtro automatico** — i compleanni di giocatori e allenatori sono esclusi tassativamente dal prompt; vengono selezionati al massimo 3 eventi.
- **Anni in emoji** — tutti gli anni a 4 cifre nel testo vengono convertiti automaticamente in numeri emoji (es. `1️⃣9️⃣8️⃣4️⃣`) per uno stile visivo riconoscibile.
- **Formattazione HTML** — il testo segue una struttura precisa con titolo in grassetto (`<b>`) e descrizione in corsivo (`<i>`), ottimizzata per Telegram.
- **Gestione giorni vuoti** — se Gemini non trova eventi rilevanti per quella data, l’invio viene annullato automaticamente e non viene pubblicato nulla.
- **Retry esponenziale** — in caso di errore 503 (servizio Gemini non disponibile), lo script riprova fino a 5 volte con attesa crescente.

-----

## Formato del messaggio

```
👀🔙 ACCADDE OGGI | 15 MAGGIO

ANNO - Titolo Evento
Descrizione breve in corsivo.

ANNO - Secondo Evento
Altra descrizione in corsivo.

👉 @Juventus_Reborn
```

-----

## Struttura del repository

```
AccaddeOggi_JR/
├── script.py                          # Script principale
└── .github/workflows/
    └── accadde_oggi.yml               # Workflow GitHub Actions
```

-----

## Configurazione

In **Settings → Secrets and variables → Actions** aggiungi:

|Secret            |Descrizione                        |
|------------------|-----------------------------------|
|`GEMINI_API_KEY`  |Chiave API Google Gemini.          |
|`TELEGRAM_TOKEN`  |Token del bot Telegram.            |
|`TELEGRAM_CHAT_ID`|Chat ID del canale di destinazione.|

-----

## Avvio

1. Fai il **fork** del repository.
1. Configura i secret elencati sopra.
1. Avvia il workflow manualmente da `Actions → Rubrica Accade Oggi → Run workflow`.

> Per automatizzare l’invio quotidiano, aggiungi uno schedule cron nel file `accadde_oggi.yml`:
> 
> ```yaml
> on:
>   schedule:
>     - cron: '0 8 * * *'   # ogni giorno alle 08:00 UTC
>   workflow_dispatch:
> ```

-----

## Stack tecnico

`Python 3.10` · `google-genai` · `urllib` · `GitHub Actions`

-----

## Modello AI

[Google Gemini](https://ai.google.dev/) — modello `gemini-3.5-flash` con temperatura `0.1` per massimizzare la precisione storica.

-----

<div align="center">

*Progetto amatoriale. Non affiliato con la Juventus FC, Telegram o Google.*

</div>