import html
import json
import os
import random
import re
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from google.genai import Client
from google.genai import types


ORA_INVIO = (7, 30)
FUSO_ORARIO = ZoneInfo("Europe/Rome")

MODELLI_GEMINI_PREDEFINITI = (
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)

MASSIMO_EVENTI = 3
SOGLIA_IMPORTANZA = 8

MAX_CICLI_GEMINI = max(
    1,
    int(os.environ.get("MAX_CICLI_GEMINI", "3")),
)
ATTESA_503_GEMINI = max(
    1,
    int(os.environ.get("ATTESA_503_GEMINI", "20")),
)

PERCORSO_STORICO = Path(
    os.environ.get(
        "EVENT_HISTORY_FILE",
        Path(__file__).resolve().parent / "data" / "eventi_pubblicati.json",
    )
)

CATEGORIE_AMMESSE = {
    "TROFEO",
    "SCUDETTO",
    "PARTITA_ICONICA",
    "PARTITA_MEMORABILE",
    "RECORD_STORICO",
    "DEBUTTO_ICONICO",
    "TRAGUARDO_STORICO",
    "ACQUISTO_ICONICO",
}

ESITO_RICHIESTO_PER_CATEGORIA = {
    "TROFEO": "TROFEO_CONQUISTATO",
    "SCUDETTO": "SCUDETTO_CONQUISTATO",
    "PARTITA_ICONICA": "VITTORIA",
    "PARTITA_MEMORABILE": "VITTORIA",
    "RECORD_STORICO": "RECORD_POSITIVO",
    "DEBUTTO_ICONICO": "DEBUTTO",
    "TRAGUARDO_STORICO": "TRAGUARDO_RAGGIUNTO",
    "ACQUISTO_ICONICO": "ACQUISTO_UFFICIALE",
}

STOPWORD_IDENTITA = {
    "a",
    "al",
    "alla",
    "con",
    "da",
    "dei",
    "del",
    "della",
    "di",
    "e",
    "il",
    "in",
    "la",
    "le",
    "lo",
    "per",
    "the",
    "un",
    "una",
    "juventus",
}


@dataclass
class Rubrica:
    testo: str
    eventi: list[dict]


class GeminiNonDisponibileError(RuntimeError):
    """Errore finale Gemini con riepilogo dei tentativi effettuati."""

    def __init__(self, messaggio, tentativi=None):
        super().__init__(messaggio)
        self.tentativi = list(tentativi or [])


MOSTRA_TRACEBACK = os.environ.get(
    "MOSTRA_TRACEBACK",
    "false",
).lower() in {"1", "true", "yes"}


def modelli_gemini_configurati():
    """Restituisce la catena ordinata di modelli, senza duplicati."""
    elenco = os.environ.get("GEMINI_MODELS")

    if elenco is not None:
        modelli = [
            modello.strip()
            for modello in elenco.split(",")
            if modello.strip()
        ]
        if not modelli:
            raise RuntimeError(
                "GEMINI_MODELS non contiene alcun modello valido."
            )
    else:
        modello_legacy = os.environ.get("GEMINI_MODEL", "").strip()
        modelli = (
            ([modello_legacy] if modello_legacy else [])
            + list(MODELLI_GEMINI_PREDEFINITI)
        )

    return tuple(dict.fromkeys(modelli))


def attendi_orario_preciso(
    ora,
    minuto,
    fuso,
    margine_massimo_minuti=15,
):
    """Attende l'orario di invio se il job parte poco prima del target."""
    ora_corrente = datetime.now(fuso)
    target = ora_corrente.replace(
        hour=ora,
        minute=minuto,
        second=0,
        microsecond=0,
    )
    secondi_attesa = (target - ora_corrente).total_seconds()

    if secondi_attesa <= 0:
        return

    if secondi_attesa > margine_massimo_minuti * 60:
        print(
            f"Attesa di {secondi_attesa / 60:.1f} minuti fuori dal margine, "
            "procedo subito."
        )
        return

    print(
        f"Attendo {secondi_attesa:.0f} secondi per raggiungere le "
        f"{ora:02d}:{minuto:02d} ora italiana..."
    )
    time.sleep(secondi_attesa)


