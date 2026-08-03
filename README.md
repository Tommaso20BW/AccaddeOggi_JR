# 👀🔙 AccaddeOggi JR

Bot Telegram per la rubrica quotidiana **“Accadde Oggi”** dedicata alla prima squadra maschile della Juventus.

## Criterio editoriale

Il bot pubblica soltanto eventi di rilievo storico eccezionale:

- conquista di un trofeo ufficiale o certezza matematica di uno Scudetto;
- partita universalmente riconosciuta come iconica, non un normale big match;
- record positivo di squadra di importanza nazionale o europea.

Come riferimento, la rimonta **Juventus–Atlético Madrid 3-0 del 12 marzo 2019** è una partita iconica ammessa; una normale vittoria contro una grande squadra non basta.

Sono esclusi vittorie ordinarie, amichevoli, compleanni, ricorrenze, mercato, esordi, gol e record individuali, oltre a sconfitte, eliminazioni, Women, Next Gen, Primavera e giovanili. Per tutte le categorie passano esclusivamente gli eventi valutati **9/10 o 10/10**. Da 8/10 in giù il bot non pubblica. Il codice richiede inoltre un esito positivo coerente con la categoria: vittoria, trofeo conquistato, Scudetto conquistato o record positivo.

## Controlli contro errori e duplicati

La generazione avviene in due passaggi separati:

1. Gemini cerca un massimo di cinque candidati tramite Google Search.
2. Un secondo controllo riparte dalle fonti, verifica giorno, mese e anno esatti, richiede almeno due domini indipendenti e conserva al massimo tre eventi. Il numero non viene mai riempito artificialmente: possono uscirne anche due, uno o nessuno.

Il codice applica poi controlli deterministici sul JSON ricevuto. Gli eventi inviati vengono registrati in [`data/eventi_pubblicati.json`](data/eventi_pubblicati.json) con un'identità canonica basata su anno, categoria, competizione e avversario. Prima di ogni invio, il nuovo risultato viene confrontato con tutto lo storico anche in forma approssimata: così lo stesso fatto viene scartato anche se Gemini cambia titolo, descrizione o gli attribuisce un altro giorno.

Lo storico viene aggiornato solo dopo che Telegram ha confermato l'invio. Il workflow esegue automaticamente commit e push del file; la sezione `concurrency` impedisce a due esecuzioni contemporanee di sovrascriversi.

## Esempio di output

```text
👀🔙 ACCADDE OGGI | 22 MAGGIO

1️⃣9️⃣9️⃣6️⃣ - Trionfo europeo a Roma
La Juventus conquista la Champions League.

👉 @Juventus_Reborn
```

Il messaggio reale usa `<b>` e `<i>` con `parse_mode=HTML`.

## Automazione GitHub Actions

Il workflow [`.github/workflows/accadde_oggi.yml`](.github/workflows/accadde_oggi.yml):

- si avvia manualmente con `workflow_dispatch`;
- usa Python 3.14 e le versioni fissate in `requirements.txt`;
- imposta il fuso `Europe/Rome`;
- esegue il bot e salva nello storico soltanto gli eventi realmente inviati;
- elimina i vecchi run completati al termine.

Non è presente uno `schedule` nel repository. Un servizio esterno può avviare il workflow poco prima delle 07:30; lo script attende l'orario esatto soltanto entro una finestra di 15 minuti.

## Configurazione

Configura questi secret in **Settings → Secrets and variables → Actions**:

| Secret | Obbligatorio | Uso |
|---|---:|---|
| `GEMINI_API_KEY` | sì | Accesso a Google Gemini |
| `TELEGRAM_TOKEN` | sì | Token del bot Telegram |
| `TELEGRAM_CHAT_ID` | sì | Chat o canale di destinazione |

Il bot usa una catena di modelli stabili: `gemini-3.6-flash`, poi
`gemini-3.5-flash` e infine `gemini-3.5-flash-lite`. Ogni modello viene ritentato
in caso di errore temporaneo; se resta indisponibile, il bot passa automaticamente
al successivo. Se tutti e tre restituiscono errori temporanei, il bot non termina
subito: attende circa 1, 3 e 10 minuti e riprova ogni volta l'intera catena. In
totale esegue fino a quattro cicli completi, con un piccolo jitter per evitare di
riprovare durante lo stesso picco di traffico.

La variabile facoltativa `GEMINI_MODELS` può sostituire l'intera catena con un
elenco separato da virgole. La precedente `GEMINI_MODEL` resta compatibile: il
modello indicato diventa il primo della catena, seguito dai fallback predefiniti.

## Avvio locale

```bash
python -m pip install -r requirements.txt
python script.py
```

Sono richiesti Python 3.10+ e le tre variabili d'ambiente indicate sopra.

---

Progetto amatoriale, non affiliato con Juventus FC, Telegram o Google.
