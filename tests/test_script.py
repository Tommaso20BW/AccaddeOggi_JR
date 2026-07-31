import json
import tempfile
import unittest
from pathlib import Path

import script


def evento_valido(**modifiche):
    evento = {
        "event_date": "1996-05-22",
        "year": 1996,
        "category": "TROFEO",
        "outcome": "TROFEO_CONQUISTATO",
        "importance": 10,
        "competition": "UEFA Champions League",
        "opponent": "Ajax",
        "title": "Trionfo europeo a Roma",
        "description": "La Juventus conquista la Champions League ai rigori.",
        "canonical_id": "1996|TROFEO|UEFA_CHAMPIONS_LEAGUE|AJAX",
        "source_urls": [
            "https://www.juventus.com/esempio",
            "https://www.uefa.com/esempio",
        ],
    }
    evento.update(modifiche)
    return evento


class TestRisposteGemini(unittest.TestCase):
    def test_estrai_json_da_code_fence(self):
        risultato = script.estrai_json('```json\n{"events": []}\n```')
        self.assertEqual(risultato, {"events": []})

    def test_valida_solo_evento_con_data_importanza_e_due_fonti(self):
        valido = evento_valido()
        data_errata = evento_valido(event_date="1996-05-23")
        ordinario = evento_valido(importance=8)
        iconica_da_nove = evento_valido(
            category="PARTITA_ICONICA", outcome="VITTORIA", importance=9
        )
        sconfitta_iconica = evento_valido(
            category="PARTITA_ICONICA", outcome="SCONFITTA", importance=10
        )
        fonte_unica = evento_valido(
            source_urls=[
                "https://www.juventus.com/a",
                "https://www.juventus.com/b",
            ]
        )

        risultato = script.valida_eventi(
            [
                valido,
                data_errata,
                ordinario,
                iconica_da_nove,
                sconfitta_iconica,
                fonte_unica,
            ],
            "05-22",
        )

        self.assertEqual(risultato, [valido, iconica_da_nove])

    def test_rifiuta_una_data_di_calendario_impossibile(self):
        impossibile = evento_valido(event_date="1996-02-30")
        self.assertEqual(script.valida_eventi([impossibile], "02-30"), [])


class TestDuplicati(unittest.TestCase):
    def test_stesso_evento_con_testo_e_giorno_diversi_viene_riconosciuto(self):
        pubblicato = evento_valido()
        nuovo = evento_valido(
            event_date="1996-05-23",
            title="La Champions di Roma",
            description="I bianconeri battono l'Ajax dal dischetto.",
            canonical_id="1996|TROFEO|CHAMPIONS_LEAGUE|AJAX",
        )

        self.assertTrue(script.eventi_equivalenti(pubblicato, nuovo))
        self.assertEqual(script.scarta_gia_pubblicati([nuovo], [pubblicato]), [])

    def test_eventi_di_anni_diversi_non_collidono(self):
        primo = evento_valido()
        secondo = evento_valido(
            year=1985,
            event_date="1985-05-29",
            canonical_id="1985|TROFEO|COPPA_CAMPIONI|LIVERPOOL",
            opponent="Liverpool",
        )
        self.assertFalse(script.eventi_equivalenti(primo, secondo))

    def test_storico_si_salva_e_si_ricarica(self):
        with tempfile.TemporaryDirectory() as directory:
            percorso = Path(directory) / "eventi.json"
            script.salva_nello_storico(
                [evento_valido()], "2026-07-31T07:30:00+02:00", percorso
            )
            contenuto = json.loads(percorso.read_text(encoding="utf-8"))

            self.assertEqual(contenuto["version"], 1)
            self.assertEqual(len(script.carica_storico(percorso)), 1)


class TestFormattazione(unittest.TestCase):
    def test_html_del_modello_viene_escapato(self):
        evento = evento_valido(title="Trionfo europeo a Roma")
        testo = script.formatta_rubrica([evento], "22 MAGGIO")
        self.assertIn("1️⃣9️⃣9️⃣6️⃣", testo)
        self.assertIn("<b>Trionfo europeo a Roma</b>", testo)
        self.assertIn("<i>La Juventus conquista", testo)


if __name__ == "__main__":
    unittest.main()