def converti_anno_in_emoji(anno):
    emoji_numeri = {
        "0": "0️⃣",
        "1": "1️⃣",
        "2": "2️⃣",
        "3": "3️⃣",
        "4": "4️⃣",
        "5": "5️⃣",
        "6": "6️⃣",
        "7": "7️⃣",
        "8": "8️⃣",
        "9": "9️⃣",
    }
    return "".join(emoji_numeri[cifra] for cifra in str(anno))


def _testo_errore(exc):
    return str(exc)


def _secondi_attesa_gemini(messaggio):
    """Ricava il retry delay suggerito dall'errore Gemini."""
    for pattern in (
        r"Please retry in\s+([0-9.]+)s",
        r"['\"]retryDelay['\"]\s*:\s*['\"]([0-9.]+)s",
    ):
        match = re.search(
            pattern,
            messaggio,
            flags=re.IGNORECASE,
        )
        if match:
            return min(
                max(int(float(match.group(1))) + 2, 2),
                60,
            )
    return 30


def _quota_giornaliera_modello(messaggio):
    return bool(
        re.search(
            r"GenerateRequestsPerDay|requests? per day|daily quota",
            messaggio,
            flags=re.IGNORECASE,
        )
    )


def _errore_quota(messaggio):
    return (
        "429" in messaggio
        or "RESOURCE_EXHAUSTED" in messaggio.upper()
    )


def _errore_temporaneo(messaggio):
    testo = messaggio.lower()
    return (
        "500" in messaggio
        or "502" in messaggio
        or "503" in messaggio
        or "504" in messaggio
        or "unavailable" in testo
        or "overloaded" in testo
        or "deadline_exceeded" in testo
        or "timed out" in testo
        or "timeout" in testo
        or "connection reset" in testo
    )


def _modello_non_disponibile(messaggio):
    testo = messaggio.lower()
    indicatori = (
        "404",
        "410",
        "deprecated",
        "model_not_found",
        "model not found",
        "no longer available",
        "not supported",
        "shut down",
    )
    return any(indicatore in testo for indicatore in indicatori)


def _descrizione_breve_errore(messaggio):
    """Restituisce una descrizione corta e leggibile per i log."""
    testo = messaggio.lower()

    if _quota_giornaliera_modello(messaggio):
        return "429 quota giornaliera esaurita"
    if _errore_quota(messaggio):
        return "429 quota/rate limit"
    if _modello_non_disponibile(messaggio):
        return "modello non disponibile"
    if "503" in messaggio or "unavailable" in testo or "overloaded" in testo:
        return "503 servizio temporaneamente non disponibile"
    if "timeout" in testo or "timed out" in testo:
        return "timeout"
    if "401" in messaggio or "unauthenticated" in testo:
        return "401 chiave API non valida"
    if "403" in messaggio or "permission_denied" in testo:
        return "403 permesso negato"

    prima_riga = messaggio.strip().splitlines()[0] if messaggio.strip() else "errore sconosciuto"
    return prima_riga[:140]


def _stampa_riepilogo_gemini(tentativi):
    """Stampa un riepilogo compatto di tutti i tentativi Gemini."""
    print()
    print("=" * 64)
    print("RIEPILOGO GEMINI")
    print("=" * 64)

    if not tentativi:
        print("Nessun tentativo registrato.")
        return

    for voce in tentativi:
        ciclo = voce["ciclo"]
        modello = voce["modello"]
        esito = voce["esito"]
        print(f"Ciclo {ciclo}: {modello} -> {esito}")


