import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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

    def test_conserva_al_massimo_tre_eventi_importanti(self):
        eventi = [
            evento_valido(
                event_date=f"{anno}-05-22",
                year=anno,
                canonical_id=f"{anno}|TROFEO|COMPETIZIONE_{anno}|AVVERSARIO",
            )
            for anno in (1996, 2000, 2005, 2010)
        ]

        risultato = script.valida_eventi(eventi, "05-22")

        self.assertEqual(len(risultato), 3)
        self.assertEqual([evento["year"] for evento in risultato], [1996, 2000, 2005])


class TestFallbackGemini(unittest.TestCase):
    def test_catena_predefinita_usa_tre_modelli_stabili(self):
        with patch.dict(
            "os.environ", {"GEMINI_MODEL": "", "GEMINI_MODELS": ""}, clear=False
        ):
            del script.os.environ["GEMINI_MODELS"]
            self.assertEqual(
                script.modelli_gemini_configurati(),
                (
                    "gemini-3.6-flash",
                    "gemini-3.5-flash",
                    "gemini-3.5-flash-lite",
                ),
            )

    def test_variabile_legacy_aggiunge_i_fallback_predefiniti(self):
        with patch.dict(
            "os.environ",
            {"GEMINI_MODEL": "modello-personalizzato"},
            clear=True,
        ):
            self.assertEqual(
                script.modelli_gemini_configurati()[0], "modello-personalizzato"
            )
            self.assertEqual(len(script.modelli_gemini_configurati()), 4)

    def test_errore_del_modello_attiva_il_fallback(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                if model == "primario":
                    raise RuntimeError("404 NOT_FOUND: model not found")
                return SimpleNamespace(text='{"events": []}')

        client = SimpleNamespace(models=Models())
        risposta = script.chiama_gemini_con_fallback(
            client,
            ("primario", "fallback-1", "fallback-2"),
            "prompt",
            None,
            max_retries=1,
        )

        self.assertEqual(risposta.text, '{"events": []}')
        self.assertEqual(chiamate, ["primario", "fallback-1"])

    def test_errore_di_autenticazione_non_viene_nascosto(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                raise RuntimeError("401 UNAUTHENTICATED")

        client = SimpleNamespace(models=Models())
        with self.assertRaisesRegex(RuntimeError, "401"):
            script.chiama_gemini_con_fallback(
                client,
                ("primario", "fallback"),
                "prompt",
                None,
                max_retries=1,
            )

        self.assertEqual(chiamate, ["primario"])


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
