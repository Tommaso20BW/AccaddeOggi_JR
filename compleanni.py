import html
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo


FUSO_ORARIO = ZoneInfo("Europe/Rome")
WIKIDATA_ENDPOINT = "https://query.wikidata.org/sparql"
WIKIDATA_TIMEOUT = max(
    5,
    int(os.environ.get("WIKIDATA_TIMEOUT", "30")),
)
WIKIDATA_USER_AGENT = (
    "AccaddeOggi-Juventus-Birthday-Bot/1.0 "
    "(https://github.com/Tommaso20BW/AccaddeOggi_JR)"
)
JUVENTUS_QID = "Q1422"

# Gli attuali giocatori della Juventus vengono sempre inclusi.
# Gli ex vengono inclusi solo se hanno almeno questa soglia di presenze.
SOGLIA_PRESENZE_EX = 50

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


def costruisci_query_wikidata(giorno, mese):
    """Crea la query per rosa attuale ed ex Juventus rilevanti."""
    return f"""
SELECT DISTINCT ?player ?playerLabel ?birthDate WHERE {{
  ?player wdt:P31 wd:Q5;
          p:P54 ?juveStatement;
          p:P569 ?birthStatement.

  ?juveStatement ps:P54 wd:{JUVENTUS_QID};
                 wikibase:rank ?juveRank.

  OPTIONAL {{ ?juveStatement pq:P1350 ?juveMatches. }}

  ?birthStatement psv:P569 ?birthNode;
                  wikibase:rank ?birthRank.
  ?birthNode wikibase:timeValue ?birthDate;
             wikibase:timePrecision ?birthPrecision.

  FILTER(?juveRank != wikibase:DeprecatedRank)

  # Passa se:
  # 1) è ancora alla Juventus: nessuna data di fine nel rapporto col club;
  # 2) è un ex con almeno SOGLIA_PRESENZE_EX presenze.
  FILTER(
    NOT EXISTS {{ ?juveStatement pq:P582 ?juveEnd. }}
    ||
    (
      BOUND(?juveMatches)
      && ?juveMatches >= {SOGLIA_PRESENZE_EX}
    )
  )

  FILTER(?birthRank != wikibase:DeprecatedRank)
  FILTER(?birthPrecision >= 11)
  FILTER(MONTH(?birthDate) = {int(mese)})
  FILTER(DAY(?birthDate) = {int(giorno)})
  FILTER NOT EXISTS {{ ?player wdt:P570 ?deathDate. }}

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


def interpreta_risposta_wikidata(dati, oggi):
    """Normalizza, deduplica e ordina i risultati Wikidata."""
    risultati = []
    qid_visti = set()

    for voce in dati.get("results", {}).get("bindings", []):
        player = voce.get("player", {}).get("value", "").strip()
        nome = voce.get("playerLabel", {}).get("value", "").strip()
        nascita_raw = voce.get("birthDate", {}).get("value", "").strip()
        qid = player.rstrip("/").rsplit("/", 1)[-1]

        if (
            not qid.startswith("Q")
            or not nome
            or nome == qid
            or not nascita_raw
            or qid in qid_visti
        ):
            continue

        try:
            nascita = _data_wikidata(nascita_raw)
        except ValueError:
            continue

        if nascita > oggi:
            continue

        qid_visti.add(qid)
        risultati.append(
            {
                "qid": qid,
                "name": nome,
                "birth_date": nascita.isoformat(),
                "age": oggi.year - nascita.year,
                "source_url": f"https://www.wikidata.org/wiki/{qid}",
            }
        )

    return sorted(
        risultati,
        key=lambda giocatore: (
            giocatore["name"].casefold(),
            giocatore["birth_date"],
        ),
    )


def recupera_compleanni(oggi):
    """Interroga Wikidata per i compleanni Juventus della data indicata."""
    query = costruisci_query_wikidata(oggi.day, oggi.month)
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

    with urllib.request.urlopen(
        richiesta,
        timeout=WIKIDATA_TIMEOUT,
    ) as risposta:
        dati = json.loads(risposta.read().decode("utf-8"))

    return interpreta_risposta_wikidata(dati, oggi)


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


def formatta_messaggio(giocatori, oggi):
    data_italiana = f"{oggi.day} {MESI_ITALIANI[oggi.month - 1]}"
    righe = [
        f"<b>🎂 COMPLEANNI BIANCONERI | {data_italiana}</b>",
    ]

    for giocatore in giocatori:
        nome = html.escape(giocatore["name"])
        eta = giocatore["age"]
        righe.append(f"🎉 <b>{nome}</b> — {eta} anni")

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
        "Cerco i compleanni della rosa attuale "
        "e degli ex Juventus rilevanti..."
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
