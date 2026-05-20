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
    oggi = datetime.now()
    mesi_ita = {
        1: "GENNAIO", 2: "FEBBRAIO", 3: "MARZO", 4: "APRILE", 5: "MAGGIO", 6: "GIUGNO",
        7: "LUGLIO", 8: "AGOSTO", 9: "SETTEMBRE", 10: "OTTOBRE", 11: "NOVEMBRE", 12: "DICEMBRE"
    }
    # Estrae la data e forza il mese in tutto maiuscolo
    data_italiana = f"{oggi.day} {mesi_ita[oggi.month]}".upper()

    client = Client()

    system_instruction = """
    Sei il redattore della pagina Juventus Reborn. Scrivi la rubrica quotidiana "ACCADDE OGGI".
    
    Regole tassative di selezione, stile e formattazione HTML per Telegram:
    - Seleziona al massimo 2 o 3 eventi storici DAVVERO principali, iconici e importanti del giorno (es. vittorie di trofei, compleanni di leggende assulte, partite storiche).
    - Se in questo giorno NON ci sono eventi storici di rilievo per la Juventus, rispondi scrivendo esclusivamente la parola: VUOTO
    - Se invece ci sono eventi importanti, NON inserire il titolo principale del post e inizia direttamente con il primo evento seguendo questa struttura:
      
      ANNO - <b>Titolo dell'Evento in Grassetto</b>
      <i>Descrizione molto breve di massimo due righe. Metti in grassetto (<b>nome</b>) solo i protagonisti.</i>
      
    - L'intera descrizione deve essere racchiusa tra i tag <i> e </i> per essere in corsivo.
    - Lascia una riga vuota tra la descrizione di un evento e l'inizio di quello successivo.
    - Sii storicamente preciso.
    """

    prompt = f"Trova e descrivi in modo sintetico gli eventi più importanti accaduti il giorno {data_italiana} nella storia della Juventus. Se non c'è nulla di rilevante scrivi solo VUOTO."

    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.1,
        )
    )

    testo_gemini = response.text.strip()
    
    if testo_gemini.upper() == "VUOTO" or not testo_gemini:
        return None
    
    testo_formattato = converti_anno_in_emoji(testo_gemini)

    # Titolo aggiornato con la barra dritta e mese in maiuscolo
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
