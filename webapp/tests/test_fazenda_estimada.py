"""Graficos de esporos da fazenda estimada (Mapa Interpolado / lavoura de
cliente): Recomendacoes e PDF, pelo IDW das fazendas reais no raio.

Rodar a partir de webapp/:  python -m unittest tests.test_fazenda_estimada
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

from markupsafe import escape

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
import data_reader  # noqa: E402
import export_pdf  # noqa: E402
import models  # noqa: E402

DOENCA = "Soybean Rust"


def _virtual(nome, lat, lon, tipo="doenca"):
    for vf in models.get_all_virtual_farms():
        if vf["nome"] == nome:
            return vf["site_name"]
    return models.create_virtual_farm(nome, lat + 0.05, lon + 0.05, 30, tipo=tipo)


class FazendaEstimadaTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        lookup = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
        # fazenda real com leitura mais recente dessa doenca
        cls.real = max((v[-1]["data"], s) for (s, d), v in lookup.items() if d == DOENCA and v)[1]
        cls.lat, cls.lon = data_reader.read_site_coordinates()[cls.real]
        cls.virtual = _virtual("Zzz Estimada Graficos", cls.lat, cls.lon)
        if not models.get_user_by_username("Zzz Teste Master Estimada"):
            models.create_user("Zzz Teste Master Estimada", "x", is_master=True)

    def _master(self):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Teste Master Estimada")["id"])
            s["_fresh"] = True
        return c

    def test_historico_interpolado(self):
        hist = webapp._historico_doenca(self.virtual, DOENCA, dias=30)
        self.assertEqual(len(hist), 30)
        real = data_reader.get_site_disease_history(self.real, DOENCA, dias=30)
        self.assertEqual(hist[-1]["data"], real[-1]["data"])  # termina na leitura mais recente das vizinhas
        self.assertTrue(any(h["concentracao"] is not None for h in hist))
        self.assertEqual(hist[-1]["warn"], real[-1]["warn"])
        # fazenda real continua com as leituras dela
        self.assertEqual(webapp._historico_doenca(self.real, DOENCA, dias=30), real)
        # ponto so' clima nao tem grafico
        self.assertEqual(webapp._historico_doenca(_virtual("Zzz Clima Graficos", self.lat, self.lon, tipo="clima"), DOENCA), [])

    def test_pdf_com_grafico(self):
        capturado = {}

        def falso(nome, safra, diseases, **kw):
            capturado["diseases"] = diseases
            return export_pdf.build_recommendation_pdf(nome, safra, diseases, **kw)

        doenca = {"doenca": "Ferrugem", "doenca_en": DOENCA, "rotulo": "Ferrugem", "status": "Perigo"}
        with mock.patch.object(export_pdf, "build_recommendation_pdf", side_effect=falso), \
                mock.patch.object(webapp, "_rosa_ventos", return_value=None), \
                mock.patch.object(webapp, "_com_doencas_manuais", side_effect=lambda *a: a[-1]), \
                webapp.app.test_request_context():
            try:
                webapp._build_site_pdf(self.virtual, [dict(doenca)], None, {}, "Soja", None)
            except Exception:
                pass  # so' interessa o que chegou no gerador do PDF
        hist = capturado["diseases"][0]["historico"]
        self.assertTrue(any(h["concentracao"] is not None for h in hist))
        self.assertIsNotNone(export_pdf._build_spore_chart(hist))  # o grafico e' desenhado

    def test_recomendacoes_mostra_grafico(self):
        master = self._master()
        html = master.get("/recommendations", follow_redirects=True).get_data(as_text=True)
        self.assertIn(f'esporos-graficos" data-site="{escape(self.virtual)}"', html)
        self.assertIn("estimados pelas fazendas reais próximas", html)
        fim = data_reader.get_site_disease_history(self.real, DOENCA, dias=1)[-1]["data"]
        d = master.get("/graficos/dados", query_string={"estacao": self.virtual, "fim": fim}).get_json()
        series = [s for doenca in d["doencas"] for s in doenca["series"] if s["estacao"] == self.virtual]
        self.assertTrue(any(v is not None for s in series for v in s["esporos"]))


if __name__ == "__main__":
    unittest.main()
