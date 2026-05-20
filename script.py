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
    data_italiana = f"{oggi.day} {mesi_ita[oggi.month]}".upper()

    client = Client()

    # Istruzioni aggiornate: SOLO CORSIVO nella descrizione, nessun grassetto all'interno
    system_instruction = """
    Sei il redattore della pagina Juventus Reborn. Scrivi la rubrica quotidiana "ACCADDE OGGI".
    
    Regole tassative di selezione, stile e formattazione HTML per Telegram:
    - Cerca eventi storici della Juventus accaduti in questo giorno, come: vittorie di trofei/scudetti, grandi record di squadra e PARTITE STORICHE (es. grandi rimonte, vittorie memorabili o storici big match).
    - TASSETTO: NON INSERIRE MAI I COMPLEANNI di giocatori, ex giocatori o allenatori. Sono totalmente vietati.
    - Di tutti gli eventi validi trovati (esclusi i compleanni), seleziona e inserisci RIGOROSAMENTE un MASSIMO DI 3 EVENTI in totale (i più importanti, iconici e significativi).
    - Se in questo giorno NON ci sono eventi storici di rilievo sul campo per la Juventus, rispondi scrivendo esclusivamente la parola: VUOTO
    - Se invece ci sono eventi importanti, NON inserire il titolo principale del post e inizia direttamente con il primo evento seguendo questa struttura:
      
      ANNO - <b>Titolo dell'Evento in Grassetto</b>
      <i>Descrizione molto breve di massimo due righe.</i>
      
    - REGOLA PER IL TITOLO: Il titolo in grassetto deve essere super sintetico, un flash di massimo 3 o 4 parole (Es: "Trionfo in Coppa Italia", "Rimonta pazzesca", "Scudetto numero 22").
    - REGOLA PER LA DESCRIZIONE: L'intera descrizione sotto al titolo deve essere racchiusa UNICAMENTE tra i tag <i> e </i>. Non inserire MAI tag di grassetto (<b>) all'interno della descrizione, nemmeno per i nomi di giocatori o allenatori. Deve essere tutto esclusivamente in corsivo pulito.
    - Lascia una riga vuota tra la descrizione di un evento e l'inizio di quello successivo.
    - Sii storicamente preciso e ordinali dal più vecchio al più recente.
    """

    prompt = f"Trova le partite storiche, i trofei o i record di squadra accaduti il giorno {data_italiana} nella storia della Juventus (NO COMPLEANNI) e inserisci i 3 più importanti nel formato richiesto. La descrizione deve essere solo in corsivo senza grassetti."

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
