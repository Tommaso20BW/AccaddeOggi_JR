import html
import json
import os
import random
import re
import sys
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
ATTESE_NUOVI_CICLI_GEMINI = (60, 180, 600)
MASSIMO_EVENTI = 3
SOGLIA_IMPORTANZA = 9
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
    "RECORD_STORICO",
}
ESITO_RICHIESTO_PER_CATEGORIA = {
    "TROFEO": "TROFEO_CONQUISTATO",
    "SCUDETTO": "SCUDETTO_CONQUISTATO",
    "PARTITA_ICONICA": "VITTORIA",
    "RECORD_STORICO": "RECORD_POSITIVO",
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
        modelli = [modello.strip() for modello in elenco.split(",") if modello.strip()]
        if not modelli:
            raise RuntimeError("GEMINI_MODELS non contiene alcun modello valido.")
    else:
        modello_legacy = os.environ.get("GEMINI_MODEL", "").strip()
        modelli = ([modello_legacy] if modello_legacy else []) + list(
            MODELLI_GEMINI_PREDEFINITI
        )

    return tuple(dict.fromkeys(modelli))


def attendi_orario_preciso(ora, minuto, fuso, margine_massimo_minuti=15):
    """Attende l'orario di invio se il job parte poco prima del target."""
    ora_corrente = datetime.now(fuso)
    target = ora_corrente.replace(hour=ora, minute=minuto, second=0, microsecond=0)
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
        "0": "0\ufe0f\u20e3",
        "1": "1\ufe0f\u20e3",
        "2": "2\ufe0f\u20e3",
        "3": "3\ufe0f\u20e3",
        "4": "4\ufe0f\u20e3",
        "5": "5\ufe0f\u20e3",
        "6": "6\ufe0f\u20e3",
        "7": "7\ufe0f\u20e3",
        "8": "8\ufe0f\u20e3",
        "9": "9\ufe0f\u20e3",
    }
    return "".join(emoji_numeri[cifra] for cifra in str(anno))


def chiama_gemini_con_retry(client, model, prompt, config, max_retries=5):
    """Chiama Gemini con retry esponenziale per gli errori temporanei."""
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
        except Exception as exc:
            errore = str(exc)
            temporaneo = any(
                codice in errore
                for codice in ("429", "500", "502", "503", "504", "UNAVAILABLE")
            )
            if not temporaneo or attempt == max_retries - 1:
                raise
            attesa = (2**attempt) + random.uniform(0, 1)
            print(
                f"Tentativo {attempt + 1}/{max_retries} fallito. "
                f"Riprovo tra {attesa:.1f}s..."
            )
            time.sleep(attesa)

    raise RuntimeError("Gemini non disponibile.")


def _errore_ammette_fallback(exc):
    errore = str(exc).lower()
    indicatori = (
        "404",
        "410",
        "429",
        "500",
        "502",
        "503",
        "504",
        "deprecated",
        "model_not_found",
        "no longer available",
        "not found",
        "not supported",
        "resource_exhausted",
        "shut down",
        "unavailable",
    )
    return any(indicatore in errore for indicatore in indicatori)


