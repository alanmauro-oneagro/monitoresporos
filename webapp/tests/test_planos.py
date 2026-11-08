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


# Campos da tela "Nova fazenda" (tipo de cultura, cultura e area; 08/11/2026).
NOVA_FAZENDA = {"grupo_cultura": "cereais", "culturas": "Soja", "area_cultura__Soja": "100"}

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
        self.assertEqual((free["valor"], free["ativo"], free["recursos"]), (0, 1, ["clima", "whatsapp_texto"]))
        # PDF por WhatsApp so' nos Premium (pedido do Alan, 08/11/2026)
        self.assertIn("whatsapp_pdf", self.planos["Premium"]["recursos"])
        self.assertIn("whatsapp_pdf", self.planos["Premium + Estação"]["recursos"])
        self.assertNotIn("whatsapp_pdf", self.planos["Básico"]["recursos"])
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
        self.assertIn("/app/assinatura", c.get("/app/regiao").headers["Location"])  # sem plano: bloqueado
        resp = c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Free"]["id"])})
        self.assertTrue(resp.headers["Location"].endswith("/app"))
        assinatura = models.get_assinatura(uid)
        self.assertEqual((assinatura["plano_id"], assinatura["status"], assinatura["cpf_cnpj"]),
                         (self.planos["Free"]["id"], "ativa", ""))
        self.assertTrue(models.assinatura_liberada(assinatura))
        self.assertEqual(c.get("/app").status_code, 200)
        # Free vale 7 dias a partir do cadastro (pedido do Alan, 08/11/2026)
        self.assertEqual(assinatura["cortesia_ate"], models.fim_free(assinatura))
        self.assertEqual(models.fim_free({"criado_em": "2026-11-08 10:00:00"}), "2026-11-15")
        self.assertNotIn("Sem prazo", c.get("/app/assinatura").get_data(as_text=True))
        # cadastro com mais de 7 dias: o Free nao ativa mais
        conn = models.get_db()
        conn.execute("UPDATE assinaturas SET criado_em = '2026-01-01 08:00:00', plano_id = NULL, cortesia_ate = NULL WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        velho = c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Free"]["id"])}, follow_redirects=True)
        self.assertIn("vale 7 dias", velho.get_data(as_text=True))
        self.assertFalse(models.assinatura_liberada(models.get_assinatura(uid)))

    def test_free_antigo_sem_prazo_vira_7_dias(self):
        """Free "sem prazo" de antes da regra passa a 7 dias do cadastro no init_db (pedido do Alan, 08/11/2026)."""
        uid = self._cliente("Zzz Cliente Free Antigo")
        models.atualizar_assinatura(uid, plano_id=self.planos["Free"]["id"], status="ativa", cortesia_ate=models.SEM_PRAZO)
        conn = models.get_db()
        conn.execute("UPDATE assinaturas SET criado_em = '2026-10-20 09:00:00' WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        cortesia_pago = self._cliente("Zzz Cliente Cortesia Paga")
        pago = models.salvar_plano(None, "Zzz Plano Pago Cortesia", "", 300, "MONTHLY", 1, 50, ["clima"], True, 9)
        models.atualizar_assinatura(cortesia_pago, plano_id=pago, cortesia_ate=models.SEM_PRAZO)
        models.init_db()
        self.assertEqual(models.get_assinatura(uid)["cortesia_ate"], "2026-10-27")
        self.assertFalse(models.assinatura_liberada(models.get_assinatura(uid)))
        self.assertEqual(models.get_assinatura(cortesia_pago)["cortesia_ate"], models.SEM_PRAZO)  # so' o Free muda

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
        # ...mas ate' o pagamento ser aprovado ele usa o Free (nao o Basico)
        self.assertEqual(c.get("/app").status_code, 200)
        with webapp.app.test_request_context():
            from flask_login import login_user
            login_user(webapp.User(models.get_user_by_id(uid)))
            plano, liberada, free_ate = webapp._acesso_cliente()
            self.assertEqual((plano["nome"], liberada, free_ate), ("Free", True, models.fim_free(assinatura)))


    def test_upgrade_so_planos_acima(self):
        """Com plano escolhido a lista some; "Fazer upgrade de plano" mostra so' os
        planos acima do atual e troca a assinatura do Asaas (pedido do Alan, 08/11/2026)."""
        baixo = models.salvar_plano(None, "Zzz Up Baixo", "", 120, "MONTHLY", 1, 50, ["clima"], True, 51)
        alto = models.salvar_plano(None, "Zzz Up Alto", "", 240, "MONTHLY", 1, 50, ["clima", "site_mapa"], True, 52)
        uid = self._cliente("Zzz Cliente Upgrade")
        models.atualizar_assinatura(uid, plano_id=baixo, status="pendente", cpf_cnpj=CPF_VALIDO,
                                    asaas_subscription_id="sub_antiga")
        c = self._logado(uid)
        tela = c.get("/app/assinatura").get_data(as_text=True)
        self.assertNotIn('type="radio" name="plano_id"', tela)  # lista some
        self.assertNotIn("Ver minhas lavouras", tela)
        self.assertIn("Fazer upgrade de plano", tela)
        up = c.get("/app/assinatura?upgrade=1").get_data(as_text=True)
        self.assertIn(f'id="plano_{alto}"', up)
        self.assertNotIn(f'id="plano_{baixo}"', up)
        self.assertNotIn(f'id="plano_{self.planos["Free"]["id"]}"', up)
        self.assertIn('name="upgrade" value="1"', up)
        with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "cancelar_assinatura") as cancelar, \
                mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_up"), \
                mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_nova"), \
                mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[]):
            c.post("/app/assinatura/assinar", data={"plano_id": str(alto), "upgrade": "1"})
        cancelar.assert_called_once_with("sub_antiga")
        a = models.get_assinatura(uid)
        self.assertEqual((a["plano_id"], a["asaas_subscription_id"]), (alto, "sub_nova"))
        # sem plano acima do atual: sem botao de upgrade
        acima = [p for p in models.get_planos() if p["ativo"] and p["ordem"] > 52]
        if not acima:
            self.assertNotIn("Fazer upgrade de plano", c.get("/app/assinatura").get_data(as_text=True))

    def test_upgrade_desconta_o_que_ja_foi_pago(self):
        """Upgrade: o pago no plano atual (ainda valido) sai do total do novo,
        dividido nas parcelas do 1o ano (pedido do Alan, 08/11/2026)."""
        baixo = models.salvar_plano(None, "Zzz Desc Baixo", "", 120, "YEARLY", 1, 50, ["clima"], True, 61)
        alto = models.salvar_plano(None, "Zzz Desc Alto", "", 240, "MONTHLY", 1, 50, ["clima"], True, 62)
        topo = models.salvar_plano(None, "Zzz Desc Topo", "", 100, "MONTHLY", 1, 50, ["clima"], True, 63)
        uid = self._cliente("Zzz Cliente Desconto")
        models.atualizar_assinatura(uid, plano_id=baixo, status="ativa", cpf_cnpj=CPF_VALIDO,
                                    asaas_subscription_id="sub_baixo")
        hoje = models._hoje_cuiaba()
        models.registrar_pagamento(uid, "pay_baixo", 120.0, "CONFIRMED", hoje, None, "sub_baixo")
        models.registrar_pagamento(uid, "pay_velho", 999.0, "CONFIRMED", "2020-01-01", None, "sub_baixo")  # vencido: nao conta
        self.assertEqual(models.credito_para_upgrade(uid, "sub_baixo"), 120.0)
        c = self._logado(uid)
        tela = c.get("/app/assinatura?upgrade=1").get_data(as_text=True)
        self.assertIn("Você já pagou <strong>R$ 120,00</strong>", tela)
        self.assertIn("Com o desconto: 12x de R$ 10,00 (total R$ 120,00)", tela)  # (240 - 120) / 12
        self.assertIn("Com o desconto: sem nada a pagar", tela)  # topo de R$ 100 coberto
        with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "cancelar_assinatura"), \
                mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_d"), \
                mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_alto") as criar, \
                mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[]):
            c.post("/app/assinatura/assinar", data={"plano_id": str(alto), "upgrade": "1"})
        args = criar.call_args[0]
        self.assertEqual((args[1], args[6]), (10.0, 12))  # parcela com desconto, so' 12 cobrancas
        self.assertIn("desconto de R$ 120,00", args[3])
        self.assertEqual(models.credito_para_upgrade(uid, "sub_alto"), 0)  # credito nao e' usado de novo
        # credito que cobre o plano novo inteiro: troca sem cobranca
        uid2 = self._cliente("Zzz Cliente Desconto 2")
        models.atualizar_assinatura(uid2, plano_id=baixo, status="ativa", cpf_cnpj=CPF_VALIDO, asaas_subscription_id="sub_b2")
        models.registrar_pagamento(uid2, "pay_b2", 120.0, "CONFIRMED", hoje, None, "sub_b2")
        c2 = self._logado(uid2)
        with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "cancelar_assinatura"), \
                mock.patch.object(webapp.asaas, "criar_assinatura") as criar2:
            r = c2.post("/app/assinatura/assinar", data={"plano_id": str(topo), "upgrade": "1"}, follow_redirects=True)
        criar2.assert_not_called()
        self.assertIn("o valor já pago cobre o plano novo", r.get_data(as_text=True))
        self.assertEqual(models.get_assinatura(uid2)["plano_id"], topo)

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
        self.assertIn("🔔", c.get("/app").get_data(as_text=True))

        # lavoura nova vira fazenda estimada liberada so' pra ele
        resp = c.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao Zzz", "lat": "-12.5", "lon": "-55.7"})
        self.assertEqual(resp.status_code, 302)
        ponto = models.get_cliente_pontos(uid)[0]
        self.assertTrue(ponto["site_name"])
        vf = models.get_virtual_farm(ponto["site_name"])
        self.assertEqual((vf["nome"], vf["raio_km"], vf["tipo"]), ("Talhao Zzz (Abas)", 40, "doenca"))
        self.assertIn(ponto["site_name"], models.get_user_permitted_site_names(uid))
        # apagar a lavoura apaga a fazenda estimada
        c.post(f"/app/pontos/{ponto['id']}/excluir")
        self.assertIsNone(models.get_virtual_farm(ponto["site_name"]))

    def test_toda_lavoura_vira_fazenda_estimada_e_free_sem_site(self):
        """Toda lavoura ja' vira fazenda estimada liberada, na Programacao de Envios
        com o cliente recebendo -- sem configuracao da OneAgro (pedido do Alan, 08/11/2026)."""
        uid = self._cliente_no_plano("Estacao", ["clima", "site_mapa"])
        models.set_user_contato(uid, "", "5565999990131")
        c = self._logado(uid)
        c.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao Estacao", "lat": "-12.5", "lon": "-55.7"})
        site = models.get_cliente_pontos(uid)[0]["site_name"]
        self.assertIsNotNone(models.get_virtual_farm(site))
        self.assertIn(site, models.get_user_permitted_site_names(uid))
        self.assertTrue(models.get_all_whatsapp_days().get(site))  # agenda padrao
        self.assertIn("5565999990131", [r["telefone"] for r in models.get_site_whatsapp_recipients(site)])
        self.assertEqual(c.get("/mapa").status_code, 200)
        free = self._cliente_no_plano("Free Site", ["clima"])
        self.assertTrue(self._logado(free).get("/mapa").headers["Location"].endswith("/app"))

    def test_excluir_cliente_apaga_fazendas_estimadas(self):
        uid = self._cliente_no_plano("Excluir Site", ["clima", "site_mapa", "fazenda_estimada"])
        self._logado(uid).post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao Exc", "lat": "-12.5", "lon": "-55.7"})
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
                                   "tipo": "cliente", "user_id": uid, "grupo_cultura": "cereais"})
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
        self.assertEqual((valor["Tipo de cultura"], valor["Culturas"]), ("Cereais", "Soja"))
        self.assertIn("R$", wb["Clientes"].cell(row=clientes.index(linha) + 1, column=cab.index("Valor do plano") + 1).number_format)
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
            f"moeda__{plano}": "USD", f"ciclo__{plano}": "QUARTERLY", f"max_pontos__{plano}": "3",
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
        self.assertIn('<span class="preco-n">4x de</span> U$ 5,00</div>', tela)  # trimestral = 4 parcelas
        self.assertIn('<div class="preco-sub">≈ R$ 27,50 por parcela</div>', tela)
        self.assertIn('<div class="preco-sub">Total U$ 20,00 · ≈ R$ 110,00</div>', tela)
        self.assertIn("Cobrado em reais, pela cotação do dólar do dia", tela)
        # cobranca sai em reais no Asaas
        with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_zzz"), \
                mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_zzz") as criar, \
                mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[]):
            self._logado(uid).post("/app/assinatura/assinar", data={"plano_id": str(plano), "cpf_cnpj": CPF_VALIDO})
        valor_cobrado, descricao = criar.call_args[0][1], criar.call_args[0][3]
        self.assertEqual(valor_cobrado, 27.5)  # 1 parcela de U$ 5,00 x 5,50
        self.assertIn("U$ 5,00 x R$ 5,5000", descricao)
        self.assertIn("4x de U$ 5,00", descricao)

    def test_parcelas_pelo_ciclo(self):
        """Valor do plano = total; Mensal 12x, Trimestral 4x, Semestral 2x, Anual 1x (pedido do Alan, 08/11/2026)."""
        for ciclo, parcelas, parcela in (("MONTHLY", 12, 100.0), ("QUARTERLY", 4, 300.0), ("SEMIANNUALLY", 2, 600.0), ("YEARLY", 1, 1200.0)):
            pid = models.salvar_plano(None, f"Zzz Parcelas {ciclo}", "", 1200, ciclo, 1, 50, ["clima"], True, 9)
            p = models.get_plano(pid)
            self.assertEqual((p["parcelas"], p["valor_parcela"]), (parcelas, parcela), ciclo)
            models.excluir_plano(pid)
        pid = models.salvar_plano(None, "Zzz Parcelas Tela", "", 1200, "MONTHLY", 1, 50, ["clima"], True, 9)
        tela = webapp.app.test_client().get("/planos").get_data(as_text=True)
        self.assertIn('<div class="preco-parcela"><span class="preco-n">12x de</span> R$ 100,00</div>', tela)
        self.assertIn('<div class="preco-sub">Total R$ 1.200,00</div>', tela)
        self.assertLess(tela.index("R$ 100,00</div>"), tela.index("Total R$ 1.200,00"))  # total por ultimo
        # com a area plantada total: "R$ X por ha/ano" (pedido do Alan, 08/11/2026)
        p = models.get_plano(pid)
        with webapp.app.test_request_context():
            self.assertIn('<div class="preco-ha">R$ 2,40 por ha/ano</div>', str(webapp._parcelas_plano(p, 500)))
            self.assertNotIn("preco-ha", str(webapp._parcelas_plano(p)))
        models.excluir_plano(pid)

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

    def test_faixas_fixas_e_salvar(self):
        master = self._logado("Zzz Teste Master Tabela")
        html = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("Tabela de apoio: preço em U$ pela área monitorada", html)
        self.assertEqual(html.count(" data-campo=\"usd_ha\" value="), 9)  # 9 faixas fixas
        for rotulo in ("até 100 ha", "até 250 ha", "até 1.000 ha", "até 10.000 ha", "acima de 10.000 ha"):
            self.assertIn(f">{rotulo}<", html)
        self.assertIn(">0,56<", html)  # raio de 100 ha
        dados = {f"usd_ha_{i}": "" for i in range(9)}
        dados.update({"usd_ha_3": "0.5", "usd_ha_8": "0.2"})
        resp = master.post("/admin/planos/tabela", data=dados)
        self.assertIn("#tabela-precificacao", resp.headers["Location"])
        tabela = webapp._tabela_precificacao()
        self.assertEqual([f["ate_ha"] for f in tabela], [100, 250, 500, 1000, 2000, 3000, 5000, 10000, None])
        self.assertEqual((tabela[3]["usd_ha"], tabela[3]["total_usd"]), (0.5, 500))
        self.assertEqual((tabela[8]["usd_ha"], tabela[8]["total_usd"]), (0.2, None))  # acima de 10.000: depende da area
        html = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("U$ 500,00", html)
        self.assertIn("R$ 2.500,00", html)  # 500 x 5,00
        ruim = master.post("/admin/planos/tabela", data={**dados, "usd_ha_0": "-1"}, follow_redirects=True)
        self.assertIn("sem negativos", ruim.get_data(as_text=True))
        self.assertEqual(self._logado("Zzz Teste Admin Tabela").post("/admin/planos/tabela", data=dados).status_code, 403)

    def test_preco_pela_area_busca_a_faixa_de_cima(self):
        import json
        models.set_setting("tabela_precificacao", json.dumps(
            [{"ate_ha": a, "usd_ha": v} for a, v in ((100, 1), (250, 0.9), (500, 0.8), (1000, 0.7), (None, 0.1))]))
        p = webapp.preco_pela_area(300)
        self.assertEqual((p["faixa"], p["usd_ha"], p["total_usd"], p["total_brl"]), ("até 500 ha", 0.8, 240, 1200))
        self.assertAlmostEqual(p["raio_km"], webapp._raio_km(300))
        self.assertEqual(webapp.preco_pela_area(100)["faixa"], "até 100 ha")  # limite fica na propria faixa
        self.assertEqual(webapp.preco_pela_area("100.5")["faixa"], "até 250 ha")
        p = webapp.preco_pela_area(1500)  # faixa ate' 2.000 sem valor
        self.assertEqual((p["faixa"], p["usd_ha"], p["total_usd"]), ("até 2.000 ha", None, None))
        self.assertEqual(webapp.preco_pela_area(25000)["total_usd"], 2500)
        self.assertEqual(webapp.preco_pela_area(25000)["faixa"], "acima de 10.000 ha")
        self.assertIsNone(webapp.preco_pela_area(None))
        self.assertIsNone(webapp.preco_pela_area(0))

    def test_formato_antigo_aproveita_areas_iguais(self):
        import json
        models.set_setting("tabela_precificacao", json.dumps(
            [{"area_ha": 100, "usd_ha": 2}, {"area_ha": 7500, "usd_ha": 9}, {"area_ha": 1000, "usd_ha": None}]))
        tabela = {f["ate_ha"]: f["usd_ha"] for f in webapp._tabela_precificacao()}
        self.assertEqual(tabela[100], 2)
        self.assertIsNone(tabela[1000])
        self.assertNotIn(7500, tabela)

    def test_planos_em_tabela_comparativa(self):
        master = self._logado("Zzz Teste Master Tabela")
        html = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn('class="table table-sm table-bordered align-middle mb-0 tabela-planos"', html)
        planos = models.get_planos()
        # uma coluna por plano + "Novo": cada aba liberada vira uma linha com uma caixa por plano
        chave = models.RECURSOS_PLANO[0][0]
        self.assertEqual(html.count(f'value="{chave}" id="rec_'), len(planos) + 1)
        self.assertIn('name="nome__novo"', html)
        self.assertIn(">Abas liberadas no app<", html)