def chiama_gemini_con_fallback(
    client,
    models,
    prompt,
    config,
    max_retries=None,
):
    """
    Su 429 o 503 prova il modello successivo, poi ripete la catena.

    I log restano sintetici e l'errore finale contiene il riepilogo completo,
    senza costringere GitHub Actions a mostrare un traceback chilometrico.
    """
    modelli = tuple(models)

    if not modelli:
        raise GeminiNonDisponibileError(
            "Nessun modello Gemini configurato."
        )

    cicli = (
        MAX_CICLI_GEMINI
        if max_retries is None
        else max(1, int(max_retries))
    )

    ultimo_errore = None
    tentativi = []
    modelli_con_quota_giornaliera_esaurita = set()

    for ciclo in range(1, cicli + 1):
        print()
        print(f"[Gemini] Ciclo {ciclo}/{cicli}")
        attesa_ciclo = None
        modelli_tentati = 0

        for modello in modelli:
            if modello in modelli_con_quota_giornaliera_esaurita:
                print(f"  - {modello}: saltato, quota giornaliera esaurita")
                continue

            modelli_tentati += 1

            try:
                print(f"  - {modello}: richiesta in corso...", flush=True)
                risposta = client.models.generate_content(
                    model=modello,
                    contents=prompt,
                    config=config,
                )

                tentativi.append(
                    {
                        "ciclo": ciclo,
                        "modello": modello,
                        "esito": "OK",
                    }
                )
                print(f"  - {modello}: OK")
                return risposta

            except Exception as exc:
                ultimo_errore = exc
                messaggio = _testo_errore(exc)
                descrizione = _descrizione_breve_errore(messaggio)
                tentativi.append(
                    {
                        "ciclo": ciclo,
                        "modello": modello,
                        "esito": descrizione,
                    }
                )
                print(f"  - {modello}: {descrizione}")

                if _modello_non_disponibile(messaggio):
                    continue

                if _errore_quota(messaggio):
                    if _quota_giornaliera_modello(messaggio):
                        modelli_con_quota_giornaliera_esaurita.add(modello)
                        continue

                    attesa_quota = _secondi_attesa_gemini(messaggio)
                    attesa_ciclo = max(attesa_ciclo or 0, attesa_quota)
                    continue

                if _errore_temporaneo(messaggio):
                    attesa_503 = min(
                        ATTESA_503_GEMINI * (2 ** (ciclo - 1)),
                        60,
                    )
                    attesa_ciclo = max(attesa_ciclo or 0, attesa_503)
                    continue

                _stampa_riepilogo_gemini(tentativi)
                raise GeminiNonDisponibileError(
                    f"Errore Gemini non recuperabile: {descrizione}",
                    tentativi,
                ) from exc

        if ciclo >= cicli or modelli_tentati == 0:
            break

        if attesa_ciclo is None:
            break

        print(f"[Gemini] Attendo {attesa_ciclo}s prima del prossimo ciclo...")
        time.sleep(attesa_ciclo)

    _stampa_riepilogo_gemini(tentativi)

    if ultimo_errore is None:
        raise GeminiNonDisponibileError(
            "Nessun modello Gemini configurato è disponibile.",
            tentativi,
        )

    raise GeminiNonDisponibileError(
        "Gemini non disponibile dopo tutti i tentativi configurati.",
        tentativi,
    ) from ultimo_errore


def estrai_json(testo):
    """Estrae un oggetto JSON anche se Gemini aggiunge un code fence."""
    testo = testo.strip()
    testo = re.sub(
        r"^```(?:json)?\s*",
        "",
        testo,
        flags=re.IGNORECASE,
    )
    testo = re.sub(r"\s*```$", "", testo)

    inizio = testo.find("{")
    fine = testo.rfind("}")

    if inizio == -1 or fine == -1 or fine < inizio:
        raise ValueError(
            "Gemini non ha restituito un oggetto JSON."
        )

    valore = json.loads(testo[inizio : fine + 1])

    if not isinstance(valore, dict):
        raise ValueError(
            "La risposta JSON di Gemini non è un oggetto."
        )

    return valore


def _config_con_ricerca(system_instruction):
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[
            types.Tool(
                google_search=types.GoogleSearch()
            )
        ],
    )