def chiama_gemini_con_fallback(
    client,
    models,
    prompt,
    config,
    max_retries=2,
    round_delays=ATTESE_NUOVI_CICLI_GEMINI,
):
    """Prova piu' modelli e ripete l'intera catena dopo attese progressive."""
    modelli = tuple(models)
    if not modelli:
        raise RuntimeError("Nessun modello Gemini configurato.")
    if max_retries < 1:
        raise ValueError("max_retries deve essere almeno 1.")

    attese_cicli = tuple(round_delays)
    if any(attesa < 0 for attesa in attese_cicli):
        raise ValueError("Le attese tra i cicli Gemini non possono essere negative.")

    numero_cicli = len(attese_cicli) + 1
    ultimo_errore = None

    for ciclo in range(numero_cicli):
        print(f"Ciclo Gemini {ciclo + 1}/{numero_cicli}.")
        for indice, modello in enumerate(modelli):
            try:
                risposta = chiama_gemini_con_retry(
                    client,
                    modello,
                    prompt,
                    config,
                    max_retries=max_retries,
                )
                print(f"Risposta ottenuta con il modello Gemini: {modello}")
                return risposta
            except Exception as exc:
                ultimo_errore = exc
                if not _errore_ammette_fallback(exc):
                    raise
                if indice < len(modelli) - 1:
                    successivo = modelli[indice + 1]
                    print(
                        f"Modello Gemini non disponibile: {modello}. "
                        f"Passo al fallback: {successivo}."
                    )

        if ciclo < len(attese_cicli):
            attesa_base = attese_cicli[ciclo]
            jitter = random.uniform(0, min(10, attesa_base * 0.1))
            attesa = attesa_base + jitter
            print(
                "Tutti i modelli Gemini sono temporaneamente indisponibili. "
                f"Attendo {attesa:.0f}s, poi riprovo l'intera catena."
            )
            time.sleep(attesa)

    if ultimo_errore is not None:
        raise ultimo_errore
    raise RuntimeError("Tutti i modelli Gemini configurati non sono disponibili.")


def estrai_json(testo):
    """Estrae un oggetto JSON anche se il modello aggiunge un code fence."""
    testo = testo.strip()
    testo = re.sub(r"^```(?:json)?\s*", "", testo, flags=re.IGNORECASE)
    testo = re.sub(r"\s*```$", "", testo)
    inizio = testo.find("{")
    fine = testo.rfind("}")
    if inizio == -1 or fine == -1 or fine < inizio:
        raise ValueError("Gemini non ha restituito un oggetto JSON.")
    valore = json.loads(testo[inizio : fine + 1])
    if not isinstance(valore, dict):
        raise ValueError("La risposta JSON di Gemini non e' un oggetto.")
    return valore


def _config_con_ricerca(system_instruction):
    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[types.Tool(google_search=types.GoogleSearch())],
    )


def scopri_candidati(client, giorno_mese, data_italiana, models=None):
    """Prima passata: trova soltanto possibili eventi di eccezionale rilievo."""
    istruzioni = """
Sei un ricercatore di storia della Juventus. Devi proporre candidati, non scrivere
un post. Usa Google Search e sii estremamente selettivo. Considera esclusivamente
la prima squadra maschile e soltanto fatti avvenuti esattamente nella data chiesta.

Un evento e' candidabile solo se appartiene a uno di questi casi:
1. conquista di un trofeo ufficiale o certezza matematica di uno Scudetto;
2. partita universalmente ricordata come una delle piu' iconiche della storia del
   club (finale, impresa europea o rimonta eccezionale), non un normale big match;
3. record positivo di squadra di importanza nazionale o europea, storico e
   ampiamente riconosciuto.

Usa come riferimento editoriale Juventus-Atletico Madrid 3-0 del 12 marzo 2019:
una rimonta europea di quel peso e' una PARTITA_ICONICA da pubblicare. Un normale
big match, anche se vinto nettamente, non raggiunge automaticamente quella soglia.

Scarta senza eccezioni: normali vittorie di campionato o coppa, amichevoli,
compleanni, nascite, morti, acquisti, cessioni, rinnovi, presentazioni, esordi,
singoli gol, presenze o record individuali, anniversari, sorteggi, premiazioni,
sconfitte, eliminazioni, eventi negativi, Women, Next Gen, Primavera e giovanili.
Quando il rilievo o la data non sono certi, non proporre l'evento. E' preferibile
restituire zero eventi invece di riempire il post.

Rispondi esclusivamente con JSON valido, senza Markdown, in questa forma:
{"events":[{"event_date":"data storica YYYY-MM-DD","year":1234,
"category":"categoria ammessa","outcome":"esito positivo ammesso",
"competition":"competizione o record",
"opponent":"avversario oppure stringa vuota","title":"titolo breve",
"description":"una frase fattuale breve","reason":"motivo del rilievo storico"}]}
Inserisci al massimo 5 candidati. Le categorie consentite sono TROFEO, SCUDETTO,
PARTITA_ICONICA e RECORD_STORICO. Gli unici outcome consentiti sono rispettivamente
TROFEO_CONQUISTATO, SCUDETTO_CONQUISTATO, VITTORIA e RECORD_POSITIVO.
"""
    prompt = (
        f"Cerca eventi juventini di importanza eccezionale avvenuti il {data_italiana} "
        f"di un qualsiasi anno storico. Giorno e mese devono essere {giorno_mese}; "
        "event_date deve contenere l'anno reale dell'evento. Non includere eventi "
        "solo vagamente interessanti e non cambiare giorno per farli rientrare."
    )
    risposta = chiama_gemini_con_fallback(
        client,
        models or modelli_gemini_configurati(),
        prompt,
        _config_con_ricerca(istruzioni),
    )
    dati = estrai_json(risposta.text or "")
    eventi = dati.get("events", [])
    return eventi if isinstance(eventi, list) else []


