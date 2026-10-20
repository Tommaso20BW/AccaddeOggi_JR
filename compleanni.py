import html
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

FUSO_ORARIO = ZoneInfo("Europe/Rome")

WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_TIMEOUT = max(5, int(os.environ.get("WIKIDATA_TIMEOUT", "60")))
WIKIDATA_MAX_TENTATIVI = max(1, int(os.environ.get("WIKIDATA_MAX_TENTATIVI", "3")))
WIKIDATA_ATTESA_RETRY = max(0, int(os.environ.get("WIKIDATA_ATTESA_RETRY", "5")))
WIKIDATA_USER_AGENT = (
    "AccaddeOggi-Juventus-Birthday-Bot/1.0 "
    "(https://github.com/Tommaso20BW/AccaddeOggi_JR)"
)

WIKIPEDIA_IT_BASE = "https://it.wikipedia.org"
WIKIPEDIA_TIMEOUT = max(5, int(os.environ.get("WIKIPEDIA_TIMEOUT", "30")))

JUVENTUS_QID = "Q1422"

# Gli attuali giocatori della Juventus vengono sempre inclusi.
# Gli ex vengono inclusi solo se hanno almeno questa soglia di presenze.
SOGLIA_PRESENZE_EX = 50

# Soglia di partite in panchina per gli allenatori (ex e attuali senza data
# di inizio recente). Per gli allenatori si contano tutte le competizioni,
# come nella tabella "Statistiche da allenatore" di it.wikipedia.
SOGLIA_PARTITE_ALLENATORE = max(
    1, int(os.environ.get("SOGLIA_PARTITE_ALLENATORE", str(SOGLIA_PRESENZE_EX)))
)

# I giocatori attuali senza dati sulle presenze vengono inclusi solo se il
# loro ingaggio alla Juventus ha una data di inizio (P580) pari o successiva
# a questo anno. Evita che vecchie voci Wikidata mai chiuse (settore giovanile,
# ex senza data di fine) passino il filtro come "rosa attuale".
SOGLIA_ANNO_INIZIO = max(2000, int(os.environ.get("SOGLIA_ANNO_INIZIO", "2020")))

GIORNI_CONSERVAZIONE_STORICO = 400

PERCORSO_STORICO = Path(
    os.environ.get(
        "BIRTHDAY_HISTORY_FILE",
        Path(__file__).resolve().parent / "compleanni_inviati.json",
    )
)

MESI_ITALIANI = (
    "GENNAIO",
    "FEBBRAIO",
    "MARZO",
    "APRILE",
    "MAGGIO",
    "GIUGNO",
    "LUGLIO",
    "AGOSTO",
    "SETTEMBRE",
    "OTTOBRE",
    "NOVEMBRE",
    "DICEMBRE",
)


# ---------------------------------------------------------------------------
# Query Wikidata
# ---------------------------------------------------------------------------

def costruisci_query_wikidata(giorno, mese):
    """Crea la query per rosa attuale ed ex Juventus rilevanti (giocatori)."""
    return f"""
SELECT DISTINCT ?player ?playerLabel ?birthDate ?deathDate WHERE {{

  ?player wdt:P31 wd:Q5;
          p:P54 ?juveStatement;
          p:P569 ?birthStatement.

  ?juveStatement ps:P54 wd:{JUVENTUS_QID};
                 wikibase:rank ?juveRank.

  OPTIONAL {{ ?juveStatement pq:P1350 ?juveMatches. }}
  OPTIONAL {{ ?juveStatement pq:P580  ?juveStart.  }}
  OPTIONAL {{ ?player wdt:P570 ?deathDate. }}

  ?birthStatement psv:P569 ?birthNode;
                  wikibase:rank ?birthRank.
  ?birthNode wikibase:timeValue ?birthDate;
             wikibase:timePrecision ?birthPrecision.

  FILTER(?juveRank != wikibase:DeprecatedRank)

  # Passa se:
  # 1) è ancora alla Juventus (nessuna data di fine) e ha presenze note >=
  #    soglia, oppure non ha presenze ma il rapporto ha una data di inizio
  #    recente (nuova firma); questo blocca le vecchie voci Wikidata mai
  #    chiuse del settore giovanile che non hanno né fine né presenze.
  # 2) è un ex con almeno SOGLIA_PRESENZE_EX presenze.
  FILTER(
    (
      NOT EXISTS {{ ?juveStatement pq:P582 ?juveEnd. }}
      &&
      (
        ( BOUND(?juveMatches) && ?juveMatches >= {SOGLIA_PRESENZE_EX} )
        ||
        (
          !BOUND(?juveMatches)
          && BOUND(?juveStart)
          && YEAR(?juveStart) >= {SOGLIA_ANNO_INIZIO}
        )
      )
    )
    ||
    (
      EXISTS {{ ?juveStatement pq:P582 ?juveEnd. }}
      && BOUND(?juveMatches)
      && ?juveMatches >= {SOGLIA_PRESENZE_EX}
    )
  )

  FILTER(?birthRank != wikibase:DeprecatedRank)
  FILTER(?birthPrecision >= 11)
  FILTER(MONTH(?birthDate) = {int(mese)})
  FILTER(DAY(?birthDate)   = {int(giorno)})

  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "it,en".
  }}
}}
ORDER BY ?birthDate ?playerLabel
""".strip()