def scopri_candidati(
    client,
    giorno_mese,
    data_italiana,
    models=None,
):
    """Prima passata: trova eventi juventini con importanza almeno 8/10."""
    istruzioni = """
Sei un ricercatore di storia della Juventus. Devi proporre candidati, non
scrivere un post. Usa Google Search e sii molto selettivo.

Considera esclusivamente la prima squadra maschile e fatti avvenuti
esattamente nella data richiesta.

Sono candidabili:
1. trofei ufficiali e Scudetti matematicamente conquistati;
2. partite iconiche o memorabili, finali, rimonte, imprese europee e vittorie
   ancora oggi ricordate come capitoli importanti della storia juventina;
3. record storici positivi di squadra;
4. debutti davvero iconici di campioni o simboli del club;
5. traguardi individuali eccezionali e storicamente rilevanti;
6. acquisti ufficiali realmente epocali o iconici, tra i più importanti
   della storia del club.

Per ACQUISTO_ICONICO usa soltanto la data dell'annuncio ufficiale Juventus.
Non usare indiscrezioni, accordi verbali, arrivi in aeroporto, visite
mediche, presentazioni o primi allenamenti. Non basta che il giocatore fosse
costoso o titolare: l'operazione deve essere ancora oggi riconosciuta come
una delle più importanti o simboliche della storia juventina.

Benchmark:
- Juventus-Atletico Madrid 3-0 del 12 marzo 2019 è PARTITA_ICONICA;
- un normale big match non è automaticamente memorabile;
- un semplice esordio, una centesima presenza o un acquisto ordinario non
  raggiungono la soglia.

Scarta: normali vittorie, amichevoli, compleanni, nascite, morti, cessioni,
rinnovi, semplici presentazioni, singoli gol ordinari, ricorrenze minori,
sorteggi, premiazioni, sconfitte, eliminazioni, eventi negativi, Women,
Next Gen, Primavera e giovanili.

È meglio restituire zero eventi che forzare candidati deboli.

Rispondi esclusivamente con JSON valido:
{
  "events": [
    {
      "event_date": "YYYY-MM-DD",
      "year": 1234,
      "category": "categoria ammessa",
      "outcome": "esito ammesso",
      "competition": "competizione, record o contesto",
      "opponent": "avversario, giocatore o stringa vuota",
      "title": "titolo breve",
      "description": "una frase fattuale breve",
      "reason": "motivo del rilievo storico"
    }
  ]
}

Inserisci al massimo 8 candidati.

Categorie:
TROFEO, SCUDETTO, PARTITA_ICONICA, PARTITA_MEMORABILE, RECORD_STORICO,
DEBUTTO_ICONICO, TRAGUARDO_STORICO, ACQUISTO_ICONICO.

Outcome:
TROFEO_CONQUISTATO, SCUDETTO_CONQUISTATO, VITTORIA, RECORD_POSITIVO,
DEBUTTO, TRAGUARDO_RAGGIUNTO, ACQUISTO_UFFICIALE.
"""
    prompt = (
        f"Cerca eventi juventini con importanza potenziale almeno 8/10 "
        f"avvenuti il {data_italiana} di qualsiasi anno. Giorno e mese "
        f"devono essere {giorno_mese}; event_date deve contenere l'anno "
        "reale. Non cambiare data e non riempire la lista con eventi ordinari."
    )
    risposta = chiama_gemini_con_fallback(
        client=client,
        models=models or modelli_gemini_configurati(),
        prompt=prompt,
        config=_config_con_ricerca(istruzioni),
    )
    dati = estrai_json(risposta.text or "")
    eventi = dati.get("events", [])
    return eventi if isinstance(eventi, list) else []


