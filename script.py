import os
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

    # 2. Inizializza il client di Gemini
    # GitHub Actions passerà automaticamente la chiave tramite la variabile d'ambiente GEMINI_API_KEY
    client = Client()

    # 3. Istruzioni di sistema per definire lo stile editoriale della rubrica
    system_instruction = """
    Sei il redattore capo di una famosa pagina social dedicata alla Juventus. 
    Il tuo compito è scrivere la rubrica quotidiana "ACCADE OGGI NELLA STORIA DELLA JUVENTUS".
    
    Regole di stile e formattazione:
    - Inizia sempre il post con il titolo: "## ⚪️⚫️ ACCADE OGGI – [DATA DI OGGI] ⚪️⚫️"
    - Sii epico, elegante, ma conciso. Usa uno stile da storytelling sportivo.
    - Usa i punti elenco o sottotitoli (es. ### 🏆 1973 – Titolo) per separare i vari eventi.
    - Usa il grassetto per evidenziare i nomi dei protagonisti (giocatori, allenatori), i risultati, i marcatori o gli anni.
    - Ordina gli eventi dal più vecchio al più recente.
    - Se in questo giorno non ci sono stati trofei o partite storiche, cerca compleanni di leggende bianconere, debutti memorabili o gol iconici.
    - Chiudi sempre il post con il motto: "_Fino alla fine._"
    - Sii storicamente preciso: non inventare mai date, trasferimenti o risultati.
    """

    prompt = f"Trova e descrivi gli eventi più importanti accaduti il giorno {data_italiana} nella storia della Juventus."

    # 4. Chiamata alle API di Gemini utilizzando il modello richiesto
    response = client.models.generate_content(
        model='gemini-3.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system_instruction,
            temperature=0.2, # Temperatura bassa per garantire massima precisione storica
        )
    )

    return response.text

if __name__ == "__main__":
    # Verifica di sicurezza prima di lanciare lo script
    if not os.environ.get("GEMINI_API_KEY"):
        print("Errore: La variabile d'ambiente GEMINI_API_KEY non è configurata.")
        exit(1)

    try:
        print("Generazione della rubrica in corso...")
        rubrica = ottieni_accade_oggi()
        print("\n--- OUTPUT GENERATO ---")
        print(rubrica)
        print("-----------------------\n")
        
    except Exception as e:
        print(f"Errore durante la generazione della rubrica: {e}")
