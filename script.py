import os
import urllib.request
import urllib.parse
from datetime import datetime
from google.genai import Client
from google.genai import types

def ottieni_accade_oggi():
    # 1. Recupera la data di oggi e imposta i mesi in italiano
    oggi = datetime.now()
    mesi_ita = {
        1: "Gennaio", 2: "Febbraio", 3: "Marzo", 4: "Aprile", 5: "Maggio", 6: "Giugno",
        7: "Luglio", 8: "Agosto", 9: "Settembre", 10: "Ottobre", 11: "Novembre", 12: "Dicembre"
    }
    data_italiana = f"{oggi.day} {mesi_ita[oggi.month]}"

    client = Client()

    system_instruction = """
    Sei il redattore capo di una famosa pagina social dedicata alla Juventus. 
    Il tuo compito è scrivere la rubrica quotidiana "ACCADE OGGI NELLA STORIA DELLA JUVENTUS".
    
    Regole di stile e formattazione:
    - Inizia sempre il post con il titolo: "⚪️⚫️ *ACCADE OGGI – [DATA DI OGGI]* ⚪️⚫️"
    - Sii epico, elegante, ma conciso. Usa uno stile da storytelling sportivo.
    - Separa i vari eventi chiaramente lasciando righe vuote.
    - Usa il grassetto di Markdown (*) per evidenziare i nomi dei protagonisti (giocatori, allenatori), i risultati, i marcatori o gli anni. (Es. *1973*, *Alessandro Del Piero*).
    - Ordina gli eventi dal più vecchio al più recente.
    - Se in questo giorno non ci sono stati trofei o partite storiche, cerca compleanni di leggende bianconere, debutti memorabili o gol iconici.
    - Chiudi sempre il post con il motto: "_Fino alla fine._"
    - Sii storicamente preciso: non inventare mai date, trasferimenti o risultati.
    """

    prompt = f"Trova e descrivi gli eventi più importanti accaduti il giorno {data_italiana} nella storia della Juventus."

    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2,
        )
    )

    return response.text

def invia_a_telegram(testo):
    # Recupera le credenziali dai segreti di GitHub
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    # Parametri della richiesta (usiamo Markdown per mantenere la formattazione di Gemini)
    payload = {
        'chat_id': chat_id,
        'text': testo,
        'parse_mode': 'Markdown'
    }
    
    data = urllib.parse.urlencode(payload).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    
    # Invia il messaggio
    with urllib.request.urlopen(req) as response:
        return response.read()

if __name__ == "__main__":
    # Controllo di sicurezza sulle variabili d'ambiente
    if not all([os.environ.get("GEMINI_API_KEY"), os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")]):
        print("Errore: Mancano una o più variabili d'ambiente (GEMINI_API_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID).")
        exit(1)

    try:
        print("Generazione della rubrica tramite Gemini...")
        rubrica = ottieni_accade_oggi()
        
        print("Invio del messaggio su Telegram...")
        invia_a_telegram(rubrica)
        print("Fatto! Controlla il tuo canale Telegram.")
        
    except Exception as e:
        print(f"Errore durante l'esecuzione dello script: {e}")