def verifica_candidati(
    client,
    candidati,
    giorno_mese,
    data_italiana,
    models=None,
):
    """Seconda passata: verifica data, fonti, categoria e importanza."""
    if not candidati:
        return []

    istruzioni = """
Sei il fact-checker finale di una rubrica Juventus. Verifica ogni candidato
da zero con Google Search.

Approva soltanto se:
- almeno due fonti web affidabili e indipendenti confermano il fatto;
- giorno, mese e anno sono esatti;
- riguarda la prima squadra maschile;
- è positivo o celebrativo;
- merita davvero 8, 9 o 10.

Scala editoriale:
10 = evento epocale: Champions/Coppa dei Campioni, Scudetto, impresa
leggendaria o acquisto tra i più clamorosi nella storia del calcio.
9 = grande trofeo, impresa universalmente iconica, record storico enorme,
debutto o traguardo di un simbolo assoluto, acquisto di un fuoriclasse con
enorme risonanza e impatto storico.
8 = evento ancora oggi molto interessante per un tifoso juventino: partita
memorabile, debutto iconico, traguardo eccezionale o acquisto simbolico e
storicamente rilevante. Deve essere chiaramente sopra una normale ricorrenza.
7 o meno = normale vittoria, curiosità, semplice debutto, primo gol,
centesima presenza ordinaria, operazione di mercato normale o giocatore
costoso ma non storico. Va scartato.

Per ACQUISTO_ICONICO:
- usa soltanto la data dell'annuncio ufficiale Juventus;
- non approvare indiscrezioni, visite mediche, aeroporto o presentazione;
- non basta il prezzo;
- assegna 8+ solo a operazioni considerate ancora oggi davvero iconiche.

Juventus-Atletico Madrid 3-0 del 12 marzo 2019 è il benchmark di una
PARTITA_ICONICA. Non promuovere eventi solo per arrivare a tre.

Rispondi esclusivamente con JSON valido:
{
  "events": [
    {
      "event_date": "YYYY-MM-DD",
      "year": 1234,
      "category": "categoria ammessa",
      "outcome": "esito ammesso",
      "importance": 8,
      "competition": "competizione, record o contesto",
      "opponent": "avversario, giocatore o stringa vuota",
      "title": "titolo da due a cinque parole",
      "description": "una sola frase fattuale breve",
      "canonical_id": "ANNO|CATEGORIA|CONTESTO|SOGGETTO",
      "source_urls": [
        "https://fonte1.example/",
        "https://fonte2.example/"
      ]
    }
  ]
}

Categorie:
TROFEO, SCUDETTO, PARTITA_ICONICA, PARTITA_MEMORABILE, RECORD_STORICO,
DEBUTTO_ICONICO, TRAGUARDO_STORICO, ACQUISTO_ICONICO.

Outcome:
TROFEO_CONQUISTATO, SCUDETTO_CONQUISTATO, VITTORIA, RECORD_POSITIVO,
DEBUTTO, TRAGUARDO_RAGGIUNTO, ACQUISTO_UFFICIALE.

Titolo: 2-5 parole. Descrizione: una frase, massimo 240 caratteri, senza
HTML, Markdown o URL. Restituisci tutti gli eventi validi 8-10, fino a un
massimo di 8; il codice sceglierà i migliori tre.
"""
    prompt = (
        f"La ricorrenza è il {data_italiana}, giorno e mese {giorno_mese}. "
        "Verifica rigorosamente questi candidati, assegna importanza 8-10 "
        "solo quando meritata e usa l'anno reale in event_date:\n"
        f"{json.dumps(candidati, ensure_ascii=False)}"
    )
    risposta = chiama_gemini_con_fallback(
        client=client,
        models=models or modelli_gemini_configurati(),
        prompt=prompt,
        config=_config_con_ricerca(istruzioni),
    )
    dati = estrai_json(risposta.text or "")
    eventi = dati.get("events", [])
    return eventi if isinstance(eventi, list) else []


def _domini_fonti(urls):
    domini = set()

    for url in urls:
        if not isinstance(url, str):
            continue

        parsed = urlparse(url)

        if parsed.scheme in {"http", "https"} and parsed.netloc:
            dominio = parsed.netloc.lower().removeprefix("www.")
            domini.add(dominio)

    return domini


