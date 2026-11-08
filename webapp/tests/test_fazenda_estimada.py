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


class PinoDoPlanoTest(unittest.TestCase):
    """Fazenda criada pelo plano (lavoura de cliente) = pino verde-claro no
    Mapa e no Mapas de Estudo (pedido do Alan, 07/11/2026)."""

    def test_flag_de_plano_nos_mapas_e_rename(self):
        webapp.app.config["TESTING"] = True
        lookup = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
        real = max((v[-1]["data"], s) for (s, d), v in lookup.items() if d == DOENCA and v)[1]
        lat, lon = data_reader.read_site_coordinates()[real]
        site = _virtual("Zzz Lavoura Plano Pino", lat, lon)
        uid = models.criar_cliente("Zzz Cliente Pino", "segredo1", "", "5565999990777", "", None)
        models.ligar_ponto_a_fazenda(models.criar_cliente_ponto(uid, "Talhao Pino", lat, lon), site)
        self.assertIn(site, models.sites_de_clientes())
        if not models.get_user_by_username("Zzz Teste Master Pino"):
            models.create_user("Zzz Teste Master Pino", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Teste Master Pino")["id"])
            s["_fresh"] = True
        for url in ("/mapa", "/mapa-interpolado"):
            html = c.get(url).get_data(as_text=True)
            self.assertIn("PIN_COR_PLANO = '#b2f2bb'", html, url)
            dados = html.split("var sites = ", 1)[1].split(";\n", 1)[0]
            import json
            por_site = {d["site"]: d for d in json.loads(dados)}
            self.assertTrue(por_site[site]["de_plano"], url)
            self.assertFalse(por_site[real].get("de_plano"), url)
        # renomear a fazenda (Manejos/Mapas de Estudo) nao perde a ligacao com a lavoura
        novo = models.update_virtual_farm(site, "Zzz Lavoura Plano Pino 2", lat + 0.05, lon + 0.05, 30)
        self.assertIn(novo, models.sites_de_clientes())
        self.assertEqual(models.get_cliente_pontos(uid)[0]["site_name"], novo)


class EnvioPorPlanoTest(unittest.TestCase):
    """Relatorio da fazenda estimada do cliente: Free so' texto; Premium tambem PDF
    (pedido do Alan, 08/11/2026)."""

    def test_formatos_pelo_plano(self):
        import whatsapp
        webapp.app.config["TESTING"] = True
        lookup = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
        real = max((v[-1]["data"], s) for (s, d), v in lookup.items() if d == DOENCA and v)[1]
        lat, lon = data_reader.read_site_coordinates()[real]
        site = _virtual("Zzz Envio Por Plano", lat, lon)
        p_free = models.salvar_plano(None, "Zzz Free Envio", "", 0, "MONTHLY", 1, 30, ["clima", "whatsapp_texto"], True, 9)
        p_prem = models.salvar_plano(None, "Zzz Prem Envio", "", 0, "MONTHLY", 1, 30,
                                     ["clima", "whatsapp_texto", "whatsapp_pdf"], True, 9)
        free = models.criar_cliente("Zzz Cli Envio Free", "segredo1", "", "5565999990141", "", p_free)
        prem = models.criar_cliente("Zzz Cli Envio Prem", "segredo1", "", "5565999990142", "", p_prem)
        sem = models.criar_cliente("Zzz Cli Envio Vencido", "segredo1", "", "5565999990143", "", p_prem)
        for uid in (free, prem):
            models.atualizar_assinatura(uid, status="ativa", cortesia_ate="2099-01-01")
        for uid in (free, prem, sem):
            models.receber_relatorio_da_fazenda(uid, site)
        conn = models.get_db()
        conn.execute("UPDATE assinaturas SET criado_em = '2026-01-01 08:00:00' WHERE user_id = ?", (sem,))
        conn.commit()
        conn.close()
        textos, docs = [], []
        # dados de exemplo do repo sao antigos: trata como leitura recente (senao nao sai PDF nenhum)
        with webapp.app.app_context(), \
                mock.patch.object(webapp, "_nivel_dados_site", return_value="ok"), \
                mock.patch.object(whatsapp, "usa_modelo_oficial", return_value=False), \
                mock.patch.object(whatsapp, "send_whatsapp", side_effect=lambda tel, txt: textos.append(tel) or (True, "ok")), \
                mock.patch.object(whatsapp, "send_whatsapp_document",
                                  side_effect=lambda tel, b, n, caption=None: docs.append(tel) or (True, "ok")):
            webapp._send_site_whatsapp(site, enviar_texto=True, enviar_pdf=True)
        self.assertIn("5565999990141", textos)          # Free: so' texto
        self.assertNotIn("5565999990141", docs)
        self.assertIn("5565999990142", docs)            # Premium: PDF (com o texto junto ou separado)
        self.assertNotIn("5565999990143", textos + docs)  # sem acesso (Free vencido): nada


class FazendaClienteNoMapaDeEstudoTest(unittest.TestCase):
    """Fazenda marcada pelo cliente: pino verde-claro no Mapas de Estudo mesmo sem
    fazenda real no raio, e em Pontos Criados com o nome do cliente (pedido do Alan, 08/11/2026)."""

    def test_aparece_sem_fazenda_real_no_raio(self):
        import json
        webapp.app.config["TESTING"] = True
        site = _virtual("Zzz Cliente Longe", -2.0, -60.0)  # no meio do Amazonas, longe das fazendas reais
        uid = models.criar_cliente("Zzz Cli Longe", "segredo1", "", "5565999990191", "", None)
        models.ligar_ponto_a_fazenda(models.criar_cliente_ponto(uid, "Area Longe", -1.95, -59.95), site)
        if not models.get_user_by_username("Zzz Teste Master Longe"):
            models.create_user("Zzz Teste Master Longe", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Teste Master Longe")["id"])
            s["_fresh"] = True
        html = c.get("/mapa-interpolado").get_data(as_text=True)
        dados = {d["site"]: d for d in json.loads(html.split("var sites = ", 1)[1].split(";\n", 1)[0])}
        self.assertIn(site, dados)
        self.assertEqual((dados[site]["de_plano"], dados[site]["cards"], dados[site]["inativo"]), (True, [], False))
        self.assertIn("🟢 Cliente: Zzz Cli Longe", html)  # em Pontos Criados


class LavourasAntigasTest(unittest.TestCase):
    """Lavouras de clientes de antes da regra ganham a fazenda estimada na subida
    do site (pedido do Alan, 08/11/2026)."""

    def test_cria_fazenda_e_destinatario(self):
        uid = models.criar_cliente("Zzz Cli Antigo Lav", "segredo1", "", "5565999990231", "", None)
        sem_fazenda = models.criar_cliente_ponto(uid, "Talhao Antigo", -12.6, -55.8)
        site_velho = models.create_virtual_farm("Zzz Fazenda Velha Lav", -12.7, -55.9, 30)
        models.ligar_ponto_a_fazenda(models.criar_cliente_ponto(uid, "Talhao Ligado", -12.7, -55.9), site_velho)
        with webapp.app.app_context():
            self.assertGreaterEqual(webapp._criar_fazendas_das_lavouras_antigas(), 1)
            pontos = {p["id"]: p for p in models.get_cliente_pontos(uid)}
            novo = pontos[sem_fazenda]["site_name"]
            self.assertIsNotNone(models.get_virtual_farm(novo))
            self.assertIn("Talhao Antigo (Zzz Cli Antigo Lav)", novo)
            for site in (novo, site_velho):
                self.assertIn(site, models.get_user_permitted_site_names(uid))
                self.assertIn("5565999990231", [r["telefone"] for r in models.get_site_whatsapp_recipients(site)])
            self.assertEqual(webapp._criar_fazendas_das_lavouras_antigas(), 0)  # 2a vez: nada a fazer


class SafrasPeloCadastroTest(unittest.TestCase):
    """Safras da fazenda do cliente = 3 culturas de maior area (pedido do Alan, 08/11/2026)."""

    def test_top3_e_nao_sobrescreve(self):
        site = models.create_virtual_farm("Zzz Safras Top3", -12.8, -55.6, 30)
        with webapp.app.app_context():
            usadas = webapp._preencher_safras(site, [("Milho", 300), ("Soja", 900), ("Tomate", 5000), ("Algodao", 200),
                                                     ("Feijao", 100), ("Milho", 50)])
        self.assertEqual(usadas, ["Soja", "Milho", "Algodao"])  # Tomate fora da lista do site; Milho somado (350)
        fc = models.get_all_farm_culturas()
        self.assertEqual([fc[(site, sf)]["cultura"] for sf, _ in models.SAFRAS], ["Soja", "Milho", "Algodao"])
        models.set_farm_cultura(site, "safra1", "Feijao")  # ajuste manual em Manejos
        with webapp.app.app_context():
            self.assertEqual(webapp._preencher_safras(site, [("Milho", 999)]), [])
        self.assertEqual(models.get_all_farm_culturas()[(site, "safra1")]["cultura"], "Feijao")

    def test_backfill_cliente_antigo_pelo_cadastro(self):
        uid = models.criar_cliente("Zzz Cli Safra Antigo", "segredo1", "", "5565999990261", "", None)
        models.registrar_cadastro({"username": "Zzz Cli Safra Antigo", "tipo": "cliente", "user_id": uid,
                                   "culturas": "Milho (400 ha), Soja (600 ha)", "area_ha": 1000})
        models.criar_cliente_ponto(uid, "Area Safra Antiga", -12.9, -55.5)
        with webapp.app.app_context():
            webapp._criar_fazendas_das_lavouras_antigas()
        site = models.get_cliente_pontos(uid)[0]["site_name"]
        fc = models.get_all_farm_culturas()
        self.assertEqual([fc.get((site, sf), {}).get("cultura") for sf, _ in models.SAFRAS], ["Soja", "Milho", None])
