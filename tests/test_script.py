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

    def test_valida_solo_eventi_corretti(self):
        valido = evento_valido()
        data_errata = evento_valido(event_date="1996-05-23")
        ordinario = evento_valido(importance=8)
        iconica = evento_valido(
            category="PARTITA_ICONICA",
            outcome="VITTORIA",
            importance=9,
        )
        sconfitta = evento_valido(
            category="PARTITA_ICONICA",
            outcome="SCONFITTA",
            importance=10,
        )
        fonte_unica = evento_valido(
            source_urls=[
                "https://www.juventus.com/a",
                "https://www.juventus.com/b",
            ]
        )

        risultato = script.valida_eventi(
            [valido, data_errata, ordinario, iconica, sconfitta, fonte_unica],
            "05-22",
        )

        self.assertEqual(risultato, [valido, iconica])

    def test_rifiuta_data_impossibile(self):
        impossibile = evento_valido(event_date="1996-02-30")
        self.assertEqual(script.valida_eventi([impossibile], "02-30"), [])

    def test_conserva_al_massimo_tre_eventi(self):
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
    def test_catena_predefinita_usa_tre_modelli(self):
        with patch.dict("os.environ", {"GEMINI_MODEL": ""}, clear=True):
            self.assertEqual(
                script.modelli_gemini_configurati(),
                (
                    "gemini-3.6-flash",
                    "gemini-3.5-flash",
                    "gemini-3.5-flash-lite",
                ),
            )

    def test_variabile_legacy_aggiunge_fallback(self):
        with patch.dict(
            "os.environ",
            {"GEMINI_MODEL": "modello-personalizzato"},
            clear=True,
        ):
            modelli = script.modelli_gemini_configurati()
            self.assertEqual(modelli[0], "modello-personalizzato")
            self.assertEqual(len(modelli), 4)

    def test_modello_inesistente_attiva_fallback(self):
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

    def test_errore_temporaneo_riprova_stesso_modello(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                if len(chiamate) == 1:
                    raise RuntimeError("503 UNAVAILABLE: high demand")
                return SimpleNamespace(text='{"events": []}')

        client = SimpleNamespace(models=Models())

        with patch("script.random.uniform", return_value=0), patch("script.time.sleep") as sleep:
            risposta = script.chiama_gemini_con_fallback(
                client,
                ("primario", "fallback"),
                "prompt",
                None,
                max_retries=2,
            )

        self.assertEqual(risposta.text, '{"events": []}')
        self.assertEqual(chiamate, ["primario", "primario"])
        sleep.assert_called_once_with(1)

    def test_quota_esaurita_non_prova_fallback(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                raise RuntimeError(
                    "429 RESOURCE_EXHAUSTED: You exceeded your current quota, "
                    "please check your plan and billing details."
                )

        client = SimpleNamespace(models=Models())

        with self.assertRaisesRegex(RuntimeError, "Quota Gemini esaurita"):
            script.chiama_gemini_con_fallback(
                client,
                ("primario", "fallback"),
                "prompt",
                None,
                max_retries=3,
            )

        self.assertEqual(chiamate, ["primario"])

    def test_errore_autenticazione_non_viene_nascosto(self):
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
    def test_stesso_evento_con_testo_diverso_viene_riconosciuto(self):
        pubblicato = evento_valido()
        nuovo = evento_valido(
            event_date="1996-05-23",
            title="La Champions di Roma",
            description="I bianconeri battono l'Ajax dal dischetto.",
            canonical_id="1996|TROFEO|CHAMPIONS_LEAGUE|AJAX",
        )

        self.assertTrue(script.eventi_equivalenti(pubblicato, nuovo))
        pubblicato["published_at"] = "2026-03-12T07:30:00+01:00"

        self.assertEqual(
            script.scarta_gia_pubblicati(
                [nuovo],
                [pubblicato],
                anno_pubblicazione=2026,
            ),
            [],
        )

        self.assertEqual(
            script.scarta_gia_pubblicati(
                [nuovo],
                [pubblicato],
                anno_pubblicazione=2027,
            ),
            [nuovo],
        )

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
                [evento_valido()],
                "2026-07-31T07:30:00+02:00",
                percorso,
            )
            contenuto = json.loads(percorso.read_text(encoding="utf-8"))

            self.assertEqual(contenuto["version"], 1)
            self.assertEqual(len(script.carica_storico(percorso)), 1)


class TestFormattazione(unittest.TestCase):
    def test_html_del_modello_viene_escapato(self):
        evento = evento_valido(title="Trionfo <europeo> a Roma")
        testo = script.formatta_rubrica([evento], "22 MAGGIO")

        self.assertIn("1️⃣9️⃣9️⃣6️⃣", testo)
        self.assertIn("<b>Trionfo &lt;europeo&gt; a Roma</b>", testo)
        self.assertIn("<i>La Juventus conquista", testo)


if __name__ == "__main__":
    unittest.main()