def valida_eventi(eventi, giorno_mese):
    """Applica vincoli deterministici alla risposta di Gemini."""
    validi = []

    for evento in eventi:
        if not isinstance(evento, dict):
            continue

        try:
            anno = int(evento.get("year"))
            importanza = int(evento.get("importance"))
        except (TypeError, ValueError):
            continue

        titolo = str(evento.get("title", "")).strip()
        descrizione = str(
            evento.get("description", "")
        ).strip()
        categoria = str(
            evento.get("category", "")
        ).strip().upper()
        outcome = str(
            evento.get("outcome", "")
        ).strip().upper()
        canonical_id = str(
            evento.get("canonical_id", "")
        ).strip()
        competition = str(
            evento.get("competition", "")
        ).strip()
        opponent = str(
            evento.get("opponent", "")
        ).strip()
        source_urls = evento.get("source_urls", [])
        event_date = str(evento.get("event_date", ""))

        if not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}",
            event_date,
        ):
            continue

        if event_date[5:] != giorno_mese:
            continue

        try:
            data_evento = datetime.strptime(
                event_date,
                "%Y-%m-%d",
            ).date()
        except ValueError:
            continue

        anno_corrente = datetime.now(
            FUSO_ORARIO
        ).year

        if anno != data_evento.year:
            continue

        if not 1897 <= anno <= anno_corrente:
            continue

        if categoria not in CATEGORIE_AMMESSE:
            continue

        if outcome != ESITO_RICHIESTO_PER_CATEGORIA[categoria]:
            continue

        if importanza < SOGLIA_IMPORTANZA:
            continue

        if not 2 <= len(titolo.split()) <= 5:
            continue

        if not descrizione or len(descrizione) > 240:
            continue

        if any(
            segno in titolo + descrizione
            for segno in ("<", ">", "*", "http")
        ):
            continue

        if not canonical_id or len(canonical_id) > 180:
            continue

        if (
            not isinstance(source_urls, list)
            or len(_domini_fonti(source_urls)) < 2
        ):
            continue

        validi.append(
            {
                "event_date": event_date,
                "year": anno,
                "category": categoria,
                "outcome": outcome,
                "importance": importanza,
                "competition": competition,
                "opponent": opponent,
                "title": titolo,
                "description": descrizione,
                "canonical_id": canonical_id,
                "source_urls": source_urls,
            }
        )

    validi.sort(
        key=lambda voce: (
            -voce["importance"],
            voce["year"],
            voce["canonical_id"],
        )
    )

    return validi[:MASSIMO_EVENTI]


def _normalizza(testo):
    testo = unicodedata.normalize(
        "NFKD",
        str(testo),
    )
    testo = "".join(
        carattere
        for carattere in testo
        if not unicodedata.combining(carattere)
    )
    return re.sub(
        r"[^a-z0-9]+",
        " ",
        testo.lower(),
    ).strip()


def _token_identita(evento):
    testo = " ".join(
        str(evento.get(campo, ""))
        for campo in (
            "canonical_id",
            "competition",
            "opponent",
            "title",
        )
    )

    return {
        token
        for token in _normalizza(testo).split()
        if len(token) > 1
        and token not in STOPWORD_IDENTITA
    }


def eventi_equivalenti(primo, secondo):
    """Riconosce lo stesso fatto anche con titoli leggermente diversi."""
    try:
        if int(primo.get("year")) != int(
            secondo.get("year")
        ):
            return False
    except (TypeError, ValueError):
        return False

    id_primo = _normalizza(
        primo.get("canonical_id", "")
    )
    id_secondo = _normalizza(
        secondo.get("canonical_id", "")
    )

    if id_primo and id_primo == id_secondo:
        return True

    token_primo = _token_identita(primo)
    token_secondo = _token_identita(secondo)

    if not token_primo or not token_secondo:
        return False

    unione = token_primo | token_secondo
    jaccard = len(
        token_primo & token_secondo
    ) / len(unione)

    similarita = SequenceMatcher(
        None,
        id_primo,
        id_secondo,
    ).ratio()

    return jaccard >= 0.65 or similarita >= 0.82


