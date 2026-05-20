import os
import re
import json
import urllib.request
import urllib.parse
from datetime import datetime
from google.genai import Client
from google.genai import types

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

def cerca_immagine_google(query):
    """Cerca un'immagine su Google Custom Search e restituisce l'URL del primo risultato"""
    cx = os.environ.get("GOOGLE_SEARCH_CX")
    key = os.environ.get("GOOGLE_SEARCH_KEY")
    
    params = {
        'q': query,
        'cx': cx,
        'key': key,
        'searchType': 'image',
        'num': 1,
        'safe': 'active'
    }
    url = f"https://www.googleapis.com/customsearch/v1?{urllib.parse.urlencode(params)}"
    
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            if 'items' in data and len(data['items']) > 0:
                return data['items'][0]['link']
    except Exception as e:
        print(f"Errore durante la ricerca dell'immagine: {e}")
    return None

def ottieni_accade_oggi():
    # Recupera la data odierna ed imposta il mese in italiano
    oggi = datetime.now()
    mesi_ita = {
        1: "GENNAIO", 2: "FEBBRAIO", 3: "MARZO", 4: "APRILE", 5: "MAGGIO", 6: "GIUGNO",
        7: "LUGLIO", 8: "AGOSTO", 9: "SETTEMBRE", 10: "OTTOBRE", 11: "NOVEMBRE", 12: "DICEMBRE"
    }
    data_italiana = f"{oggi.day} {mesi_ita[oggi.month]}"

    client = Client()

    # Istruzioni tassative per ottenere una struttura JSON pulita e HTML valido
    system_instruction = """
    Sei il redattore della pagina Juventus Reborn. Scrivi la rubrica quotidiana "ACCADDE OGGI".
    
    Devi restituire l'output strutturato rigorosamente in formato JSON valido con queste due chiavi:
    1. "testo": Il contenuto del post formattato in HTML per Telegram. Segui queste regole di stile:
       - NON inserire alcun titolo principale del post (es. "ACCADDE OGGI"). Inizia subito con il primo evento storico.
       - Seleziona al massimo 2 o 3 eventi storici principali accaduti in questo giorno.
       - Struttura riga per riga di ogni evento:
         ANNO - <b>Titolo Breve in Grassetto</b>\\n<i>Descrizione breve, massima sintesi in due righe. Metti in grassetto (<b>Nome</b>) solo i protagonisti importanti all'interno del testo.</i>
       - Ricorda di racchiudere l'intera riga descrittiva tra i tag <i> e </i> per renderla completamente in corsivo.
       - Lascia sempre una riga vuota tra un blocco evento e quello successivo.
    2. "query_ricerca": Una stringa di parole chiave (breve e mirata) per trovare su Google Immagini la foto storica d'archivio legata all'evento più importante del giorno (Es: "Juventus 1973 scudetto Cuccureddu" o "Del Piero addio Juventus 2012").
    
    Sii storicamente preciso. Non inventare eventi o date. Restituisci esclusivamente il JSON puro, senza blocchi di codice markdown.
    """

    prompt = f"Trova gli eventi del giorno {data_italiana} nella storia della Juventus e genera il JSON richiesto."

    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
            response_mime_type="application/json"
        )
    )

    data_json = json.loads(response.text.strip())
    
    # Converte gli anni testuali (es. 1973) in numeri racchiusi dentro le emoji (1️⃣9️⃣7️⃣3️⃣)
    testo_pulito = converti_anno_in_emoji(data_json["testo"])
    
    # Compone il blocco definitivo con intestazione in grassetto e firma del canale
    titolo_principale = f"<b>👀🔙 ACCADDE OGGI - {data_italiana}</b>\n\n"
    firma_finale = "\n\n👉 @Juventus_Reborn"
    
    testo_completo = f"{titolo_principale}{testo_pulito}{firma_finale}"
    
    return testo_completo, data_json["query_ricerca"]

def invia_foto_telegram(testo, url_foto):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    # Se la ricerca Google ha trovato un'immagine valida usa sendPhoto, altrimenti ripiega sul testo semplice
    if url_foto:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        payload = {
            'chat_id': chat_id,
            'photo': url_foto,
            'caption': testo,
            'parse_mode': 'HTML'
        }
    else:
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
        print("Errore: Variabili d'ambiente di Telegram o Gemini mancanti.")
        exit(1)

    try:
        print("Generazione testo e query con Gemini...")
        rubrica, query_foto = ottieni_accade_oggi()
        print(f"Query di ricerca generata con successo: '{query_foto}'")
        
        url_immagine = None
        if os.environ.get("GOOGLE_SEARCH_CX") and os.environ.get("GOOGLE_SEARCH_KEY"):
            print("Esecuzione ricerca immagine su Google...")
            url_immagine = cerca_immagine_google(query_foto)
            print(f"URL immagine trovato: {url_immagine}")
        
        print("Invio del post su Telegram...")
        invia_foto_telegram(rubrica, url_immagine)
        print("Procedura completata con successo!")
        
    except Exception as e:
        print(f"Errore generale del processo: {e}")