if __name__ == "__main__":
    unittest.main()


class PlanosLinhasForaDaTelaTest(unittest.TestCase):
    """Lavouras, Raio e 5 abas sairam da tabela (pedido do Alan, 07/11/2026),
    mas salvar a tela nao pode apagar o que o plano ja' tem."""

    def test_salvar_mantem_valores_escondidos(self):
        import re
        import html as html_mod
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Linhas"):
            models.create_user("Zzz Teste Master Linhas", "x", is_master=True)
        pid = models.salvar_plano(None, "Zzz Plano Linhas", "", 10.0, "MONTHLY", 7, 33,
                                  ["clima", "esporos", "regiao", "site_mapa"], True, 9)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Teste Master Linhas")["id"])
            s["_fresh"] = True
        pagina = c.get("/admin/planos").get_data(as_text=True)
        for rotulo in ("<td>Lavouras</td>", "<td>Raio (km)</td>", "Esporos estimados na região", "Mapa da região com as estações"):
            self.assertNotIn(rotulo, pagina)
        self.assertIn(">Site: Mapa<", pagina)
        # envia o formulario exatamente como a tela monta (sem mexer em nada)
        form = pagina.split('action="/admin/planos/salvar"', 1)[1].split("</form>", 1)[0]
        dados = {}
        for tag in re.findall(r"<(?:input|select|textarea)[^>]*>", form):
            nome = re.search(r'name="([^"]+)"', tag)
            if not nome:
                continue
            if 'type="checkbox"' in tag and "checked" not in tag:
                continue
            valor = re.search(r'value="([^"]*)"', tag)
            valor = html_mod.unescape(valor.group(1)) if valor else ("on" if 'type="checkbox"' in tag else "")
            if tag.startswith("<select"):
                bloco = form.split(tag, 1)[1].split("</select>", 1)[0]
                sel = re.search(r'<option value="([^"]+)"[^>]*selected', bloco)
                valor = sel.group(1) if sel else ""
            dados.setdefault(nome.group(1), []).append(valor)
        self.assertIn("Planos salvos", c.post("/admin/planos/salvar", data=dados, follow_redirects=True).get_data(as_text=True))
        plano = models.get_plano(pid)
        self.assertEqual((plano["max_pontos"], plano["raio_km"]), (7, 33))
        self.assertEqual(set(plano["recursos"]), {"clima", "esporos", "regiao", "site_mapa"})
        models.excluir_plano(pid)