def carica_storico(percorso=PERCORSO_STORICO):
    if not percorso.exists():
        return []

    try:
        dati = json.loads(
            percorso.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Storico eventi illeggibile: {percorso}"
        ) from exc

    eventi = (
        dati.get("events", [])
        if isinstance(dati, dict)
        else []
    )

    if not isinstance(eventi, list):
        raise RuntimeError(
            "Formato dello storico eventi non valido."
        )

    return [
        evento
        for evento in eventi
        if isinstance(evento, dict)
    ]


def _anno_pubblicazione(evento):
    """Ricava l'anno in cui un evento è stato pubblicato."""
    published_at = str(evento.get("published_at", "")).strip()

    if not published_at:
        return None

    corrispondenza = re.match(r"^(\d{4})", published_at)

    if not corrispondenza:
        return None

    try:
        return int(corrispondenza.group(1))
    except ValueError:
        return None


def scarta_gia_pubblicati(
    eventi,
    storico,
    anno_pubblicazione=None,
):
    """
    Scarta un evento solo se è già stato pubblicato nello stesso anno.

    Un secondo run nello stesso anno non crea duplicati.
    Lo stesso anniversario torna pubblicabile negli anni successivi.
    """
    if anno_pubblicazione is None:
        anno_pubblicazione = datetime.now(
            FUSO_ORARIO
        ).year

    nuovi = []

    storico_anno_corrente = [
        evento
        for evento in storico
        if _anno_pubblicazione(evento) == anno_pubblicazione
    ]

    for evento in eventi:
        if any(
            eventi_equivalenti(evento, vecchio)
            for vecchio in storico_anno_corrente
        ):
            print(
                "Evento già pubblicato nel "
                f"{anno_pubblicazione}, scartato: "
                f"{evento['canonical_id']}"
            )
            continue

        if any(
            eventi_equivalenti(evento, altro)
            for altro in nuovi
        ):
            print(
                "Candidato duplicato nello stesso run, scartato: "
                f"{evento['canonical_id']}"
            )
            continue

        nuovi.append(evento)

    return nuovi