def costruisci_query_allenatori(giorno, mese):
    """Query per gli allenatori Juventus (P6087) nati nel giorno indicato.

    Qui NON si filtra sulle partite: su Wikidata il qualificatore P1350 è
    spesso assente per gli allenatori, quindi la soglia viene applicata in
    Python (con ripiego sulla voce di it.wikipedia).
    """
    return f"""
SELECT DISTINCT ?player ?playerLabel ?birthDate ?deathDate
                ?coachStatement ?coachMatches ?coachStart ?coachEnd ?itTitle
WHERE {{

  ?player wdt:P31 wd:Q5;
          p:P6087 ?coachStatement;
          p:P569 ?birthStatement.

  ?coachStatement ps:P6087 wd:{JUVENTUS_QID};
                  wikibase:rank ?coachRank.

  OPTIONAL {{ ?coachStatement pq:P1350 ?coachMatches. }}
  OPTIONAL {{ ?coachStatement pq:P580  ?coachStart.   }}
  OPTIONAL {{ ?coachStatement pq:P582  ?coachEnd.     }}
  OPTIONAL {{ ?player wdt:P570 ?deathDate. }}

  OPTIONAL {{
    ?article schema:about ?player;
             schema:isPartOf <{WIKIPEDIA_IT_BASE}/>;
             schema:name ?itTitle.
  }}

  ?birthStatement psv:P569 ?birthNode;
                  wikibase:rank ?birthRank.
  ?birthNode wikibase:timeValue ?birthDate;
             wikibase:timePrecision ?birthPrecision.

  FILTER(?coachRank != wikibase:DeprecatedRank)
  FILTER(?birthRank != wikibase:DeprecatedRank)
  FILTER(?birthPrecision >= 11)
  FILTER(MONTH(?birthDate) = {int(mese)})
  FILTER(DAY(?birthDate)   = {int(giorno)})

  SERVICE wikibase:label {{
    bd:serviceParam wikibase:language "it,en".
  }}
}}
ORDER BY ?birthDate ?playerLabel
""".strip()


def _data_wikidata(valore):
    """Converte la data Wikidata ISO in una data Python."""
    testo = valore.strip()
    if testo.startswith("+"):
        testo = testo[1:]
    return date.fromisoformat(testo[:10])


def _valore(voce, chiave):
    return voce.get(chiave, {}).get("value", "").strip()


def _persona_base(voce, oggi):
    """Estrae i campi comuni di una riga SPARQL, o None se non valida."""
    player = _valore(voce, "player")
    nome = _valore(voce, "playerLabel")
    nascita_raw = _valore(voce, "birthDate")
    morte_raw = _valore(voce, "deathDate")

    qid = player.rstrip("/").rsplit("/", 1)[-1]

    if not qid.startswith("Q") or not nome or nome == qid or not nascita_raw:
        return None

    try:
        nascita = _data_wikidata(nascita_raw)
    except ValueError:
        return None

    if nascita > oggi:
        return None

    morte = None
    if morte_raw:
        try:
            morte = _data_wikidata(morte_raw)
        except ValueError:
            morte = None

    return {
        "qid": qid,
        "name": nome,
        "birth_date": nascita.isoformat(),
        "age": oggi.year - nascita.year,
        "death_date": morte.isoformat() if morte else None,
        "death_year": morte.year if morte else None,
        "deceased": morte is not None,
        "source_url": f"https://www.wikidata.org/wiki/{qid}",
    }


def interpreta_risposta_wikidata(dati, oggi):
    """Normalizza, deduplica e ordina i risultati Wikidata (giocatori)."""
    risultati = []
    qid_visti = set()

    for voce in dati.get("results", {}).get("bindings", []):
        persona = _persona_base(voce, oggi)

        if persona is None or persona["qid"] in qid_visti:
            continue

        qid_visti.add(persona["qid"])
        persona["role"] = "giocatore"
        risultati.append(persona)

    return _ordina(risultati)


