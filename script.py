import os
import re
import time
import random
import urllib.request
import urllib.parse
from datetime import datetime
from zoneinfo import ZoneInfo
from google.genai import Client
from google.genai import types

# Orario esatto (ora italiana) in cui deve iniziare la generazione/invio.
# Il trigger esterno (cron-job.org) puo' avviare il job qualche minuto prima
# come buffer anti-ritardo: lo script aspetta comunque fino a questo orario.
ORA_INVIO = (7, 30)  # (ore, minuti) - modifica qui se vuoi cambiare orario
FUSO_ORARIO = ZoneInfo("Europe/Rome")


def attendi_orario_preciso(ora, minuto, fuso, margine_massimo_minuti=15):
    """Aspetta fino a ora:minuto (ora italiana). Se il workflow parte in ritardo
    oltre il margine massimo, procede subito senza aspettare oltre."""
    ora_corrente = datetime.now(fuso)
    target = ora_corrente.replace(hour=ora, minute=minuto, second=0, microsecond=0)

    secondi_attesa = (target - ora_corrente).total_seconds()

    if secondi_attesa <= 0:
        return

    if secondi_attesa > margine_massimo_minuti * 60:
        print(f"Attesa di {secondi_attesa/60:.1f} minuti fuori dal margine, procedo subito.")
        return

    print(f"Attendo {secondi_attesa:.0f} secondi per raggiungere le {ora:02d}:{minuto:02d} ora italiana...")
    time.sleep(secondi_attesa)

def converti_anno_in_emoji(testo):
    """Trova gli anni a 4 cifre nel testo e li converte in numeri emoji quadrate (es. 1️⃣9️⃣7️⃣3️⃣)"""
    emoji_numeri = {
        '0': '0️⃣', '1': '1️⃣', '2': '2️⃣', '3': '3️⃣', '4': '4️⃣',
        '5': '5️⃣', '6': '6️⃣', '7': '7️⃣', '8': '8️⃣', '9': '9️⃣'
    }

    def rimpiazza(match):
        anno = match.group(0)
        return "".join(emoji_numeri[c] for c in anno)

    return re.sub(r'\b\d{4}\b', rimpiazza, testo)

def converti_markdown_in_html(testo):
    """Converte la formattazione Markdown di Gemini in tag HTML per Telegram."""
    # **testo** → <b>testo</b>
    testo = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', testo)
    # _testo_ → <i>testo</i> (solo se non è parte di una parola)
    testo = re.sub(r'(?<!\w)_(.*?)_(?!\w)', r'<i>\1</i>', testo, flags=re.DOTALL)
    return testo

def chiama_gemini_con_retry(client, model, prompt, config, max_retries=5):
    """Chiama l'API Gemini con retry esponenziale in caso di 503."""
    for attempt in range(max_retries):
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=config
            )
            return response
        except Exception as e:
            errore = str(e)
            if "503" in errore or "UNAVAILABLE" in errore:
                if attempt < max_retries - 1:
                    wait = (2 ** attempt) + random.uniform(0, 1)
                    print(f"Tentativo {attempt + 1}/{max_retries} fallito (503). Riprovo tra {wait:.1f}s...")
                    time.sleep(wait)
                else:
                    raise Exception(f"Gemini non disponibile dopo {max_retries} tentativi.") from e
            else:
                raise