class AppClienteCulturasSiteTest(unittest.TestCase):
    """App do cliente (pedido do Alan, 08/11/2026): "Lavouras" virou "Culturas"
    com os dados do cadastro; depois do login entra direto no Site; a barra de
    baixo aparece tambem nas abas do Site."""

    def test_login_vai_pro_site_e_barra_fixa(self):
        webapp.app.config["TESTING"] = True
        com_site = models.salvar_plano(None, "Zzz Plano Com Site", "", 0, "MONTHLY", 1, 30, ["clima", "site_mapa"], True, 9)
        sem_site = models.salvar_plano(None, "Zzz Plano Sem Site", "", 0, "MONTHLY", 1, 30, ["clima"], True, 9)
        a = models.criar_cliente("Zzz Cli Site", "segredo1", "", "5565999990161", "", com_site)
        b = models.criar_cliente("Zzz Cli Sem Site", "segredo1", "", "5565999990162", "", sem_site)
        for uid in (a, b):
            models.atualizar_assinatura(uid, status="ativa", cortesia_ate="2099-01-01")
        models.registrar_cadastro({"username": "Zzz Cli Site", "tipo": "cliente", "user_id": a, "grupo_cultura": "cereais",
                                   "culturas": "Soja (1.200 ha), Milho (800 ha)", "area_ha": 2000, "empresa": "Fazenda Zzz"})
        c = webapp.app.test_client()
        resp = c.post("/login", data={"username": "Zzz Cli Site", "password": "segredo1"}, follow_redirects=True)
        self.assertTrue(resp.request.path.endswith("/mapa"))  # direto no Site
        html = resp.get_data(as_text=True)
        self.assertIn('<nav class="abas">', html)  # barra de baixo tambem no Site
        self.assertIn("position: fixed; bottom: 0", html)
        home = c.get("/app").get_data(as_text=True)
        self.assertIn("Minha conta", home)
        self.assertIn('<span>Soja</span><span>1.200 ha</span>', home)
        # total em nome do dono da conta, nao da 1a fazenda (pedido do Alan, 08/11/2026)
        self.assertIn('<span>Área total · Zzz Cli Site</span>', home)
        self.assertNotIn("Fazenda Zzz", home)
        self.assertIn("Cereais", home)
        self.assertIn("</span>Conta</a>", home)
        self.assertNotIn("</span>Lavouras</a>", home)
        d = webapp.app.test_client()
        resp = d.post("/login", data={"username": "Zzz Cli Sem Site", "password": "segredo1"}, follow_redirects=True)
        self.assertTrue(resp.request.path.endswith("/app"))  # plano sem Site: Culturas
