import json
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch


import compleanni


def risposta_wikidata():
    return {
        "results": {
            "bindings": [
                {
                    "player": {
                        "value": "http://www.wikidata.org/entity/Q1"
                    },
                    "playerLabel": {"value": "Mario Rossi"},
                    "birthDate": {
                        "value": "1980-08-14T00:00:00Z"
                    },
                },
                {
                    "player": {
                        "value": "http://www.wikidata.org/entity/Q2"
                    },
                    "playerLabel": {"value": "Andrea & Bianchi"},
                    "birthDate": {
                        "value": "2000-08-14T00:00:00Z"
                    },
                },
            ]
        }
    }


class TestWikidata(unittest.TestCase):
    def test_query_filtra_data_juventus_viventi_e_precisione(self):
        query = compleanni.costruisci_query_wikidata(14, 8)
        self.assertIn("wdt:P54 wd:Q1422", query)
        self.assertIn("MONTH(?birthDate) = 8", query)
        self.assertIn("DAY(?birthDate) = 14", query)
        self.assertIn("FILTER NOT EXISTS", query)
        self.assertIn("?birthPrecision >= 11", query)

    def test_interpreta_ordina_calcola_eta_e_deduplica(self):
        dati = risposta_wikidata()
        dati["results"]["bindings"].append(
            dati["results"]["bindings"][0]
        )
        giocatori = compleanni.interpreta_risposta_wikidata(
            dati,
            date(2026, 8, 14),
        )
        self.assertEqual(len(giocatori), 2)
        self.assertEqual(giocatori[0]["name"], "Andrea & Bianchi")
        self.assertEqual(giocatori[0]["age"], 26)
        self.assertEqual(giocatori[1]["age"], 46)

    def test_recupera_compleanni_usa_endpoint_json(self):
        context_manager = MagicMock()
        context_manager.__enter__.return_value.read.return_value = (
            json.dumps(risposta_wikidata()).encode("utf-8")
        )

        with patch(
            "compleanni.urllib.request.urlopen",
            return_value=context_manager,
        ) as urlopen:
            giocatori = compleanni.recupera_compleanni(
                date(2026, 8, 14)
            )

        self.assertEqual(len(giocatori), 2)
        richiesta = urlopen.call_args.args[0]
        self.assertTrue(
            richiesta.full_url.startswith(compleanni.WIKIDATA_ENDPOINT)
        )
        self.assertEqual(
            richiesta.headers["Accept"],
            "application/sparql-results+json",
        )


class TestFormattazione(unittest.TestCase):
    def test_messaggio_usa_il_formato_compatto_richiesto(self):
        giocatori = compleanni.interpreta_risposta_wikidata(
            risposta_wikidata(),
            date(2026, 8, 14),
        )
        testo = compleanni.formatta_messaggio(
            giocatori,
            date(2026, 8, 14),
        )
        self.assertEqual(
            testo,
            "<b>🎂 COMPLEANNI BIANCONERI | 14 AGOSTO</b>\n"
            "🎉 <b>Andrea &amp; Bianchi</b> — 26 anni\n"
            "🎉 <b>Mario Rossi</b> — 46 anni",
        )


