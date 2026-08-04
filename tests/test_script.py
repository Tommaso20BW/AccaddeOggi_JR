import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import script


def evento(date="1996-05-22", year=1996, category="TROFEO",
           outcome="TROFEO_CONQUISTATO", importance=10,
           context="UEFA Champions League", subject="Ajax",
           title="Trionfo europeo a Roma",
           description="La Juventus conquista la Champions League ai rigori.",
           canonical=None, sources=None):
    return {
        "event_date": date,
        "year": year,
        "category": category,
        "outcome": outcome,
        "importance": importance,
        "competition": context,
        "opponent": subject,
        "title": title,
        "description": description,
        "canonical_id": canonical or f"{year}|{category}|{context}|{subject}",
        "source_urls": sources or [
            "https://www.juventus.com/storia",
            "https://www.uefa.com/storia",
        ],
    }


class TestEditoriale(unittest.TestCase):
    def test_accetta_8_9_10(self):
        events = [
            evento(importance=8, canonical="A"),
            evento(importance=9, canonical="B"),
            evento(importance=10, canonical="C"),
        ]
        self.assertEqual([x["importance"] for x in script.valida_eventi(events, "05-22")], [10, 9, 8])

    def test_rifiuta_7(self):
        self.assertEqual(script.valida_eventi([evento(importance=7)], "05-22"), [])

    def test_massimo_tre_priorita_importanza(self):
        events = [
            evento(date="1900-05-22", year=1900, importance=8, canonical="1900"),
            evento(date="2000-05-22", year=2000, importance=10, canonical="2000"),
            evento(date="1990-05-22", year=1990, importance=9, canonical="1990"),
            evento(date="1980-05-22", year=1980, importance=10, canonical="1980"),
            evento(date="1970-05-22", year=1970, importance=8, canonical="1970"),
        ]
        result = script.valida_eventi(events, "05-22")
        self.assertEqual([x["importance"] for x in result], [10, 10, 9])
        self.assertEqual([x["year"] for x in result], [1980, 2000, 1990])

    def test_acquisto_iconico_valido(self):
        e = evento(date="2018-07-10", year=2018, category="ACQUISTO_ICONICO",
                   outcome="ACQUISTO_UFFICIALE", importance=10,
                   context="Calciomercato", subject="Cristiano Ronaldo",
                   title="Arriva Cristiano Ronaldo",
                   description="La Juventus ufficializza l'acquisto di Cristiano Ronaldo.")
        self.assertEqual(script.valida_eventi([e], "07-10"), [e])

    def test_acquisto_non_ufficiale_scartato(self):
        e = evento(date="2018-07-10", year=2018, category="ACQUISTO_ICONICO",
                   outcome="VISITE_MEDICHE", importance=10)
        self.assertEqual(script.valida_eventi([e], "07-10"), [])

    def test_tutte_nuove_categorie(self):
        for i, (cat, outcome) in enumerate([
            ("PARTITA_MEMORABILE", "VITTORIA"),
            ("DEBUTTO_ICONICO", "DEBUTTO"),
            ("TRAGUARDO_STORICO", "TRAGUARDO_RAGGIUNTO"),
            ("ACQUISTO_ICONICO", "ACQUISTO_UFFICIALE"),
        ]):
            e = evento(category=cat, outcome=outcome, importance=8, canonical=f"X{i}")
            self.assertEqual(script.valida_eventi([e], "05-22"), [e])

    def test_due_fonti_indipendenti(self):
        e = evento(sources=["https://www.juventus.com/a", "https://www.juventus.com/b"])
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_data_errata(self):
        self.assertEqual(script.valida_eventi([evento(date="1996-05-23")], "05-22"), [])

    def test_titolo_troppo_corto_scartato(self):
        e = evento(title="Solo")
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_titolo_troppo_lungo_scartato(self):
        e = evento(title="Questo titolo contiene decisamente troppe parole")
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_descrizione_troppo_lunga_scartata(self):
        e = evento(description="x" * 241)
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_categoria_sconosciuta_scartata(self):
        e = evento(category="CURIOSITA", outcome="EVENTO")
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_sconfitta_scartata(self):
        e = evento(category="PARTITA_ICONICA", outcome="SCONFITTA")
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_anno_non_coerente_scartato(self):
        e = evento(date="1996-05-22", year=1997)
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_url_non_http_scartati(self):
        e = evento(sources=["ftp://juventus.com/a", "mailto:test@example.com"])
        self.assertEqual(script.valida_eventi([e], "05-22"), [])

    def test_ordine_a_parita_di_voto(self):
        events = [
            evento(date="2000-05-22", year=2000, importance=9, canonical="B"),
            evento(date="1980-05-22", year=1980, importance=9, canonical="A"),
        ]
        result = script.valida_eventi(events, "05-22")
        self.assertEqual([x["year"] for x in result], [1980, 2000])