def _ordina(risultati):
    return sorted(
        risultati,
        key=lambda giocatore: (
            giocatore["name"].casefold(),
            giocatore["birth_date"],
        ),
    )


def _intero(valore):
    try:
        return int(float(valore))
    except (TypeError, ValueError):
        return None


def raggruppa_allenatori(dati, oggi):
    """Raggruppa le righe SPARQL per persona, unendo i vari incarichi.

    Restituisce una lista di dict con:
      - current: almeno un incarico senza data di fine
      - recent_start: incarico in corso con inizio >= SOGLIA_ANNO_INIZIO
      - wd_matches: somma di P1350 se TUTTI gli incarichi lo hanno, altrimenti None
      - it_title: titolo della voce su it.wikipedia (se esiste)
    """
    persone = {}

    for voce in dati.get("results", {}).get("bindings", []):
        persona = _persona_base(voce, oggi)

        if persona is None:
            continue

        info = persone.setdefault(
            persona["qid"],
            {
                "persona": persona,
                "incarichi": {},
                "it_title": "",
            },
        )

        titolo = _valore(voce, "itTitle")
        if titolo and not info["it_title"]:
            info["it_title"] = titolo

        id_incarico = _valore(voce, "coachStatement") or f"anon-{len(info['incarichi'])}"
        incarico = info["incarichi"].setdefault(
            id_incarico, {"matches": None, "start": None, "end": False}
        )

        partite = _intero(_valore(voce, "coachMatches"))
        if partite is not None:
            incarico["matches"] = partite

        inizio_raw = _valore(voce, "coachStart")
        if inizio_raw:
            try:
                incarico["start"] = _data_wikidata(inizio_raw)
            except ValueError:
                pass

        if _valore(voce, "coachEnd"):
            incarico["end"] = True

    risultato = []

    for qid, info in persone.items():
        incarichi = list(info["incarichi"].values())

        in_corso = [i for i in incarichi if not i["end"]]
        recent_start = any(
            i["start"] is not None and i["start"].year >= SOGLIA_ANNO_INIZIO
            for i in in_corso
        )

        if incarichi and all(i["matches"] is not None for i in incarichi):
            wd_matches = sum(i["matches"] for i in incarichi)
        else:
            wd_matches = None

        risultato.append(
            {
                "persona": info["persona"],
                "current": bool(in_corso),
                "recent_start": recent_start,
                "wd_matches": wd_matches,
                "it_title": info["it_title"],
            }
        )

    return risultato


# ---------------------------------------------------------------------------
# Partite da allenatore: it.wikipedia (wikitesto)
# ---------------------------------------------------------------------------

_RE_WDL = re.compile(r"\{\{\s*WDL(tot)?\s*\|\s*(\d+)\s*\|", re.IGNORECASE)
_RE_TOTALE_JUVE = re.compile(r"Totale\s+Juventus", re.IGNORECASE)
_RE_RIGA_JUVE = re.compile(r"Calcio\s+Juventus|Juventus\s+Football\s+Club", re.IGNORECASE)


def estrai_partite_juventus(wikitesto):
    """Legge dal wikitesto le partite totali da allenatore alla Juventus.

    Nella tabella "Statistiche da allenatore" ogni riga contiene
    {{WDL|G|V|N|P}} (G = partite totali di tutte le competizioni) e la riga
    di riepilogo contiene {{WDLtot|G|V|N|P}} con "Totale Juventus".

    - Se esiste la riga "Totale Juventus": si usa quella (copre più stagioni
      e più cicli).
    - Altrimenti si sommano le righe Juventus (caso di una sola stagione).
    Restituisce None se il dato non è presente.
    """
    totale = None
    somma = 0

    for riga in re.split(r"\n\|-", wikitesto):
        m = _RE_WDL.search(riga)

        if not m:
            continue

        valore = int(m.group(2))

        if m.group(1):
            if _RE_TOTALE_JUVE.search(riga):
                totale = valore if totale is None else max(totale, valore)
        elif _RE_RIGA_JUVE.search(riga):
            somma += valore

    if totale is not None:
        return totale

    return somma or None