class TestStorico(unittest.TestCase):
    def test_salva_e_impedisce_doppio_invio(self):
        with tempfile.TemporaryDirectory() as directory:
            percorso = Path(directory) / "storico.json"
            giocatori = [
                {
                    "qid": "Q1",
                    "name": "Mario Rossi",
                    "birth_date": "1980-08-14",
                    "age": 46,
                }
            ]
            compleanni.salva_storico(
                date(2026, 8, 14),
                giocatori,
                percorso,
            )
            storico = compleanni.carica_storico(percorso)

        self.assertTrue(
            compleanni.gia_inviato(date(2026, 8, 14), storico)
        )
        self.assertEqual(
            storico["2026-08-14"],
            [
                {
                    "name": "Mario Rossi",
                    "age": 46,
                }
            ],
        )
        self.assertFalse(
            compleanni.gia_inviato(date(2026, 8, 15), storico)
        )

    def test_salvataggio_mantiene_solo_ultimi_400_giorni(self):
        oggi = date(2026, 8, 14)
        data_minima = oggi - timedelta(
            days=compleanni.GIORNI_CONSERVAZIONE_STORICO - 1
        )
        troppo_vecchia = data_minima - timedelta(days=1)
        futura = oggi + timedelta(days=1)

        with tempfile.TemporaryDirectory() as directory:
            percorso = Path(directory) / "storico.json"
            percorso.write_text(
                json.dumps(
                    {
                        troppo_vecchia.isoformat(): [],
                        data_minima.isoformat(): [],
                        futura.isoformat(): [],
                    }
                ),
                encoding="utf-8",
            )
            compleanni.salva_storico(
                oggi,
                [
                    {
                        "qid": "Q1",
                        "name": "Mario Rossi",
                        "age": 46,
                    }
                ],
                percorso,
            )
            storico = compleanni.carica_storico(percorso)

        self.assertEqual(
            list(storico),
            [data_minima.isoformat(), oggi.isoformat()],
        )


class TestTelegram(unittest.TestCase):
    def test_invia_alla_destinazione_privata(self):
        context_manager = MagicMock()
        context_manager.__enter__.return_value.read.return_value = (
            b'{"ok": true}'
        )

        with patch.dict(
            os.environ,
            {
                "TELEGRAM_TOKEN": "token-di-test",
                "TELEGRAM_TO_BOT": "1645822265",
                "TELEGRAM_CHAT_ID": "@canale_da_non_usare",
            },
            clear=True,
        ), patch(
            "compleanni.urllib.request.urlopen",
            return_value=context_manager,
        ) as urlopen:
            compleanni.invia_a_telegram("Auguri")

        richiesta = urlopen.call_args.args[0]
        payload = richiesta.data.decode("utf-8")
        self.assertIn("chat_id=1645822265", payload)
        self.assertNotIn("canale_da_non_usare", payload)

    def test_telegram_to_bot_obbligatorio(self):
        with patch.dict(
            os.environ,
            {"TELEGRAM_TOKEN": "token-di-test"},
            clear=True,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "TELEGRAM_TO_BOT",
            ):
                compleanni.invia_a_telegram("Auguri")


class TestMain(unittest.TestCase):
    def test_nessun_compleanno_non_invia_e_non_scrive(self):
        with patch("compleanni.carica_storico", return_value={}), patch(
            "compleanni.recupera_compleanni",
            return_value=[],
        ), patch(
            "compleanni.invia_a_telegram"
        ) as invia, patch(
            "compleanni.salva_storico"
        ) as salva:
            compleanni.main()

        invia.assert_not_called()
        salva.assert_not_called()

    def test_compleanni_trovati_invia_e_salva_dopo_telegram(self):
        giocatori = compleanni.interpreta_risposta_wikidata(
            risposta_wikidata(),
            date(2026, 8, 14),
        )
        ordine = []

        with patch("compleanni.carica_storico", return_value={}), patch(
            "compleanni.recupera_compleanni",
            return_value=giocatori,
        ), patch(
            "compleanni.invia_a_telegram"
        ) as invia, patch(
            "compleanni.salva_storico"
        ) as salva:
            invia.side_effect = lambda _testo: ordine.append("invia")
            salva.side_effect = lambda *_args: ordine.append("salva")
            compleanni.main()

        invia.assert_called_once()
        salva.assert_called_once()
        self.assertEqual(ordine, ["invia", "salva"])

    def test_data_gia_inviata_non_interroga_wikidata(self):
        oggi = compleanni.datetime.now(compleanni.FUSO_ORARIO).date()

        with patch(
            "compleanni.carica_storico",
            return_value={oggi.isoformat(): []},
        ), patch(
            "compleanni.recupera_compleanni"
        ) as recupera, patch(
            "compleanni.invia_a_telegram"
        ) as invia:
            compleanni.main()

        recupera.assert_not_called()
        invia.assert_not_called()


if __name__ == "__main__":
    unittest.main()
