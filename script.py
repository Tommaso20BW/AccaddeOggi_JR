import html
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
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
    "gemini-2.5-flash",
    "gemini-3.5-flash-lite",
)

MASSIMO_EVENTI = 3
SOGLIA_IMPORTANZA = 8
GIORNI_CONSERVAZIONE_STORICO = 400

MAX_CICLI_GEMINI = max(
    1,
    int(os.environ.get("MAX_CICLI_GEMINI", "3")),
)
ATTESA_503_GEMINI = max(
    1,
    int(os.environ.get("ATTESA_503_GEMINI", "20")),
)

SERPER_ENDPOINT = "https://google.serper.dev/search"
SERPER_NUM_RISULTATI = max(
    5,
    min(20, int(os.environ.get("SERPER_NUM_RISULTATI", "10"))),
)
SERPER_TIMEOUT = max(
    5,
    int(os.environ.get("SERPER_TIMEOUT", "20")),
)

# Lo storico resta nella root del repository.
PERCORSO_STORICO = Path(
    os.environ.get(
        "EVENT_HISTORY_FILE",
        Path(__file__).resolve().parent / "eventi_pubblicati.json",
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


def chiama_gemini_con_fallback(
    client,
    models,
    prompt,
    config,
    max_retries=None,
):
    """
    Su 429 o 503 prova il modello successivo. Dopo aver tentato tutti i
    modelli, attende e ripete l'intera catena.
    """
    modelli = tuple(models)

    if not modelli:
        raise RuntimeError("Nessun modello Gemini configurato.")

    cicli = (
        MAX_CICLI_GEMINI
        if max_retries is None
        else max(1, int(max_retries))
    )

    ultimo_errore = None
    modelli_con_quota_giornaliera_esaurita = set()

    for ciclo in range(1, cicli + 1):
        attesa_ciclo = None
        modelli_tentati = 0

        for modello in modelli:
            if modello in modelli_con_quota_giornaliera_esaurita:
                continue

            modelli_tentati += 1

            try:
                print(
                    f"Tentativo con il modello {modello} "
                    f"(ciclo {ciclo}/{cicli})..."
                )

                risposta = client.models.generate_content(
                    model=modello,
                    contents=prompt,
                    config=config,
                )

                print(
                    f"Risposta ottenuta con il modello Gemini: {modello}"
                )
                return risposta

            except Exception as exc:
                ultimo_errore = exc
                messaggio = _testo_errore(exc)

                if _modello_non_disponibile(messaggio):
                    print(
                        f"Modello Gemini non disponibile: {modello}. "
                        "Provo il modello successivo..."
                    )
                    continue

                if _errore_quota(messaggio):
                    if _quota_giornaliera_modello(messaggio):
                        modelli_con_quota_giornaliera_esaurita.add(
                            modello
                        )
                        print(
                            f"{modello}: quota giornaliera esaurita. "
                            "Lo escludo dai prossimi cicli..."
                        )
                        continue

                    attesa_quota = _secondi_attesa_gemini(
                        messaggio
                    )
                    attesa_ciclo = max(
                        attesa_ciclo or 0,
                        attesa_quota,
                    )

                    print(
                        f"Quota temporanea per {modello}. "
                        "Provo il modello successivo..."
                    )
                    continue

                if _errore_temporaneo(messaggio):
                    attesa_503 = min(
                        ATTESA_503_GEMINI * (2 ** (ciclo - 1)),
                        60,
                    )
                    attesa_ciclo = max(
                        attesa_ciclo or 0,
                        attesa_503,
                    )

                    print(
                        f"Modello {modello} temporaneamente non disponibile. "
                        "Provo il modello successivo..."
                    )
                    continue

                raise

        if ciclo >= cicli or modelli_tentati == 0:
            break

        if attesa_ciclo is None:
            break

        print(
            "Tutti i modelli disponibili sono temporaneamente occupati. "
            f"Attendo {attesa_ciclo}s prima del ciclo successivo..."
        )
        time.sleep(attesa_ciclo)

    if ultimo_errore is None:
        raise RuntimeError(
            "Nessun modello Gemini configurato è disponibile."
        )

    raise ultimo_errore


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


def _config_senza_ricerca(system_instruction):
    """Configurazione Gemini senza Google Search integrato."""
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0,
        response_mime_type="application/json",
    )


def _richiesta_serper(query, num=None):
    """Esegue una ricerca Serper e restituisce i risultati organici."""
    api_key = os.environ.get("SERPER_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError(
            "Manca SERPER_API_KEY nei GitHub Secrets."
        )

    payload = json.dumps(
        {
            "q": query,
            "gl": "it",
            "hl": "it",
            "num": num or SERPER_NUM_RISULTATI,
        }
    ).encode("utf-8")

    richiesta = urllib.request.Request(
        SERPER_ENDPOINT,
        data=payload,
        headers={
            "X-API-KEY": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            richiesta,
            timeout=SERPER_TIMEOUT,
        ) as risposta:
            dati = json.loads(
                risposta.read().decode("utf-8")
            )
    except urllib.error.HTTPError as exc:
        corpo = exc.read().decode(
            "utf-8",
            errors="replace",
        )
        raise RuntimeError(
            f"Serper HTTP {exc.code}: {corpo[:500]}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Serper non raggiungibile: {exc.reason}"
        ) from exc

    organici = dati.get("organic", [])

    if not isinstance(organici, list):
        return []

    risultati = []

    for posizione, voce in enumerate(organici, start=1):
        if not isinstance(voce, dict):
            continue

        titolo = str(voce.get("title", "")).strip()
        link = str(voce.get("link", "")).strip()
        snippet = str(voce.get("snippet", "")).strip()
        data = str(voce.get("date", "")).strip()

        if not titolo or not link:
            continue

        risultati.append(
            {
                "position": posizione,
                "title": titolo,
                "link": link,
                "snippet": snippet,
                "date": data,
            }
        )

    return risultati


def _cerca_fonti(queries):
    """Esegue più ricerche ed elimina URL duplicati."""
    corpus = []
    url_visti = set()

    for indice, query in enumerate(queries, start=1):
        print(
            f"[Ricerca] {indice}/{len(queries)}: {query}"
        )

        risultati = _richiesta_serper(query)

        print(
            f"[Ricerca] Risultati organici: {len(risultati)}"
        )

        for risultato in risultati:
            link = risultato["link"]

            if link in url_visti:
                continue

            url_visti.add(link)
            corpus.append(
                {
                    "query": query,
                    **risultato,
                }
            )

    print(
        f"[Ricerca] Fonti uniche raccolte: {len(corpus)}"
    )
    return corpus


def _query_scoperta(data_italiana):
    return [
        f'Juventus "accadde oggi" "{data_italiana}"',
        f'Juventus storia "{data_italiana}" trofeo partita debutto acquisto',
        f'site:juventus.com "{data_italiana}" Juventus storia',
        f'Juventus on this day "{data_italiana}"',
    ]


def _query_verifica(candidati):
    queries = []

    for evento in candidati[:8]:
        if not isinstance(evento, dict):
            continue

        anno = str(evento.get("year", "")).strip()
        titolo = str(evento.get("title", "")).strip()
        competizione = str(
            evento.get("competition", "")
        ).strip()
        fase = str(
            evento.get("stage", "")
        ).strip()
        soggetto = str(
            evento.get("opponent", "")
        ).strip()

        termini = " ".join(
            parte
            for parte in (
                "Juventus",
                anno,
                titolo,
                competizione,
                fase,
                soggetto,
            )
            if parte
        )

        if termini:
            queries.append(termini)

    return queries[:8]


def scopri_candidati(
    client,
    giorno_mese,
    data_italiana,
    models=None,
):
    """Trova candidati usando Serper e li fa analizzare da Gemini."""
    fonti = _cerca_fonti(
        _query_scoperta(data_italiana)
    )

    if not fonti:
        print(
            "[Ricerca] Nessuna fonte trovata: nessun candidato."
        )
        return []

    istruzioni = """
Sei un ricercatore di storia della Juventus. Devi analizzare esclusivamente
i risultati di ricerca forniti nel prompt. Non usare conoscenze esterne e
non inventare dettagli assenti dalle fonti.

Considera soltanto la prima squadra maschile e fatti avvenuti esattamente
nel giorno e mese richiesti.

Sono candidabili:
1. trofei ufficiali e Scudetti matematicamente conquistati;
2. partite iconiche o memorabili;
3. record storici positivi di squadra;
4. debutti davvero iconici;
5. traguardi individuali eccezionali;
6. acquisti ufficiali realmente epocali o iconici.

Per PARTITA_ICONICA e PARTITA_MEMORABILE sii estremamente selettivo.
NON basta che una partita abbia un punteggio largo, sia giocata in Champions,
segni il ritorno della Juventus in una competizione o presenti marcatori
famosi. Deve essere una gara ricordata ancora oggi per una conseguenza
storica concreta: trofeo, qualificazione decisiva in una fase avanzata,
rimonta celebre, eliminazione di una grande avversaria, record eccezionale
o episodio realmente entrato nell'immaginario juventino.

NON candidare come PARTITA_ICONICA o PARTITA_MEMORABILE gare di preliminari
o qualificazioni di accesso a una competizione. Anche un largo successo in
queste fasi resta normalmente da 7 o meno. Questa esclusione non riguarda
ottavi, quarti, semifinali o finali della fase a eliminazione diretta.

Per ACQUISTO_ICONICO usa soltanto la data dell'annuncio ufficiale Juventus.
Scarta indiscrezioni, visite mediche, aeroporto e presentazioni.

Scarta eventi ordinari, sconfitte, eliminazioni, compleanni, morti, rinnovi,
cessioni, Women, Next Gen, Primavera e giovanili.

Rispondi esclusivamente con JSON valido:
{
  "events": [
    {
      "event_date": "YYYY-MM-DD",
      "year": 1234,
      "category": "categoria ammessa",
      "outcome": "esito ammesso",
      "competition": "competizione o contesto",
      "stage": "fase o turno esatto, oppure stringa vuota",
      "opponent": "avversario, giocatore o stringa vuota",
      "title": "titolo breve",
      "description": "una frase fattuale breve",
      "reason": "motivo del rilievo",
      "evidence_urls": ["https://..."]
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
        f"Data da verificare: {data_italiana}, giorno-mese {giorno_mese}.\n\n"
        "RISULTATI DI RICERCA SERPER:\n"
        + json.dumps(
            fonti,
            ensure_ascii=False,
            indent=2,
        )
    )

    risposta = chiama_gemini_con_fallback(
        client=client,
        models=models or modelli_gemini_configurati(),
        prompt=prompt,
        config=_config_senza_ricerca(istruzioni),
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
    """Verifica i candidati con una seconda ricerca Serper mirata."""
    if not candidati:
        return []

    queries = _query_verifica(candidati)

    if not queries:
        return []

    fonti = _cerca_fonti(queries)

    if not fonti:
        print(
            "[Verifica] Nessuna fonte trovata: candidati scartati."
        )
        return []

    istruzioni = """
Sei il fact-checker finale di una rubrica Juventus. Usa esclusivamente i
risultati di ricerca forniti nel prompt.

Approva un evento soltanto se:
- almeno due URL di domini indipendenti confermano lo stesso fatto;
- giorno, mese e anno sono coerenti;
- riguarda la prima squadra maschile;
- è positivo o celebrativo;
- merita davvero 8, 9 o 10.

Scala:
10 = evento epocale.
9 = grande trofeo, impresa iconica, record enorme o acquisto storico.
8 = evento ancora oggi chiaramente memorabile per un tifoso juventino e con
    una conseguenza storica concreta.
7 o meno = evento interessante o celebrativo ma non davvero memorabile.

Per PARTITA_ICONICA e PARTITA_MEMORABILE non assegnare 8 solo per un largo
punteggio, per il fatto che si giochi in Champions, per il ritorno della Juve
in una competizione o per la presenza di grandi nomi.

Preliminari e qualificazioni di accesso a una competizione sono sempre da
7 o meno e non devono essere restituiti. Ottavi, quarti, semifinali e finali
della fase a eliminazione diretta non rientrano in questa esclusione.

Una partita da 8 o più deve avere almeno uno di questi elementi sostanziali:
- rimonta storica o qualificazione ottenuta in circostanze eccezionali;
- eliminazione di una grande avversaria in una fase avanzata;
- finale o trofeo;
- record eccezionale e riconosciuto;
- episodio iconico ancora fortemente associato a quella partita.

Per ACQUISTO_ICONICO accetta soltanto l'annuncio ufficiale dell'acquisto.
Non approvare visite mediche, aeroporto, indiscrezioni o presentazione.

Non inventare URL. Usa soltanto URL presenti nel corpus Serper.

Rispondi esclusivamente con JSON valido:
{
  "events": [
    {
      "event_date": "YYYY-MM-DD",
      "year": 1234,
      "category": "categoria ammessa",
      "outcome": "esito ammesso",
      "importance": 8,
      "competition": "competizione o contesto",
      "stage": "fase o turno esatto, oppure stringa vuota",
      "opponent": "avversario, giocatore o stringa vuota",
      "title": "titolo da due a cinque parole",
      "description": "una frase, massimo 240 caratteri",
      "canonical_id": "ANNO|CATEGORIA|CONTESTO|SOGGETTO",
      "source_urls": [
        "https://fonte1.example/",
        "https://fonte2.example/"
      ]
    }
  ]
}

Restituisci tutti gli eventi validi da 8 a 10, fino a un massimo di 8.
Il codice sceglierà i migliori tre.
"""

    prompt = (
        f"Data: {data_italiana}, giorno-mese {giorno_mese}.\n\n"
        "CANDIDATI:\n"
        + json.dumps(
            candidati,
            ensure_ascii=False,
            indent=2,
        )
        + "\n\nRISULTATI DI VERIFICA SERPER:\n"
        + json.dumps(
            fonti,
            ensure_ascii=False,
            indent=2,
        )
    )

    risposta = chiama_gemini_con_fallback(
        client=client,
        models=models or modelli_gemini_configurati(),
        prompt=prompt,
        config=_config_senza_ricerca(istruzioni),
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


def _partita_di_accesso(contesto):
    """
    Riconosce preliminari e qualificazioni di accesso a una competizione.

    Non blocca ottavi/quarti/semifinali/finali. Il termine "playoff" da solo
    non è sufficiente, così non vengono esclusi eventuali playoff interni a
    una competizione già iniziata.
    """
    testo = _normalizza(contesto)

    if re.search(r"\bpreliminar\w*\b", testo):
        return True

    if re.search(
        r"\b(qualificazion\w*|qualifying|qualification)\b",
        testo,
    ):
        return True

    if re.search(r"\bplay\s*off\b", testo):
        indicatori_accesso = (
            "accesso",
            "qualific",
            "preliminar",
            "first round",
            "second round",
            "third round",
            "primo turno",
            "secondo turno",
            "terzo turno",
        )
        return any(indicatore in testo for indicatore in indicatori_accesso)

    return False


def valida_eventi(eventi, giorno_mese):
    """Applica i vincoli deterministici e restituisce solo gli eventi validi."""
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
        stage = str(
            evento.get("stage", "")
        ).strip()
        opponent = str(
            evento.get("opponent", "")
        ).strip()
        source_urls = evento.get("source_urls", [])
        event_date = str(evento.get("event_date", ""))

        motivo = None

        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
            motivo = "event_date non valido"
        elif event_date[5:] != giorno_mese:
            motivo = "giorno o mese non corrispondente"
        else:
            try:
                data_evento = datetime.strptime(
                    event_date,
                    "%Y-%m-%d",
                ).date()
            except ValueError:
                motivo = "data impossibile o malformata"
                data_evento = None

            if motivo is None:
                anno_corrente = datetime.now(FUSO_ORARIO).year

                if anno != data_evento.year:
                    motivo = "anno incoerente con event_date"
                elif not 1897 <= anno <= anno_corrente:
                    motivo = "anno fuori intervallo"
                elif categoria not in CATEGORIE_AMMESSE:
                    motivo = "categoria non ammessa"
                elif outcome != ESITO_RICHIESTO_PER_CATEGORIA[categoria]:
                    motivo = "outcome non ammesso per la categoria"
                elif importanza < SOGLIA_IMPORTANZA:
                    motivo = f"importanza sotto soglia ({importanza}/10)"
                elif (
                    categoria
                    in {"PARTITA_ICONICA", "PARTITA_MEMORABILE"}
                    and _partita_di_accesso(
                        " ".join(
                            (
                                competition,
                                stage,
                                titolo,
                                descrizione,
                                canonical_id,
                            )
                        )
                    )
                ):
                    motivo = (
                        "preliminare o qualificazione di accesso "
                        "non ammessa come partita memorabile"
                    )
                elif not 2 <= len(titolo.split()) <= 5:
                    motivo = "titolo fuori dal limite di 2-5 parole"
                elif not descrizione:
                    motivo = "descrizione vuota"
                elif len(descrizione) > 240:
                    motivo = "descrizione oltre 240 caratteri"
                elif any(
                    segno in titolo + descrizione
                    for segno in ("<", ">", "*", "http")
                ):
                    motivo = "markup o URL nel testo"
                elif not canonical_id:
                    motivo = "canonical_id mancante"
                elif len(canonical_id) > 180:
                    motivo = "canonical_id troppo lungo"
                elif (
                    not isinstance(source_urls, list)
                    or len(_domini_fonti(source_urls)) < 2
                ):
                    motivo = "meno di due domini indipendenti"

        if motivo is not None:
            print(
                f"Evento scartato dalla validazione: "
                f"{canonical_id or titolo or event_date} | {motivo}"
            )
            continue

        validi.append(
            {
                "event_date": event_date,
                "year": anno,
                "category": categoria,
                "outcome": outcome,
                "importance": importanza,
                "competition": competition,
                "stage": stage,
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
            "stage",
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


def _data_pubblicazione(evento):
    """Ricava la data completa in cui un evento è stato pubblicato."""
    published_at = str(evento.get("published_at", "")).strip()

    if not published_at:
        return None

    try:
        return datetime.fromisoformat(
            published_at.replace("Z", "+00:00")
        ).date()
    except ValueError:
        return None


def scarta_gia_pubblicati(
    eventi,
    storico,
    anno_pubblicazione=None,
):
    """Scarta un evento solo se già pubblicato nello stesso anno."""
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
                f"Evento già pubblicato nel {anno_pubblicazione}, "
                f"scartato: {evento['canonical_id']}"
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
    data_pubblicazione = _data_pubblicazione(
        {"published_at": pubblicato_il}
    )

    if data_pubblicazione is None:
        raise RuntimeError(
            "Data di pubblicazione dello storico non valida."
        )

    data_minima = data_pubblicazione - timedelta(
        days=GIORNI_CONSERVAZIONE_STORICO - 1
    )
    storico = [
        evento
        for evento in storico
        if (
            (data_evento := _data_pubblicazione(evento))
            is not None
            and data_minima <= data_evento <= data_pubblicazione
        )
    ]

    for evento in eventi:
        storico.append(
            {
                "canonical_id": evento["canonical_id"],
                "event_date": evento["event_date"],
                "year": evento["year"],
                "category": evento["category"],
                "outcome": evento["outcome"],
                "importance": evento["importance"],
                "competition": evento["competition"],
                "stage": evento.get("stage", ""),
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
        "Eventi che superano data, fonti, filtro editoriale e "
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
        "SERPER_API_KEY",
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


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        messaggio = str(exc)
        previsto = any(
            indicatore in messaggio.lower()
            for indicatore in (
                "429",
                "resource_exhausted",
                "quota",
                "serper http 429",
                "serper non raggiungibile",
                "gemini non disponibile",
            )
        )

        print()
        print("=" * 64)

        if previsto:
            print("ESITO FINALE: NESSUN INVIO")
            print("=" * 64)
            print(f"Motivo: {messaggio}")
            print("Telegram: nessun messaggio inviato.")
            print("Storico: invariato.")
            print("Il bot riproverà alla prossima esecuzione.")
            print("=" * 64)
            raise SystemExit(0)

        print("ESITO FINALE: ERRORE")
        print("=" * 64)
        print(f"Motivo: {messaggio}")
        print("Telegram: invio non confermato.")
        print("Storico: non aggiornato.")
        print("=" * 64)
        raise SystemExit(1)