class TestStorico(unittest.TestCase):
    def test_stesso_anno_bloccato_successivo_permesso(self):
        e = evento()
        old = dict(e, published_at="2026-05-22T07:30:00+02:00")
        self.assertEqual(script.scarta_gia_pubblicati([e], [old], 2026), [])
        self.assertEqual(script.scarta_gia_pubblicati([e], [old], 2027), [e])

    def test_duplicato_stesso_run(self):
        e1 = evento()
        e2 = dict(e1, title="La Champions di Roma")
        self.assertEqual(len(script.scarta_gia_pubblicati([e1, e2], [], 2026)), 1)

    def test_salvataggio_storico(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "events.json"
            script.salva_nello_storico([evento()], "2026-05-22T07:30:00+02:00", p)
            self.assertEqual(len(script.carica_storico(p)), 1)

    def test_storico_senza_published_at_non_blocca(self):
        e = evento()
        self.assertEqual(script.scarta_gia_pubblicati([e], [dict(e)], 2026), [e])

    def test_published_at_malformato_non_blocca(self):
        e = evento()
        old = dict(e, published_at="data-sbagliata")
        self.assertEqual(script.scarta_gia_pubblicati([e], [old], 2026), [e])


class TestGemini(unittest.TestCase):
    def test_quota_non_fallback(self):
        calls = []
        class Models:
            def generate_content(self, model, contents, config):
                calls.append(model)
                raise RuntimeError("429 RESOURCE_EXHAUSTED: You exceeded your current quota, check your plan and billing details")
        with self.assertRaisesRegex(RuntimeError, "Quota Gemini esaurita"):
            script.chiama_gemini_con_fallback(SimpleNamespace(models=Models()), ("a", "b"), "x", None, 3)
        self.assertEqual(calls, ["a"])

    def test_404_fallback(self):
        calls = []
        class Models:
            def generate_content(self, model, contents, config):
                calls.append(model)
                if model == "a":
                    raise RuntimeError("404 model not found")
                return SimpleNamespace(text='{"events":[]}')
        r = script.chiama_gemini_con_fallback(SimpleNamespace(models=Models()), ("a", "b"), "x", None, 1)
        self.assertEqual(r.text, '{"events":[]}')
        self.assertEqual(calls, ["a", "b"])

    def test_errore_503_riprova_stesso_modello(self):
        calls = []
        class Models:
            def generate_content(self, model, contents, config):
                calls.append(model)
                if len(calls) == 1:
                    raise RuntimeError("503 UNAVAILABLE")
                return SimpleNamespace(text='{"events":[]}')
        from unittest.mock import patch
        with patch("script.time.sleep"), patch("script.random.uniform", return_value=0):
            r = script.chiama_gemini_con_fallback(
                SimpleNamespace(models=Models()), ("a", "b"), "x", None, 2
            )
        self.assertEqual(r.text, '{"events":[]}')
        self.assertEqual(calls, ["a", "a"])

    def test_401_non_attiva_fallback(self):
        calls = []
        class Models:
            def generate_content(self, model, contents, config):
                calls.append(model)
                raise RuntimeError("401 UNAUTHENTICATED")
        with self.assertRaisesRegex(RuntimeError, "401"):
            script.chiama_gemini_con_fallback(
                SimpleNamespace(models=Models()), ("a", "b"), "x", None, 1
            )
        self.assertEqual(calls, ["a"])


if __name__ == "__main__":
    unittest.main()