def salva_nello_storico(
    eventi,
    pubblicato_il,
    percorso=PERCORSO_STORICO,
):
    storico = carica_storico(percorso)

    for evento in eventi:
        storico.append(
            {
                "canonical_id": evento["canonical_id"],
                "event_date": evento["event_date"],
                "year": evento["year"],
                "category": evento["category"],
                "outcome": evento["outcome"],
                "competition": evento["competition"],
                "opponent": evento["opponent"],
                "title": evento["title"],
                "published_at": pubblicato_il,
            }
        )

    percorso.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporaneo = percorso.with_suffix(".tmp")

    temporaneo.write_text(
        json.dumps(
            {
                "version": 1,
                "events": storico,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    temporaneo.replace(percorso)


def formatta_rubrica(eventi, data_italiana):
    blocchi = []

    for evento in eventi:
        anno = converti_anno_in_emoji(
            evento["year"]
        )
        titolo = html.escape(
            evento["title"],
            quote=False,
        )
        descrizione = html.escape(
            evento["description"],
            quote=False,
        )

        blocchi.append(
            f"{anno} - <b>{titolo}</b>\n"
            f"<i>{descrizione}</i>"
        )

    titolo_principale = (
        f"<b>👀🔙 ACCADDE OGGI | "
        f"{data_italiana}</b>\n\n"
    )
    firma_finale = "\n\n👉 @Juventus_Reborn"

    return (
        titolo_principale
        + "\n\n".join(blocchi)
        + firma_finale
    )


def prepara_accadde_oggi(
    client=None,
    adesso=None,
    percorso_storico=PERCORSO_STORICO,
):
    adesso = adesso or datetime.now(
        FUSO_ORARIO
    )

    if adesso.tzinfo is None:
        adesso = adesso.replace(
            tzinfo=FUSO_ORARIO
        )

    mesi_ita = {
        1: "GENNAIO",
        2: "FEBBRAIO",
        3: "MARZO",
        4: "APRILE",
        5: "MAGGIO",
        6: "GIUGNO",
        7: "LUGLIO",
        8: "AGOSTO",
        9: "SETTEMBRE",
        10: "OTTOBRE",
        11: "NOVEMBRE",
        12: "DICEMBRE",
    }

    giorno_mese = adesso.strftime("%m-%d")
    data_italiana = (
        f"{adesso.day} "
        f"{mesi_ita[adesso.month]}"
    )

    client = client or Client()
    modelli = modelli_gemini_configurati()

    print(
        "Catena modelli Gemini: "
        + " -> ".join(modelli)
    )
    print(
        "Ricerca molto selettiva degli eventi "
        f"per il {data_italiana}..."
    )

    candidati = scopri_candidati(
        client=client,
        giorno_mese=giorno_mese,
        data_italiana=data_italiana,
        models=modelli,
    )

    print(
        f"Candidati trovati: {len(candidati)}. "
        "Avvio il fact-check indipendente..."
    )

    verificati = verifica_candidati(
        client=client,
        candidati=candidati,
        giorno_mese=giorno_mese,
        data_italiana=data_italiana,
        models=modelli,
    )

    validi = valida_eventi(
        verificati,
        giorno_mese,
    )

    print(
        "Eventi che superano data, fonti e "
        f"soglia 8/10: {len(validi)}"
    )

    storico = carica_storico(
        percorso_storico
    )
    nuovi = scarta_gia_pubblicati(
        validi,
        storico,
        anno_pubblicazione=adesso.year,
    )

    if not nuovi:
        return None

    return Rubrica(
        testo=formatta_rubrica(
            nuovi,
            data_italiana,
        ),
        eventi=nuovi,
    )


def invia_a_telegram(testo):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": testo,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    data = urllib.parse.urlencode(
        payload
    ).encode("utf-8")

    richiesta = urllib.request.Request(
        url,
        data=data,
    )

    with urllib.request.urlopen(
        richiesta,
        timeout=30,
    ) as risposta:
        corpo = risposta.read()

    dati = json.loads(
        corpo.decode("utf-8")
    )

    if not dati.get("ok"):
        raise RuntimeError(
            f"Telegram ha rifiutato il messaggio: {dati}"
        )

    return dati


def main():
    variabili = (
        "GEMINI_API_KEY",
        "TELEGRAM_TOKEN",
        "TELEGRAM_CHAT_ID",
    )

    mancanti = [
        nome
        for nome in variabili
        if not os.environ.get(nome)
    ]

    if mancanti:
        raise RuntimeError(
            "Mancano variabili d'ambiente: "
            + ", ".join(mancanti)
        )

    attendi_orario_preciso(
        ORA_INVIO[0],
        ORA_INVIO[1],
        FUSO_ORARIO,
    )

    rubrica = prepara_accadde_oggi()

    if rubrica is None:
        print(
            "Nessun evento davvero importante e nuovo: "
            "nessun invio."
        )
        return

    print("Invio a Telegram...")
    invia_a_telegram(rubrica.testo)

    salva_nello_storico(
        rubrica.eventi,
        datetime.now(
            FUSO_ORARIO
        ).isoformat(timespec="seconds"),
    )

    print(
        "Inviato con successo e registrato nello storico!"
    )


def _stampa_esito_senza_invio(exc):
    """Chiude il job senza errore quando Gemini non è utilizzabile."""
    print()
    print("=" * 64)
    print("ESITO FINALE: NESSUN INVIO")
    print("=" * 64)
    print(f"Motivo: {exc}")
    print("Telegram: nessun messaggio inviato.")
    print("Storico: invariato.")
    print("Workflow: completato senza errore; verrà ritentato al prossimo avvio.")
    print("=" * 64)


if __name__ == "__main__":
    try:
        main()
    except GeminiNonDisponibileError as exc:
        _stampa_esito_senza_invio(exc)
        raise SystemExit(0)
    except Exception as exc:
        print()
        print("=" * 64)
        print("ESITO FINALE: ERRORE REALE")
        print("=" * 64)
        print(f"Causa: {exc}")
        print("Telegram: stato non confermato.")
        print("Storico: controllare il log precedente.")
        print("=" * 64)
        if MOSTRA_TRACEBACK:
            raise
        raise SystemExit(1)
