import json
import os
import sys
import tempfile
import types as pytypes
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


# Permette di eseguire i test anche in un ambiente locale dove google-genai
# non è ancora installato. Su GitHub Actions verrà usato il pacchetto reale.
try:
    import google.genai  # noqa: F401
except ModuleNotFoundError:
    google_module = pytypes.ModuleType("google")
    genai_module = pytypes.ModuleType("google.genai")

    class DummyClient:
        pass

    class DummyConfig:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class DummyTypes:
        GenerateContentConfig = DummyConfig

    genai_module.Client = DummyClient
    genai_module.types = DummyTypes
    google_module.genai = genai_module

    sys.modules["google"] = google_module
    sys.modules["google.genai"] = genai_module


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import script  # noqa: E402


def evento(
    *,
    date="1996-05-22",
    year=1996,
    category="TROFEO",
    outcome="TROFEO_CONQUISTATO",
    importance=10,
    competition="Champions League",
    opponent="Ajax",
    title="La Champions di Roma",
    description="La Juventus conquista la Champions League ai rigori.",
    canonical_id=None,
    sources=None,
):
    return {
        "event_date": date,
        "year": year,
        "category": category,
        "outcome": outcome,
        "importance": importance,
        "competition": competition,
        "opponent": opponent,
        "title": title,
        "description": description,
        "canonical_id": canonical_id
        or f"{year}|{category}|{competition}|{opponent}",
        "source_urls": sources
        or [
            "https://www.juventus.com/storia",
            "https://www.uefa.com/storia",
        ],
    }


class TestConfigurazione(unittest.TestCase):
    def test_catena_modelli_predefinita(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                script.modelli_gemini_configurati(),
                (
                    "gemini-3.6-flash",
                    "gemini-2.5-flash",
                    "gemini-3.5-flash-lite",
                ),
            )

    def test_gemini_models_personalizzati(self):
        with patch.dict(
            os.environ,
            {"GEMINI_MODELS": "modello-a, modello-b, modello-a"},
            clear=True,
        ):
            self.assertEqual(
                script.modelli_gemini_configurati(),
                ("modello-a", "modello-b"),
            )

    def test_config_gemini_non_contiene_google_search(self):
        config = script._config_senza_ricerca("istruzioni")
        self.assertIsNone(getattr(config, "tools", None))
        self.assertEqual(config.system_instruction, "istruzioni")


class TestSerper(unittest.TestCase):
    def test_richiesta_serper_senza_chiave(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "SERPER_API_KEY"):
                script._richiesta_serper("Juventus")

    def test_richiesta_serper_parsa_risultati(self):
        risposta = {
            "organic": [
                {
                    "title": "Titolo uno",
                    "link": "https://example.com/uno",
                    "snippet": "Testo uno",
                    "date": "5 ago 2020",
                },
                {
                    "title": "",
                    "link": "https://example.com/scarto",
                },
            ]
        }

        context_manager = MagicMock()
        context_manager.__enter__.return_value.read.return_value = (
            json.dumps(risposta).encode("utf-8")
        )

        with patch.dict(
            os.environ,
            {"SERPER_API_KEY": "test-key"},
            clear=True,
        ), patch(
            "script.urllib.request.urlopen",
            return_value=context_manager,
        ) as urlopen:
            risultati = script._richiesta_serper("Juventus", num=7)

        self.assertEqual(len(risultati), 1)
        self.assertEqual(risultati[0]["title"], "Titolo uno")
        richiesta = urlopen.call_args.args[0]
        self.assertEqual(richiesta.headers["X-api-key"], "test-key")
        payload = json.loads(richiesta.data.decode("utf-8"))
        self.assertEqual(payload["num"], 7)
        self.assertEqual(payload["gl"], "it")

    def test_cerca_fonti_elimina_url_duplicati(self):
        prima = [
            {
                "position": 1,
                "title": "A",
                "link": "https://example.com/a",
                "snippet": "",
                "date": "",
            }
        ]
        seconda = [
            {
                "position": 1,
                "title": "A duplicato",
                "link": "https://example.com/a",
                "snippet": "",
                "date": "",
            },
            {
                "position": 2,
                "title": "B",
                "link": "https://example.org/b",
                "snippet": "",
                "date": "",
            },
        ]

        with patch(
            "script._richiesta_serper",
            side_effect=[prima, seconda],
        ):
            corpus = script._cerca_fonti(["query 1", "query 2"])

        self.assertEqual(len(corpus), 2)
        self.assertEqual(
            {voce["link"] for voce in corpus},
            {"https://example.com/a", "https://example.org/b"},
        )

    def test_query_scoperta_include_juventus(self):
        queries = script._query_scoperta("5 AGOSTO")
        self.assertGreaterEqual(len(queries), 3)
        self.assertTrue(all("Juventus" in query for query in queries))

    def test_query_verifica_usa_candidati(self):
        candidati = [
            {
                "year": 2018,
                "title": "Arriva Cristiano Ronaldo",
                "competition": "Calciomercato",
                "opponent": "Cristiano Ronaldo",
            }
        ]
        queries = script._query_verifica(candidati)
        self.assertEqual(len(queries), 1)
        self.assertIn("2018", queries[0])
        self.assertIn("Cristiano Ronaldo", queries[0])


