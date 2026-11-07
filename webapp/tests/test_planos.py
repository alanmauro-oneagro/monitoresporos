"""Planos padrao (Free, Basico, Premium, Premium + Estacao), plano gratis
liberado na hora e preco/abas editaveis pelo Master.

Rodar a partir de webapp/:  python -m unittest tests.test_planos
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
os.environ.pop("ASAAS_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
import models  # noqa: E402

CPF_VALIDO = "52998224725"


class PlanosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        models.init_db()
        if not models.get_user_by_username("Zzz Teste Master Planos"):
            models.create_user("Zzz Teste Master Planos", "x", is_master=True)
        cls.planos = {p["nome"]: p for p in models.get_planos()}

    def _logado(self, user_id):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(user_id)
            s["_fresh"] = True
        return c

    def _cliente(self, nome):
        return models.criar_cliente(nome, "segredo1", "", "5565999990000", "", None)

    def test_planos_padrao(self):
        for nome in ("Free", "Básico", "Premium", "Premium + Estação"):
            self.assertIn(nome, self.planos)
        free = self.planos["Free"]
        self.assertEqual((free["valor"], free["ativo"], free["recursos"]), (0, 1, ["clima"]))
        self.assertEqual(self.planos["Premium"]["ativo"], 0)  # pago nasce fora de venda ate' ter preco
        models.init_db()  # rodar de novo nao duplica
        self.assertEqual(sum(1 for p in models.get_planos() if p["nome"] == "Free"), 1)

    def test_master_define_preco_e_abas(self):
        master = self._logado(models.get_user_by_username("Zzz Teste Master Planos")["id"])
        premium = self.planos["Premium"]
        basico = self.planos["Básico"]
        k, b = premium["id"], basico["id"]
        # um envio so' grava todos os planos da tela
        master.post("/admin/planos/salvar", data={
            "plano_ids": [str(k), str(b)],
            f"nome__{k}": "Premium", f"valor__{k}": "149,90", f"ciclo__{k}": "MONTHLY", f"max_pontos__{k}": "10",
            f"raio_km__{k}": "50", f"ordem__{k}": "3", f"recursos__{k}": ["clima", "esporos", "risco"], f"ativo__{k}": "on",
            f"nome__{b}": "Básico", f"valor__{b}": "59,90", f"ciclo__{b}": "MONTHLY", f"max_pontos__{b}": "3",
            f"raio_km__{b}": "50", f"ordem__{b}": "2", f"recursos__{b}": ["clima", "site_mapa"],
            "nome__novo": "",
        })
        p = models.get_plano(k)
        self.assertEqual((p["valor"], p["ativo"], p["recursos"]), (149.9, 1, ["clima", "esporos", "risco"]))
        self.assertEqual((models.get_plano(b)["valor"], models.get_plano(b)["ativo"]), (59.9, 0))
        html = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("Abas liberadas no app", html)
        self.assertIn("0 = grátis", html)
        self.assertEqual(html.count('type="submit">Salvar</button>'), 1)  # um botao Salvar so'
        # com um plano errado, nenhum e' gravado
        negativo = master.post("/admin/planos/salvar", data={
            "plano_ids": [str(k), str(b)],
            f"nome__{k}": "Premium", f"valor__{k}": "-1", f"ciclo__{k}": "MONTHLY", f"max_pontos__{k}": "10", f"raio_km__{k}": "50",
            f"nome__{b}": "Básico", f"valor__{b}": "1,00", f"ciclo__{b}": "MONTHLY", f"max_pontos__{b}": "3", f"raio_km__{b}": "50",
        }, follow_redirects=True)
        self.assertIn("0 = plano grátis", negativo.get_data(as_text=True))
        self.assertEqual((models.get_plano(k)["valor"], models.get_plano(b)["valor"]), (149.9, 59.9))

    def test_free_libera_na_hora_sem_cpf(self):
        uid = self._cliente("Zzz Cliente Free")
        c = self._logado(uid)
        html = c.get("/app/assinatura").get_data(as_text=True)
        self.assertIn("Escolha o seu plano", html)
        self.assertIn("Grátis", html)
        # todos os planos aparecem; os fora de venda como "Em breve", sem escolha
        self.assertIn("Premium + Estação", html)
        self.assertIn("Em breve", html)
        self.assertIn(f'id="plano_{self.planos["Premium + Estação"]["id"]}"', html)
        self.assertNotIn("Aguardando pagamento", html)  # situacao so' depois de escolher
        forcado = c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Premium + Estação"]["id"])},
                         follow_redirects=True)
        self.assertIn("Escolha um plano", forcado.get_data(as_text=True))
        self.assertIsNone(models.get_assinatura(uid)["plano_id"])
        self.assertIn("/app/assinatura", c.get("/app").headers["Location"])  # sem plano: bloqueado
        resp = c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Free"]["id"])})
        self.assertTrue(resp.headers["Location"].endswith("/app"))
        assinatura = models.get_assinatura(uid)
        self.assertEqual((assinatura["plano_id"], assinatura["status"], assinatura["cpf_cnpj"]),
                         (self.planos["Free"]["id"], "ativa", ""))
        self.assertTrue(models.assinatura_liberada(assinatura))
        self.assertEqual(c.get("/app").status_code, 200)
        self.assertIn("Sem prazo", c.get("/app/assinatura").get_data(as_text=True))

    def test_trocar_free_por_pago(self):
        basico = self.planos["Básico"]
        models.salvar_plano(basico["id"], "Básico", "", 49.9, "MONTHLY", 3, 50, ["clima", "esporos"], True, 2)
        uid = self._cliente("Zzz Cliente Troca")
        c = self._logado(uid)
        c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Free"]["id"])})
        # sem CPF: recusa e continua no gratis
        html = c.post("/app/assinatura/assinar", data={"plano_id": str(basico["id"])}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("CPF ou CNPJ válido", html)
        self.assertTrue(models.assinatura_liberada(models.get_assinatura(uid)))
        # com CPF: vira o pago, aguardando pagamento (Asaas nao configurado no teste)
        c.post("/app/assinatura/assinar", data={"plano_id": str(basico["id"]), "cpf_cnpj": CPF_VALIDO})
        assinatura = models.get_assinatura(uid)
        self.assertEqual((assinatura["plano_id"], assinatura["cpf_cnpj"], assinatura["cortesia_ate"]),
                         (basico["id"], CPF_VALIDO, None))
        self.assertFalse(models.assinatura_liberada(assinatura))


    def _cliente_no_plano(self, nome, recursos):
        plano_id = models.salvar_plano(None, f"Zzz Plano {nome}", "", 0, "MONTHLY", 3, 40, recursos, True, 9)
        uid = self._cliente(nome)
        models.atualizar_assinatura(uid, plano_id=plano_id, status="ativa", cortesia_ate=models.SEM_PRAZO)
        return uid

    def test_recursos_do_site_depois_da_previsao(self):
        chaves = [c for c, _ in models.RECURSOS_PLANO]
        self.assertEqual(chaves[:6], ["clima", "site_mapa", "site_alertas", "site_graficos", "site_manejos", "site_recomendacoes"])
        self.assertIn("fazenda_estimada", chaves)

    def test_abas_do_site_conforme_plano_e_fazenda_estimada(self):
        uid = self._cliente_no_plano("Abas", ["clima", "site_mapa", "site_manejos", "site_recomendacoes", "fazenda_estimada"])
        c = self._logado(uid)
        self.assertEqual(c.get("/mapa").status_code, 200)
        self.assertEqual(c.get("/fazendas").status_code, 200)
        self.assertEqual(c.get("/recommendations", follow_redirects=True).status_code, 200)
        for fora in ("/graficos", "/dashboard", "/admin/users", "/mapa-interpolado"):
            with self.subTest(rota=fora):
                self.assertTrue(c.get(fora).headers["Location"].endswith("/app"))
        menu = c.get("/mapa").get_data(as_text=True)
        self.assertIn("📱 App", menu)
        self.assertNotIn(">Graficos<", menu)
        self.assertIn("🌐", c.get("/app").get_data(as_text=True))

        # lavoura nova vira fazenda estimada liberada so' pra ele
        resp = c.post("/app/pontos/novo", data={"nome": "Talhao Zzz", "lat": "-12.5", "lon": "-55.7"})
        self.assertEqual(resp.status_code, 302)
        ponto = models.get_cliente_pontos(uid)[0]
        self.assertTrue(ponto["site_name"])
        vf = models.get_virtual_farm(ponto["site_name"])
        self.assertEqual((vf["nome"], vf["raio_km"], vf["tipo"]), ("Talhao Zzz (Abas)", 40, "doenca"))
        self.assertIn(ponto["site_name"], models.get_user_permitted_site_names(uid))
        # apagar a lavoura apaga a fazenda estimada
        c.post(f"/app/pontos/{ponto['id']}/excluir")
        self.assertIsNone(models.get_virtual_farm(ponto["site_name"]))

    def test_plano_sem_fazenda_estimada_e_free_sem_site(self):
        uid = self._cliente_no_plano("Estacao", ["clima", "site_mapa"])
        c = self._logado(uid)
        c.post("/app/pontos/novo", data={"nome": "Talhao Estacao", "lat": "-12.5", "lon": "-55.7"})
        self.assertIsNone(models.get_cliente_pontos(uid)[0]["site_name"])  # Premium + Estacao: Master libera a real
        self.assertEqual(c.get("/mapa").status_code, 200)
        free = self._cliente_no_plano("Free Site", ["clima"])
        self.assertTrue(self._logado(free).get("/mapa").headers["Location"].endswith("/app"))

    def test_excluir_cliente_apaga_fazendas_estimadas(self):
        uid = self._cliente_no_plano("Excluir Site", ["clima", "site_mapa", "fazenda_estimada"])
        self._logado(uid).post("/app/pontos/novo", data={"nome": "Talhao Exc", "lat": "-12.5", "lon": "-55.7"})
        site_name = models.get_cliente_pontos(uid)[0]["site_name"]
        self.assertIsNotNone(models.get_virtual_farm(site_name))
        models.delete_user(uid)
        self.assertIsNone(models.get_virtual_farm(site_name))


class ExcelClientesPlanosTest(unittest.TestCase):
    """Abas "Clientes" e "Planos" no Excel de Exportar (pedido do Alan, 01/11/2026)."""

    def test_abas_clientes_e_planos(self):
        import datetime as dt
        import export_excel
        from openpyxl import load_workbook
        plano = models.salvar_plano(None, "Zzz Plano Excel", "", 79.9, "MONTHLY", 3, 50, ["clima", "esporos"], True, 9)
        uid = models.criar_cliente("Zzz Cliente Excel", "segredo1", "excel@t.com", "5565999990055", "", plano)
        models.atualizar_assinatura(uid, plano_id=plano, status="ativa", cortesia_ate=models.SEM_PRAZO)
        models.registrar_cadastro({"username": "Zzz Cliente Excel", "nome_completo": "Zzz Cliente Excel da Silva",
                                   "perfil": "produtor,tecnico", "telefone": "5565999990055", "empresa": "Fazenda Zzz",
                                   "cidade": "Sorriso", "uf": "MT", "culturas": "Soja", "area_ha": 1500,
                                   "tipo": "cliente", "user_id": uid})
        wb = load_workbook(export_excel.build_workbook())
        self.assertIn("Clientes", wb.sheetnames)
        self.assertIn("Planos", wb.sheetnames)
        clientes = list(wb["Clientes"].iter_rows(values_only=True))
        self.assertEqual(clientes[0][:3], ("Cliente", "Usuário", "Celular"))
        linha = next(r for r in clientes if r[1] == "Zzz Cliente Excel")
        cab = clientes[0]
        valor = dict(zip(cab, linha))
        self.assertEqual(valor["Cliente"], "Zzz Cliente Excel da Silva")
        self.assertEqual(valor["Perfil"], "Produtor, Técnico do setor")
        self.assertEqual((valor["Cidade"], valor["Plano"], valor["Valor do plano"], valor["Moeda"], valor["Ciclo"]),
                         ("Sorriso/MT", "Zzz Plano Excel", 79.9, "R$", "Mensal"))
        self.assertEqual((valor["Situação"], valor["Acesso liberado"], valor["Cortesia até"]), ("Ativa", "Sim", "Sem prazo"))
        self.assertIsInstance(valor["Cadastrado em"], dt.datetime)  # data de verdade, nao texto
        self.assertNotIn("Erro ao gerar", str(clientes))
        planos = list(wb["Planos"].iter_rows(values_only=True))
        p = dict(zip(planos[0], next(r for r in planos if r[0] == "Zzz Plano Excel")))
        self.assertEqual((p["Valor"], p["Moeda"], p["À venda"], p["Clientes no plano"], p["Clientes com acesso liberado"]),
                         (79.9, "R$", "Sim", 1, 1))
        self.assertIn("Esporos", p["Abas liberadas"])
        self.assertIn("R$", wb["Planos"]["B2"].number_format)


class PlanoEmDolarTest(unittest.TestCase):
    """Plano com valor em U$, cobrado em reais pela cotacao do dia (pedido do
    Alan, 07/11/2026)."""

    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Dolar"):
            models.create_user("Zzz Teste Master Dolar", "x", is_master=True)

    def setUp(self):
        import cotacao
        self.cotacao = cotacao
        cotacao._cache.update(em=0.0, valor=None)
        self.patch = mock.patch.object(cotacao, "_buscar", return_value=(5.5, "2026-11-06"))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.cotacao._cache.update(em=0.0, valor=None)

    def _logado(self, user_id):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(user_id)
            s["_fresh"] = True
        return c

    def test_master_escolhe_dolar_e_preco_aparece_convertido(self):
        plano = models.salvar_plano(None, "Zzz Plano Dolar", "", 1, "MONTHLY", 3, 50, ["clima"], True, 9)
        master = self._logado(models.get_user_by_username("Zzz Teste Master Dolar")["id"])
        master.post("/admin/planos/salvar", data={
            "plano_ids": [str(plano)], f"nome__{plano}": "Zzz Plano Dolar", f"valor__{plano}": "20,00",
            f"moeda__{plano}": "USD", f"ciclo__{plano}": "MONTHLY", f"max_pontos__{plano}": "3",
            f"raio_km__{plano}": "50", f"ordem__{plano}": "9", f"recursos__{plano}": ["clima"], f"ativo__{plano}": "on",
        })
        p = models.get_plano(plano)
        self.assertEqual((p["valor"], p["moeda"], p["simbolo"]), (20.0, "USD", "U$"))
        html = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("Dólar hoje: <strong>R$ 5,5000</strong>", html)
        self.assertIn("≈ R$ 110,00 hoje", html)
        self.assertEqual(webapp._preco_plano(p), "U$ 20,00 (≈ R$ 110,00)")
        uid = models.criar_cliente("Zzz Cliente Dolar", "segredo1", "", "5565999990066", "", None)
        tela = self._logado(uid).get("/app/assinatura").get_data(as_text=True)
        self.assertIn("U$ 20,00 (≈ R$ 110,00)", tela)
        self.assertIn("Cobrado em reais, pela cotação do dólar do dia", tela)
        # cobranca sai em reais no Asaas
        with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_zzz"), \
                mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_zzz") as criar, \
                mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[]):
            self._logado(uid).post("/app/assinatura/assinar", data={"plano_id": str(plano), "cpf_cnpj": CPF_VALIDO})
        valor_cobrado, descricao = criar.call_args[0][1], criar.call_args[0][3]
        self.assertEqual(valor_cobrado, 110.0)
        self.assertIn("U$ 20,00 x R$ 5,5000", descricao)

    def test_sem_cotacao_nao_cobra(self):
        plano = models.salvar_plano(None, "Zzz Plano Dolar 2", "", 20, "MONTHLY", 3, 50, ["clima"], True, 9, moeda="USD")
        uid = models.criar_cliente("Zzz Cliente Dolar 2", "segredo1", "", "5565999990067", "", None)
        models.set_setting("cotacao_dolar", "")
        self.patch.stop()
        with mock.patch.object(self.cotacao, "_buscar", side_effect=OSError("fora do ar")), \
                mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_zzz2"), \
                mock.patch.object(webapp.asaas, "criar_assinatura") as criar:
            resp = self._logado(uid).post("/app/assinatura/assinar", data={"plano_id": str(plano), "cpf_cnpj": CPF_VALIDO},
                                          follow_redirects=True)
        self.patch.start()
        criar.assert_not_called()
        self.assertIn("cotação do dólar indisponível", resp.get_data(as_text=True))

    def test_cotacao_guardada_vale_quando_o_banco_central_cai(self):
        self.assertEqual(self.cotacao.dolar()["valor"], 5.5)  # busca e guarda
        self.cotacao._cache.update(em=0.0, valor=None)
        with mock.patch.object(self.cotacao, "_buscar", side_effect=OSError("fora do ar")):
            self.assertEqual(self.cotacao.dolar(), {"valor": 5.5, "data": "2026-11-06"})


class TabelaPrecificacaoTest(unittest.TestCase):
    """Tabela de apoio em U$ pela area monitorada, 10 colunas, raio calculado
    (pedido do Alan, 07/11/2026)."""

    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Tabela"):
            models.create_user("Zzz Teste Master Tabela", "x", is_master=True)
        if not models.get_user_by_username("Zzz Teste Admin Tabela"):
            models.create_user("Zzz Teste Admin Tabela", "x", is_admin=True)

    def setUp(self):
        import cotacao
        self.patch = mock.patch.object(cotacao, "dolar", return_value={"valor": 5.0, "data": "2026-11-06"})
        self.patch.start()
        models.set_setting("tabela_precificacao", "")

    def tearDown(self):
        self.patch.stop()
        models.set_setting("tabela_precificacao", "")

    def _logado(self, nome):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username(nome)["id"])
            s["_fresh"] = True
        return c

    def test_raio_pela_area(self):
        self.assertAlmostEqual(webapp._raio_km(100), 0.5642, places=3)  # 1 km2 = 100 ha
        self.assertAlmostEqual(webapp._raio_km(31415.93), 10.0, places=3)
        self.assertIsNone(webapp._raio_km(0))

    def test_tabela_padrao_e_salvar(self):
        master = self._logado("Zzz Teste Master Tabela")
        html = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("Tabela de apoio: preço em U$ pela área monitorada", html)
        self.assertEqual(html.count('data-campo="area"'), 10)  # 10 colunas
        self.assertIn('name="area_0" data-col="0" data-campo="area" value="100"', html)
        self.assertIn(">0,56<", html)  # raio de 100 ha
        dados = {f"area_{i}": "" for i in range(10)}
        dados.update({f"usd_ha_{i}": "" for i in range(10)})
        dados.update({"area_0": "1000", "usd_ha_0": "0.5", "area_1": "2500,5", "usd_ha_1": ""})
        resp = master.post("/admin/planos/tabela", data=dados)
        self.assertIn("#tabela-precificacao", resp.headers["Location"])
        tabela = webapp._tabela_precificacao()
        self.assertEqual(len(tabela), 10)
        self.assertEqual((tabela[0]["area_ha"], tabela[0]["usd_ha"], tabela[0]["total_usd"]), (1000, 0.5, 500))
        self.assertAlmostEqual(tabela[0]["raio_km"], 1.784, places=3)
        self.assertEqual((tabela[1]["area_ha"], tabela[1]["total_usd"]), (2500.5, None))
        self.assertIsNone(tabela[2]["area_ha"])  # coluna apagada continua vazia (nao volta o padrao)
        html = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("U$ 500,00", html)
        self.assertIn('name="area_0" data-col="0" data-campo="area" value="1000"', html)  # sem ".0"
        self.assertIn("R$ 2.500,00", html)  # 500 x 5,00
        ruim = master.post("/admin/planos/tabela", data={**dados, "usd_ha_0": "-1"}, follow_redirects=True)
        self.assertIn("sem negativos", ruim.get_data(as_text=True))
        self.assertEqual(self._logado("Zzz Teste Admin Tabela").post("/admin/planos/tabela", data=dados).status_code, 403)


if __name__ == "__main__":
    unittest.main()