def partite_da_wikipedia(titolo):
    """Scarica il wikitesto della voce italiana e restituisce le partite.

    None = dato non disponibile (errore di rete o tabella assente).
    """
    parametri = urllib.parse.urlencode({"title": titolo, "action": "raw"})
    richiesta = urllib.request.Request(
        f"{WIKIPEDIA_IT_BASE}/w/index.php?{parametri}",
        headers={"User-Agent": WIKIDATA_USER_AGENT},
    )

    for tentativo in range(1, 3):
        try:
            with urllib.request.urlopen(richiesta, timeout=WIKIPEDIA_TIMEOUT) as risposta:
                testo = risposta.read().decode("utf-8", errors="replace")
            return estrai_partite_juventus(testo)
        except (OSError, TimeoutError) as exc:
            print(f"Wikipedia non ha risposto per '{titolo}' ({tentativo}/2): {exc}")
            if tentativo < 2 and WIKIDATA_ATTESA_RETRY:
                time.sleep(WIKIDATA_ATTESA_RETRY)

    return None


def seleziona_allenatori(candidati, recupera_partite=partite_da_wikipedia):
    """Applica la soglia di partite e restituisce gli allenatori da includere.

    Regole, in ordine:
      1) partite note su Wikidata (P1350) >= soglia            -> incluso
      2) incarico in corso con data di inizio recente          -> incluso
      3) altrimenti si legge it.wikipedia: partite >= soglia   -> incluso
    Se il dato non si trova, l'allenatore viene scartato.
    """
    risultati = []

    for cand in candidati:
        persona = cand["persona"]
        incluso = False

        if cand["wd_matches"] is not None and cand["wd_matches"] >= SOGLIA_PARTITE_ALLENATORE:
            incluso = True
        elif cand["current"] and cand["recent_start"]:
            incluso = True
        elif cand["it_title"]:
            partite = recupera_partite(cand["it_title"])
            if partite is None:
                print(
                    f"Partite non trovate per {persona['name']} "
                    f"({cand['it_title']}): scartato."
                )
            else:
                incluso = partite >= SOGLIA_PARTITE_ALLENATORE
        else:
            print(f"Nessuna voce it.wikipedia per {persona['name']}: scartato.")

        if incluso:
            persona["role"] = "allenatore"
            risultati.append(persona)

    return risultati


# ---------------------------------------------------------------------------
# Rete
# ---------------------------------------------------------------------------

def _esegui_sparql(query):
    parametri = urllib.parse.urlencode(
        {
            "query": query,
            "format": "json",
        }
    )

    richiesta = urllib.request.Request(
        f"{WIKIDATA_ENDPOINT}?{parametri}",
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": WIKIDATA_USER_AGENT,
        },
    )

    ultimo_errore = None

    for tentativo in range(1, WIKIDATA_MAX_TENTATIVI + 1):
        try:
            with urllib.request.urlopen(richiesta, timeout=WIKIDATA_TIMEOUT) as risposta:
                return json.loads(risposta.read().decode("utf-8"))

        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            ultimo_errore = exc

            if tentativo >= WIKIDATA_MAX_TENTATIVI:
                break

            print(
                f"Wikidata non ha risposto al tentativo "
                f"{tentativo}/{WIKIDATA_MAX_TENTATIVI}: {exc}. "
                f"Riprovo tra {WIKIDATA_ATTESA_RETRY}s..."
            )

            if WIKIDATA_ATTESA_RETRY:
                time.sleep(WIKIDATA_ATTESA_RETRY)

    raise RuntimeError(
        f"Wikidata non disponibile dopo {WIKIDATA_MAX_TENTATIVI} tentativi: "
        f"{ultimo_errore}"
    ) from ultimo_errore


def recupera_compleanni(oggi):
    """Interroga Wikidata per i compleanni Juventus (giocatori + allenatori)."""
    dati_giocatori = _esegui_sparql(costruisci_query_wikidata(oggi.day, oggi.month))
    giocatori = interpreta_risposta_wikidata(dati_giocatori, oggi)

    dati_allenatori = _esegui_sparql(costruisci_query_allenatori(oggi.day, oggi.month))
    candidati = raggruppa_allenatori(dati_allenatori, oggi)
    allenatori = seleziona_allenatori(candidati)

    # Chi è sia giocatore sia allenatore compare una sola volta.
    qid_presenti = {g["qid"] for g in giocatori}
    allenatori = [a for a in allenatori if a["qid"] not in qid_presenti]

    return _ordina(giocatori + allenatori)


# ---------------------------------------------------------------------------
# Storico
# ---------------------------------------------------------------------------