class TestGeminiFallback(unittest.TestCase):
    def test_429_prova_modello_successivo(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                if model == "primo":
                    raise RuntimeError("429 RESOURCE_EXHAUSTED")
                return SimpleNamespace(text='{"events": []}')

        risultato = script.chiama_gemini_con_fallback(
            SimpleNamespace(models=Models()),
            ("primo", "secondo"),
            "prompt",
            None,
            max_retries=1,
        )

        self.assertEqual(risultato.text, '{"events": []}')
        self.assertEqual(chiamate, ["primo", "secondo"])

    def test_503_prova_modello_successivo(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                if model == "primo":
                    raise RuntimeError("503 UNAVAILABLE")
                return SimpleNamespace(text='{"events": []}')

        risultato = script.chiama_gemini_con_fallback(
            SimpleNamespace(models=Models()),
            ("primo", "secondo"),
            "prompt",
            None,
            max_retries=1,
        )

        self.assertEqual(risultato.text, '{"events": []}')
        self.assertEqual(chiamate, ["primo", "secondo"])

    def test_quota_generica_ripete_catena(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                if len(chiamate) < 3:
                    raise RuntimeError("429 RESOURCE_EXHAUSTED")
                return SimpleNamespace(text='{"events": []}')

        with patch("script.time.sleep") as sleep:
            risultato = script.chiama_gemini_con_fallback(
                SimpleNamespace(models=Models()),
                ("primo", "secondo"),
                "prompt",
                None,
                max_retries=2,
            )

        self.assertEqual(risultato.text, '{"events": []}')
        self.assertEqual(chiamate, ["primo", "secondo", "primo"])
        sleep.assert_called_once()

    def test_401_interrompe_senza_fallback(self):
        chiamate = []

        class Models:
            def generate_content(self, model, contents, config):
                chiamate.append(model)
                raise RuntimeError("401 UNAUTHENTICATED")

        with self.assertRaisesRegex(RuntimeError, "401"):
            script.chiama_gemini_con_fallback(
                SimpleNamespace(models=Models()),
                ("primo", "secondo"),
                "prompt",
                None,
                max_retries=3,
            )

        self.assertEqual(chiamate, ["primo"])


class TestScopertaVerifica(unittest.TestCase):
    def test_scoperta_senza_fonti_non_chiama_gemini(self):
        client = SimpleNamespace(models=MagicMock())

        with patch("script._cerca_fonti", return_value=[]):
            eventi = script.scopri_candidati(
                client,
                "08-05",
                "5 AGOSTO",
                models=("modello",),
            )

        self.assertEqual(eventi, [])
        client.models.generate_content.assert_not_called()

    def test_scoperta_usa_serper_e_gemini_senza_tool(self):
        fonti = [
            {
                "query": "q",
                "position": 1,
                "title": "Evento",
                "link": "https://example.com/a",
                "snippet": "Evento Juventus",
                "date": "",
            }
        ]
        risposta = SimpleNamespace(
            text=json.dumps(
                {
                    "events": [
                        {
                            "event_date": "2018-07-10",
                            "year": 2018,
                            "category": "ACQUISTO_ICONICO",
                            "outcome": "ACQUISTO_UFFICIALE",
                            "competition": "Calciomercato",
                            "opponent": "Cristiano Ronaldo",
                            "title": "Arriva Cristiano Ronaldo",
                            "description": "La Juventus ufficializza l'acquisto.",
                            "reason": "Operazione epocale",
                            "evidence_urls": ["https://example.com/a"],
                        }
                    ]
                }
            )
        )

        with patch("script._cerca_fonti", return_value=fonti), patch(
            "script.chiama_gemini_con_fallback",
            return_value=risposta,
        ) as chiamata:
            eventi = script.scopri_candidati(
                SimpleNamespace(),
                "07-10",
                "10 LUGLIO",
                models=("modello",),
            )

        self.assertEqual(len(eventi), 1)
        config = chiamata.call_args.kwargs["config"]
        self.assertIsNone(getattr(config, "tools", None))

    def test_verifica_senza_candidati(self):
        self.assertEqual(
            script.verifica_candidati(
                SimpleNamespace(),
                [],
                "08-05",
                "5 AGOSTO",
            ),
            [],
        )


class TestValidazioneEditoriale(unittest.TestCase):
    def test_accetta_8_9_10(self):
        eventi = [
            evento(importance=8, canonical_id="A"),
            evento(importance=9, canonical_id="B"),
            evento(importance=10, canonical_id="C"),
        ]
        validi = script.valida_eventi(eventi, "05-22")
        self.assertEqual(
            [voce["importance"] for voce in validi],
            [10, 9, 8],
        )

    def test_rifiuta_7(self):
        self.assertEqual(
            script.valida_eventi(
                [evento(importance=7)],
                "05-22",
            ),
            [],
        )

    def test_massimo_tre_priorita_importanza(self):
        eventi = [
            evento(
                date="1900-05-22",
                year=1900,
                importance=8,
                canonical_id="1900",
            ),
            evento(
                date="2000-05-22",
                year=2000,
                importance=10,
                canonical_id="2000",
            ),
            evento(
                date="1990-05-22",
                year=1990,
                importance=9,
                canonical_id="1990",
            ),
            evento(
                date="1980-05-22",
                year=1980,
                importance=10,
                canonical_id="1980",
            ),
        ]
        validi = script.valida_eventi(eventi, "05-22")
        self.assertEqual(len(validi), 3)
        self.assertEqual(
            [voce["importance"] for voce in validi],
            [10, 10, 9],
        )

    def test_acquisto_iconico_ufficiale(self):
        acquisto = evento(
            date="2018-07-10",
            year=2018,
            category="ACQUISTO_ICONICO",
            outcome="ACQUISTO_UFFICIALE",
            importance=10,
            competition="Calciomercato",
            opponent="Cristiano Ronaldo",
            title="Arriva Cristiano Ronaldo",
        )
        self.assertEqual(
            script.valida_eventi([acquisto], "07-10"),
            [acquisto],
        )

    def test_visite_mediche_non_passano(self):
        visite = evento(
            date="2018-07-10",
            year=2018,
            category="ACQUISTO_ICONICO",
            outcome="VISITE_MEDICHE",
            importance=10,
        )
        self.assertEqual(
            script.valida_eventi([visite], "07-10"),
            [],
        )

    def test_servono_due_domini(self):
        debole = evento(
            sources=[
                "https://www.juventus.com/a",
                "https://www.juventus.com/b",
            ]
        )
        self.assertEqual(
            script.valida_eventi([debole], "05-22"),
            [],
        )


class TestScartatiFactCheck(unittest.TestCase):
    def test_candidato_eliminato_dal_fact_check_viene_archiviato(self):
        candidato = {
            "event_date": "2010-08-05",
            "year": 2010,
            "category": "PARTITA_MEMORABILE",
            "competition": "UEFA Europa League",
            "opponent": "Shamrock Rovers",
            "title": "Vittoria sullo Shamrock Rovers",
        }

        scartati = script.raccogli_scartati_fact_check([candidato], [])

        self.assertEqual(len(scartati), 1)
        self.assertEqual(scartati[0]["phase"], "fact_check")

    def test_candidato_verificato_non_viene_archiviato(self):
        candidato = {
            "event_date": "2010-08-05",
            "year": 2010,
            "category": "PARTITA_MEMORABILE",
            "competition": "UEFA Europa League",
            "opponent": "Shamrock Rovers",
            "title": "Vittoria sullo Shamrock Rovers",
        }
        verificato = dict(candidato)

        self.assertEqual(
            script.raccogli_scartati_fact_check([candidato], [verificato]),
            [],
        )


class TestScartati(unittest.TestCase):
    def test_raccoglie_motivo_validazione(self):
        debole = evento(importance=7)
        self.assertEqual(
            script.valida_eventi([debole], "05-22"),
            [],
        )

        scartati = script.raccogli_scartati_validazione(
            [debole]
        )

        self.assertEqual(len(scartati), 1)
        self.assertEqual(
            scartati[0]["phase"],
            "validazione",
        )
        self.assertIn(
            "sotto soglia",
            scartati[0]["reason"],
        )

    def test_salva_scartati_json(self):
        with tempfile.TemporaryDirectory() as directory:
            percorso = Path(directory) / "scartati.json"
            scarto = {
                "phase": "validazione",
                "reason": "importanza sotto soglia (7/10)",
                "event": evento(importance=7),
            }

            script.salva_scartati(
                [scarto],
                "2026-08-05T09:00:00+02:00",
                percorso,
            )

            dati = json.loads(
                percorso.read_text(encoding="utf-8")
            )

        self.assertEqual(dati["version"], 1)
        self.assertEqual(len(dati["rejected"]), 1)
        self.assertEqual(
            dati["rejected"][0]["phase"],
            "validazione",
        )

    def test_non_duplica_stesso_scarto_nello_stesso_giorno(self):
        with tempfile.TemporaryDirectory() as directory:
            percorso = Path(directory) / "scartati.json"
            scarto = {
                "phase": "validazione",
                "reason": "categoria non ammessa",
                "event": evento(),
            }

            script.salva_scartati(
                [scarto],
                "2026-08-05T09:00:00+02:00",
                percorso,
            )
            script.salva_scartati(
                [scarto],
                "2026-08-05T11:00:00+02:00",
                percorso,
            )

            self.assertEqual(
                len(script.carica_scartati(percorso)),
                1,
            )

    def test_duplicato_storico_finiscе_negli_scartati(self):
        corrente = evento()
        vecchio = dict(
            corrente,
            published_at="2026-05-22T07:30:00+02:00",
        )
        raccolta = []

        risultati = script.scarta_gia_pubblicati(
            [corrente],
            [vecchio],
            anno_pubblicazione=2026,
            scartati=raccolta,
        )

        self.assertEqual(risultati, [])
        self.assertEqual(len(raccolta), 1)
        self.assertEqual(raccolta[0]["phase"], "storico")


class TestStorico(unittest.TestCase):
    def test_stesso_anno_bloccato_anno_successivo_permesso(self):
        corrente = evento()
        vecchio = dict(
            corrente,
            published_at="2026-05-22T07:30:00+02:00",
        )

        self.assertEqual(
            script.scarta_gia_pubblicati(
                [corrente],
                [vecchio],
                anno_pubblicazione=2026,
            ),
            [],
        )
        self.assertEqual(
            script.scarta_gia_pubblicati(
                [corrente],
                [vecchio],
                anno_pubblicazione=2027,
            ),
            [corrente],
        )

    def test_salvataggio_e_lettura_storico(self):
        with tempfile.TemporaryDirectory() as directory:
            percorso = Path(directory) / "eventi.json"
            script.salva_nello_storico(
                [evento()],
                "2026-05-22T07:30:00+02:00",
                percorso,
            )
            storico = script.carica_storico(percorso)

        self.assertEqual(len(storico), 1)
        self.assertEqual(
            storico[0]["canonical_id"],
            evento()["canonical_id"],
        )


class TestFormattazione(unittest.TestCase):
    def test_output_telegram(self):
        testo = script.formatta_rubrica(
            [evento()],
            "22 MAGGIO",
        )
        self.assertIn(
            "<b>👀🔙 ACCADDE OGGI | 22 MAGGIO</b>",
            testo,
        )
        self.assertIn("👉 @Juventus_Reborn", testo)


if __name__ == "__main__":
    unittest.main()