def verifica_candidati(client, candidati, giorno_mese, data_italiana, models=None):
    """Seconda passata: un revisore indipendente verifica data, fonti e rilievo."""
    if not candidati:
        return []

    istruzioni = """
Sei il fact-checker finale di una rubrica Juventus. Non fidarti della lista ricevuta:
verifica ogni candidato da zero con Google Search. Approva un evento soltanto se:
- almeno due fonti web affidabili e tra loro indipendenti confermano lo stesso fatto;
- le fonti confermano il giorno, mese e anno esatti, non soltanto l'anno o un
  articolo commemorativo pubblicato nella data richiesta;
- riguarda la prima squadra maschile della Juventus;
- non e' una sconfitta, un'eliminazione o un evento negativo, anche se famoso;
- raggiunge almeno 9/10 nella scala seguente: 10 conquista di Champions/Coppa dei
  Campioni oppure una delle imprese o dei record di squadra piu' celebri e
  indiscutibili dell'intera storia del club; 9 conquista di un altro trofeo
  ufficiale, Scudetto matematico, impresa universalmente iconica o record storico
  di squadra nazionale/europeo; 8 o meno normale vittoria, big match, turno
  preliminare, traguardo individuale o curiosita'. Gli eventi con voto 8 o meno
  vanno scartati.

Benchmark editoriale: Juventus-Atletico Madrid 3-0 del 12 marzo 2019 e' una
PARTITA_ICONICA che raggiunge la soglia; una normale vittoria contro una grande
squadra non basta.

Non promuovere un candidato solo per arrivare a un certo numero. Zero eventi e' un
esito corretto. Correggi i dettagli inesatti, ma se la data reale non coincide con
quella richiesta scarta l'evento.

Rispondi esclusivamente con JSON valido, senza Markdown:
{"events":[{"event_date":"data storica YYYY-MM-DD","year":1234,
"category":"categoria ammessa","outcome":"esito positivo ammesso","importance":9,
"competition":"competizione o record","opponent":"avversario o stringa vuota",
"title":"titolo da due a cinque parole","description":"una sola frase fattuale breve",
"canonical_id":"ANNO|CATEGORIA|COMPETIZIONE_O_RECORD|AVVERSARIO_O_NESSUNO",
"source_urls":["https://fonte1/...","https://fonte2/..."]}]}

Titolo: da 2 a 5 parole. Descrizione: una sola frase, massimo 240 caratteri, senza
HTML, Markdown, enfasi, URL o citazioni. canonical_id deve identificare il fatto e
non la data di pubblicazione: ANNO|CATEGORIA|COMPETIZIONE_O_RECORD|AVVERSARIO_O_NESSUNO.
Gli outcome ammessi sono TROFEO_CONQUISTATO per TROFEO, SCUDETTO_CONQUISTATO per
SCUDETTO, VITTORIA per PARTITA_ICONICA e RECORD_POSITIVO per RECORD_STORICO.
Restituisci al massimo 3 eventi, dal piu' vecchio al piu' recente. Non abbassare
mai la soglia di importanza per arrivare a tre: anche zero, uno o due sono corretti.
"""
    prompt = (
        f"La ricorrenza richiesta e' il {data_italiana}, giorno e mese {giorno_mese}, "
        "in qualsiasi anno storico. Verifica rigorosamente questi candidati e usa "
        "in event_date l'anno reale del fatto:\n"
        f"{json.dumps(candidati, ensure_ascii=False)}"
    )
    risposta = chiama_gemini_con_fallback(
        client,
        models or modelli_gemini_configurati(),
        prompt,
        _config_con_ricerca(istruzioni),
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
    """Applica vincoli deterministici: il prompt da solo non e' una garanzia."""
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
        descrizione = str(evento.get("description", "")).strip()
        categoria = str(evento.get("category", "")).strip().upper()
        outcome = str(evento.get("outcome", "")).strip().upper()
        canonical_id = str(evento.get("canonical_id", "")).strip()
        competition = str(evento.get("competition", "")).strip()
        opponent = str(evento.get("opponent", "")).strip()
        source_urls = evento.get("source_urls", [])

        event_date = str(evento.get("event_date", ""))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
            continue
        if event_date[5:] != giorno_mese:
            continue
        try:
            data_evento = datetime.strptime(event_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if anno != data_evento.year or not (1897 <= anno <= datetime.now(FUSO_ORARIO).year):
            continue
        if categoria not in CATEGORIE_AMMESSE:
            continue
        if outcome != ESITO_RICHIESTO_PER_CATEGORIA[categoria]:
            continue
        if importanza < SOGLIA_IMPORTANZA:
            continue
        if not (2 <= len(titolo.split()) <= 5):
            continue
        if not descrizione or len(descrizione) > 240:
            continue
        if any(segno in titolo + descrizione for segno in ("<", ">", "*", "http")):
            continue
        if not canonical_id or len(canonical_id) > 180:
            continue
        if not isinstance(source_urls, list) or len(_domini_fonti(source_urls)) < 2:
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

    validi.sort(key=lambda voce: (voce["year"], -voce["importance"]))
    return validi[:MASSIMO_EVENTI]


def _normalizza(testo):
    testo = unicodedata.normalize("NFKD", str(testo))
    testo = "".join(carattere for carattere in testo if not unicodedata.combining(carattere))
    return re.sub(r"[^a-z0-9]+", " ", testo.lower()).strip()


def _token_identita(evento):
    testo = " ".join(
        str(evento.get(campo, ""))
        for campo in ("canonical_id", "competition", "opponent", "title")
    )
    return {
        token
        for token in _normalizza(testo).split()
        if len(token) > 1 and token not in STOPWORD_IDENTITA
    }


def eventi_equivalenti(primo, secondo):
    """Riconosce lo stesso fatto anche se titolo o giorno attribuito cambiano."""
    try:
        if int(primo.get("year")) != int(secondo.get("year")):
            return False
    except (TypeError, ValueError):
        return False

    id_primo = _normalizza(primo.get("canonical_id", ""))
    id_secondo = _normalizza(secondo.get("canonical_id", ""))
    if id_primo and id_primo == id_secondo:
        return True

    token_primo = _token_identita(primo)
    token_secondo = _token_identita(secondo)
    if not token_primo or not token_secondo:
        return False
    unione = token_primo | token_secondo
    jaccard = len(token_primo & token_secondo) / len(unione)
    similarita = SequenceMatcher(None, id_primo, id_secondo).ratio()
    return jaccard >= 0.65 or similarita >= 0.82


def carica_storico(percorso=PERCORSO_STORICO):
    if not percorso.exists():
        return []
    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Storico eventi illeggibile: {percorso}") from exc
    eventi = dati.get("events", []) if isinstance(dati, dict) else []
    if not isinstance(eventi, list):
        raise RuntimeError("Formato dello storico eventi non valido.")
    return [evento for evento in eventi if isinstance(evento, dict)]


def scarta_gia_pubblicati(eventi, storico):
    nuovi = []
    for evento in eventi:
        if any(eventi_equivalenti(evento, vecchio) for vecchio in storico):
            print(f"Evento gia' pubblicato, scartato: {evento['canonical_id']}")
            continue
        if any(eventi_equivalenti(evento, altro) for altro in nuovi):
            print(f"Candidato duplicato, scartato: {evento['canonical_id']}")
            continue
        nuovi.append(evento)
    return nuovi


def salva_nello_storico(eventi, pubblicato_il, percorso=PERCORSO_STORICO):
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
    percorso.parent.mkdir(parents=True, exist_ok=True)
    temporaneo = percorso.with_suffix(".tmp")
    temporaneo.write_text(
        json.dumps({"version": 1, "events": storico}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporaneo.replace(percorso)


def formatta_rubrica(eventi, data_italiana):
    blocchi = []
    for evento in eventi:
        anno = converti_anno_in_emoji(evento["year"])
        titolo = html.escape(evento["title"], quote=False)
        descrizione = html.escape(evento["description"], quote=False)
        blocchi.append(f"{anno} - <b>{titolo}</b>\n<i>{descrizione}</i>")

    titolo_principale = f"<b>\U0001f440\U0001f519 ACCADDE OGGI | {data_italiana}</b>\n\n"
    firma_finale = "\n\n\U0001f449 @Juventus_Reborn"
    return titolo_principale + "\n\n".join(blocchi) + firma_finale


def prepara_accadde_oggi(client=None, adesso=None, percorso_storico=PERCORSO_STORICO):
    adesso = adesso or datetime.now(FUSO_ORARIO)
    if adesso.tzinfo is None:
        adesso = adesso.replace(tzinfo=FUSO_ORARIO)
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
    data_italiana = f"{adesso.day} {mesi_ita[adesso.month]}"
    client = client or Client()
    modelli = modelli_gemini_configurati()

    print(f"Catena modelli Gemini: {' -> '.join(modelli)}")
    print(f"Ricerca molto selettiva degli eventi per il {data_italiana}...")
    candidati = scopri_candidati(client, giorno_mese, data_italiana, modelli)
    print(f"Candidati trovati: {len(candidati)}. Avvio il fact-check indipendente...")
    verificati = verifica_candidati(
        client, candidati, giorno_mese, data_italiana, modelli
    )
    validi = valida_eventi(verificati, giorno_mese)
    print(f"Eventi che superano data, fonti e soglia 9/10: {len(validi)}")

    storico = carica_storico(percorso_storico)
    nuovi = scarta_gia_pubblicati(validi, storico)
    if not nuovi:
        return None

    return Rubrica(
        testo=formatta_rubrica(nuovi, data_italiana),
        eventi=nuovi,
    )


def invia_a_telegram(testo):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": testo, "parse_mode": "HTML"}
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read()


def main():
    variabili = ("GEMINI_API_KEY", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
    mancanti = [nome for nome in variabili if not os.environ.get(nome)]
    if mancanti:
        raise RuntimeError(f"Mancano variabili d'ambiente: {', '.join(mancanti)}")

    attendi_orario_preciso(ORA_INVIO[0], ORA_INVIO[1], FUSO_ORARIO)
    rubrica = prepara_accadde_oggi()
    if rubrica is None:
        print("Nessun evento davvero importante e nuovo: nessun invio.")
        return

    print("Invio a Telegram...")
    invia_a_telegram(rubrica.testo)
    salva_nello_storico(
        rubrica.eventi,
        datetime.now(FUSO_ORARIO).isoformat(timespec="seconds"),
    )
    print("Inviato con successo e registrato nello storico!")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