def carica_storico(percorso=PERCORSO_STORICO):
    """Legge lo storico; un file assente equivale a uno storico vuoto."""
    percorso = Path(percorso)

    if not percorso.exists():
        return {}

    try:
        dati = json.loads(percorso.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"Storico compleanni non leggibile: {percorso}"
        ) from exc

    if not isinstance(dati, dict):
        raise RuntimeError("Formato dello storico compleanni non valido.")

    # Compatibilità con il vecchio formato con version/sent_dates.
    if "sent_dates" in dati:
        date_inviate = dati.get("sent_dates", [])

        if not isinstance(date_inviate, list):
            raise RuntimeError(
                "Formato dello storico compleanni non valido."
            )

        return {
            str(voce.get("date")): voce.get("players", [])
            for voce in date_inviate
            if isinstance(voce, dict)
            and voce.get("date")
            and isinstance(voce.get("players", []), list)
        }

    return {
        str(data_invio): giocatori
        for data_invio, giocatori in dati.items()
        if isinstance(giocatori, list)
    }


def gia_inviato(oggi, storico):
    return oggi.isoformat() in storico


def salva_storico(
    oggi,
    giocatori,
    percorso=PERCORSO_STORICO,
):
    """Registra l'invio soltanto dopo la conferma di Telegram."""
    percorso = Path(percorso)
    storico = carica_storico(percorso)

    data_minima = oggi - timedelta(
        days=GIORNI_CONSERVAZIONE_STORICO - 1
    )

    date_inviate = {}

    for data_testo, giocatori_salvati in storico.items():
        try:
            data_invio = date.fromisoformat(str(data_testo))
        except ValueError:
            continue

        if data_minima <= data_invio < oggi:
            date_inviate[data_invio.isoformat()] = giocatori_salvati

    date_inviate[oggi.isoformat()] = [
        {
            "name": giocatore["name"],
            "age": giocatore["age"],
        }
        for giocatore in giocatori
    ]

    date_inviate = dict(sorted(date_inviate.items()))

    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(
        json.dumps(
            date_inviate,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------

def formatta_messaggio(giocatori, oggi):
    data_italiana = f"{oggi.day} {MESI_ITALIANI[oggi.month - 1]}"

    righe = [
        f"<b>🎂 COMPLEANNI | {data_italiana}</b>",
        "",
    ]

    # Prima i vivi, poi i giocatori scomparsi.
    ordinati = sorted(
        giocatori,
        key=lambda giocatore: (
            bool(giocatore.get("deceased")),
            giocatore["name"].casefold(),
        ),
    )

    for giocatore in ordinati:
        nome = html.escape(giocatore["name"])
        eta = giocatore["age"]
        emoji = "🕊️" if giocatore.get("deceased") else "🎉"
        ruolo = " (allenatore)" if giocatore.get("role") == "allenatore" else ""
        righe.append(f"{emoji} <b>{nome}</b>{ruolo} — {eta} anni")

    return "\n".join(righe)


def invia_a_telegram(testo):
    """Invia soltanto alla chat privata configurata in TELEGRAM_TO_BOT."""
    token = os.environ.get("TELEGRAM_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_TO_BOT", "").strip()

    mancanti = []
    if not token:
        mancanti.append("TELEGRAM_TOKEN")
    if not chat_id:
        mancanti.append("TELEGRAM_TO_BOT")

    if mancanti:
        raise RuntimeError(
            "Mancano variabili d'ambiente: " + ", ".join(mancanti)
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": testo,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
    ).encode("utf-8")

    richiesta = urllib.request.Request(url, data=payload)

    with urllib.request.urlopen(richiesta, timeout=30) as risposta:
        dati = json.loads(risposta.read().decode("utf-8"))

    if not dati.get("ok"):
        raise RuntimeError(
            f"Telegram ha rifiutato il messaggio: {dati}"
        )

    return dati


def main():
    oggi = datetime.now(FUSO_ORARIO).date()

    storico = carica_storico()

    if gia_inviato(oggi, storico):
        print(
            "Compleanni già inviati oggi: nessun nuovo messaggio."
        )
        return

    print(
        "Cerco i compleanni della rosa attuale, degli ex Juventus "
        "e degli allenatori rilevanti..."
    )

    giocatori = recupera_compleanni(oggi)

    if not giocatori:
        print("Nessun compleanno Juventus oggi: nessun invio.")
        return

    messaggio = formatta_messaggio(giocatori, oggi)

    print(f"Compleanni trovati: {len(giocatori)}. Invio a Telegram...")
    invia_a_telegram(messaggio)

    salva_storico(
        oggi,
        giocatori,
    )

    print("Compleanni inviati con successo e registrati nello storico!")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print()
        print("=" * 64)
        print("ESITO FINALE COMPLEANNI: ERRORE")
        print("=" * 64)
        print(f"Motivo: {exc}")
        print("Telegram: invio non confermato.")
        print("Storico: non aggiornato.")
        print("=" * 64)
        raise SystemExit(1)