def ottieni_accade_oggi():
    oggi = datetime.now()
    mesi_ita = {
        1: "GENNAIO", 2: "FEBBRAIO", 3: "MARZO", 4: "APRILE", 5: "MAGGIO", 6: "GIUGNO",
        7: "LUGLIO", 8: "AGOSTO", 9: "SETTEMBRE", 10: "OTTOBRE", 11: "NOVEMBRE", 12: "DICEMBRE"
    }
    data_italiana = f"{oggi.day} {mesi_ita[oggi.month]}".upper()

    client = Client()

    system_instruction = """
    Sei il redattore della pagina Juventus Reborn. Scrivi la rubrica quotidiana "ACCADDE OGGI".
    
    Regole tassative di selezione, stile e formattazione HTML per Telegram:
    - Usa la ricerca Google per VERIFICARE che ogni evento sia realmente accaduto nel giorno esatto indicato. Non riportare mai un evento senza averne confermato la data sul web. Se non riesci a confermare la data, scarta l'evento.
    - Cerca eventi storici della Juventus accaduti in questo giorno, come: vittorie di trofei/scudetti, grandi record di squadra e PARTITE STORICHE (es. grandi rimonte, vittorie memorabili o storici big match).
    - ESCLUSIONI ASSOLUTE: NON inserire mai sconfitte, eliminazioni, o risultati negativi per la Juventus. Solo eventi in cui la Juventus ha vinto, conquistato un trofeo o stabilito un record positivo.
    - SOLO PRIMA SQUADRA MASCHILE: Considera ESCLUSIVAMENTE eventi della prima squadra maschile della Juventus. È TASSATIVAMENTE VIETATO inserire eventi riguardanti Juventus Women (femminile), Juventus Next Gen / Under 23, Primavera, e qualsiasi squadra giovanile o del settore femminile/giovanile. Se un evento riguarda una di queste squadre, scartalo sempre.
    - TASSETTO: NON INSERIRE MAI I COMPLEANNI di giocatori, ex giocatori o allenatori. Sono totalmente vietati.
    - Di tutti gli eventi validi trovati (esclusi i compleanni e le sconfitte), seleziona e inserisci RIGOROSAMENTE un MASSIMO DI 3 EVENTI in totale (i più importanti, iconici e significativi).
    - Se in questo giorno NON ci sono eventi storici di rilievo sul campo per la Juventus, rispondi scrivendo esclusivamente la parola: VUOTO
    - Se invece ci sono eventi importanti, NON inserire il titolo principale del post e inizia direttamente con il primo evento seguendo questa struttura:
      
      ANNO - <b>Titolo dell'Evento in Grassetto</b>
      <i>Descrizione molto breve di massimo due righe.</i>
      
    - REGOLA PER IL TITOLO: Il titolo in grassetto deve essere super sintetico, un flash di massimo 3 o 4 parole (Es: "Trionfo in Coppa Italia", "Rimonta pazzesca", "Scudetto numero 22").
    - REGOLA PER LA DESCRIZIONE: L'intera descrizione sotto al titolo deve essere racchiusa UNICAMENTE tra i tag <i> e </i>. Non inserire MAI tag di grassetto (<b>) all'interno della descrizione, nemmeno per i nomi di giocatori o allenatori. Deve essere tutto esclusivamente in corsivo pulito.
    - NON usare MAI la sintassi Markdown (asterischi, underscore). Usa esclusivamente tag HTML: <b> per il grassetto e <i> per il corsivo.
    - NON inserire MAI link, URL, fonti, note o citazioni nel testo, anche se hai usato la ricerca per verificare gli eventi. L'output deve contenere solo gli eventi nel formato richiesto.
    - Lascia una riga vuota tra la descrizione di un evento e l'inizio di quello successivo.
    - Sii storicamente preciso e ordinali dal più vecchio al più recente.
    """

    prompt = f"Trova le partite storiche VINTE, i trofei o i record positivi della PRIMA SQUADRA MASCHILE della Juventus accaduti il giorno {data_italiana} (NO COMPLEANNI, NO SCONFITTE, NO ELIMINAZIONI, NO Women, NO Next Gen/Under 23, NO Primavera o giovanili) e inserisci i 3 più importanti nel formato richiesto. Verifica la data di ogni evento con la ricerca prima di includerlo. La descrizione deve essere solo in corsivo senza grassetti."

    # Grounding con Google Search: il modello verifica gli eventi sul web
    # invece di affidarsi alla memoria, riducendo drasticamente le date sbagliate.
    grounding_tool = types.Tool(
        google_search=types.GoogleSearch()
    )

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        tools=[grounding_tool],
    )

    response = chiama_gemini_con_retry(client, 'gemini-2.5-flash', prompt, config)

    testo_gemini = response.text.strip()

    if testo_gemini.upper() == "VUOTO" or not testo_gemini:
        return None

    # Prima converti il Markdown in HTML (prima degli anni, per non confondere le regex)
    testo_gemini = converti_markdown_in_html(testo_gemini)

    testo_formattato = converti_anno_in_emoji(testo_gemini)

    titolo_principale = f"<b>👀🔙 ACCADDE OGGI | {data_italiana}</b>\n\n"
    firma_finale = "\n\n👉 @Juventus_Reborn"

    return f"{titolo_principale}{testo_formattato}{firma_finale}"

def invia_a_telegram(testo):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = {
        'chat_id': chat_id,
        'text': testo,
        'parse_mode': 'HTML'
    }

    data = urllib.parse.urlencode(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data)

    with urllib.request.urlopen(req) as response:
        return response.read()

if __name__ == "__main__":
    if not all([os.environ.get("GEMINI_API_KEY"), os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")]):
        print("Errore: Mancano variabili d'ambiente.")
        exit(1)

    try:
        attendi_orario_preciso(ORA_INVIO[0], ORA_INVIO[1], FUSO_ORARIO)

        print("Generazione testo personalizzato (HTML)...")
        rubrica = ottieni_accade_oggi()

        if rubrica is None:
            print("Nessun evento importante trovato per oggi. L'invio a Telegram è stato annullato.")
        else:
            print("Invio a Telegram...")
            invia_a_telegram(rubrica)
            print("Inviato con successo!")

    except Exception as e:
        print(f"Errore: {e}")
