import os
import re
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

def ottieni_accade_oggi():
    # 1. Recupera la data di oggi e imposta i mesi in italiano
    oggi = datetime.now()
    mesi_ita = {
        1: "GENNAIO", 2: "FEBBRAIO", 3: "MARZO", 4: "APRILE", 5: "MAGGIO", 6: "GIUGNO",
        7: "LUGLIO", 8: "AGOSTO", 9: "SETTEMBRE", 10: "OTTOBRE", 11: "NOVEMBRE", 12: "DICEMBRE"
    }
    data_italiana = f"{oggi.day} {mesi_ita[oggi.month]}"

    client = Client()

    # Istruzioni tassative per evitare testi appiccicati e forzare il corsivo su Telegram
    system_instruction = """
    Sei il redattore della pagina Juventus Reborn. Scrivi la rubrica quotidiana "ACCADDE OGGI".
    
    Regole tassative di stile e formattazione per Telegram:
    - NON inserire il titolo principale del post (es. ACCADDE OGGI). Inizia direttamente con il primo evento.
    - Seleziona al massimo 2 o 3 eventi storici principali del giorno.
    - Ogni evento deve seguire rigorosamente questa struttura a due righe separate (usa un a capo netto tra titolo e descrizione):
      
      ANNO - *Titolo dell'Evento in Grassetto*
      _Descrizione molto breve e stringata di massimo due righe. Metti in grassetto (*) solo i nomi dei protagonisti._
      
    - Nota bene: la descrizione DEVE essere racchiusa tra trattini bassi (_) per apparire interamente in corsivo.
    - Lascia sempre una riga vuota tra la descrizione di un evento e l'inizio di quello successivo.
    - Sii storicamente preciso: non inventare mai date, trasferimenti o risultati.
    """

    prompt = f"Trova e descrivi in modo sintetico e ben spaziato gli eventi più importanti accaduti il giorno {data_italiana} nella storia della Juventus."

    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
        )
    )

    testo_gemini = response.text.strip()
    
    # Applica la conversione degli anni (es. 1973 -> 1️⃣9️⃣7️⃣3️⃣)
    testo_formattato = converti_anno_in_emoji(testo_gemini)

    # Costruisce il messaggio finale inserendo il titolo principale in bold e la firma
    titolo_principale = f"*👀🔙 ACCADDE OGGI - {data_italiana}*\n\n"
    firma_finale = "\n\n👉 @Juventus_Reborn"
    
    return f"{titolo_principale}{testo_formattato}{firma_finale}"

def invia_a_telegram(testo):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    payload = {
        'chat_id': chat_id,
        'text': testo,
        'parse_mode': 'Markdown'
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
        print("Generazione testo personalizzato...")
        rubrica = ottieni_accade_oggi()
        
        print("Invio a Telegram...")
        invia_a_telegram(rubrica)
        print("Inviato con successo con la formattazione corretta!")
        
    except Exception as e:
        print(f"Errore: {e}")
