"""Fazenda adicional do cliente (pedido do Alan, 08/11/2026): tela nova com
tipo/culturas/area, cobranca da area a mais (diferenca pela tabela de apoio,
mesmas parcelas do plano), 7 dias gratis e depois bloqueio; cliente sem
acesso cai na Assinatura so' com Conta e Assinatura; forma de pagamento.

Rodar a partir de webapp/:  python -m unittest tests.test_fazenda_adicional
"""
import json
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

FAIXAS = [100, 250, 500, 1000, 2000, 3000, 5000, 10000, None]


class FazendaAdicionalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        models.init_db()
        cls.basico = next(p for p in models.get_planos() if p["nome"] == "Básico")
        models.salvar_plano(cls.basico["id"], "Básico", "", 120.0, "MONTHLY", 3, 50, ["clima", "esporos", "regiao"], True, 2)

    def setUp(self):
        import cotacao
        self.patch = mock.patch.object(cotacao, "dolar", return_value={"valor": 5.0, "data": "2026-11-06"})
        self.patch.start()
        models.set_setting("tabela_precificacao", json.dumps([{"ate_ha": f, "usd_ha": 2.0} for f in FAIXAS]))

    def tearDown(self):
        self.patch.stop()
        models.set_setting("tabela_precificacao", "")

    def _cliente_pagante(self, nome):
        if models.get_user_by_username(nome):
            models.delete_user(models.get_user_by_username(nome)["id"])
        uid = models.criar_cliente(nome, "segredo1", "", "5565999991234", "52998224725", self.basico["id"])
        models.registrar_cadastro({"username": nome, "tipo": "cliente", "user_id": uid, "grupo_cultura": "cereais",
                                   "culturas": "Soja (300 ha)", "area_ha": 300, "area_preco_ha": 300})
        models.atualizar_assinatura(uid, asaas_customer_id="cus_x", asaas_subscription_id="sub_principal", status="ativa")
        models.registrar_pagamento(uid, f"pay_principal_{uid}", 10.0, "CONFIRMED", models._hoje_cuiaba(), None, "sub_principal")
        return uid

    def _logado(self, uid):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        return c

    def _nova(self, c, **extra):
        dados = {"nome": "Faz Mourão", "cidade": "Sorriso", "uf": "MT", "grupo_cultura": "cereais",
                 "culturas": ["Soja", "Milho"], "area_cultura__Soja": "100", "area_cultura__Milho": "50",
                 "lat": "-12.5", "lon": "-55.7"}
        dados.update(extra)
        return c.post("/app/pontos/novo", data=dados)

    def test_tela_tem_tipo_cultura_e_area(self):
        uid = self._cliente_pagante("Zzz Adic Tela")
        html = self._logado(uid).get("/app/pontos/novo").get_data(as_text=True)
        self.assertIn('placeholder="Nome (ex.: Faz Mourão)"', html)
        for rotulo in ("Cereais", "HF", "Outros", "Área total", 'name="area_cultura__Soja"'):
            self.assertIn(rotulo, html)
        # sem tipo de cultura / sem area: nao cria
        c = self._logado(uid)
        self._nova(c, grupo_cultura="")
        self._nova(c, area_cultura__Soja="", area_cultura__Milho="")
        self.assertEqual(models.get_cliente_pontos(uid), [])

    def test_area_a_mais_gera_cobranca_e_bloqueia_depois_de_7_dias(self):
        uid = self._cliente_pagante("Zzz Adic Cobra")
        c = self._logado(uid)
        with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_fazenda") as criar, \
                mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[
                    {"id": "pay_faz", "value": 83.33, "status": "PENDING", "dueDate": models._hoje_cuiaba(),
                     "invoiceUrl": "https://fatura/faz"}]):
            resp = self._nova(c)
        ponto = models.get_cliente_pontos(uid)[0]
        # area do preco: Cereais = Soja -> 300 + 100 ha; (400 - 300) x U$ 2 = U$ 200 = R$ 1.000 em 12x
        self.assertEqual((ponto["adicional_status"], ponto["adicional_total"]), ("pendente", 200.0))
        args = criar.call_args[0]
        self.assertEqual((args[0], args[1], args[2], args[5]), ("cus_x", 83.33, "MONTHLY", f"fazenda:{ponto['id']}"))
        self.assertEqual(ponto["culturas"], "Soja (100 ha), Milho (50 ha)")
        cad = models.get_cadastro_do_usuario(uid)
        self.assertEqual((cad["area_ha"], cad["area_preco_ha"]), (450, 400))
        self.assertIn("Soja (400 ha)", cad["culturas"])
        site = ponto["site_name"]
        # 7 dias gratis: a fazenda aparece
        self.assertIn(site, models.get_user_permitted_site_names(uid))
        self.assertIn("Aguardando pagamento", c.get("/app").get_data(as_text=True))
        assin = c.get("/app/assinatura").get_data(as_text=True)
        self.assertIn("Fazendas adicionais", assin)
        self.assertIn("https://fatura/faz", assin)
        # passou dos 7 dias sem pagar: some das telas, pagina bloqueada, sem relatorio
        conn = models.get_db()
        conn.execute("UPDATE cliente_pontos SET criado_em = '2026-01-01 08:00:00' WHERE id = ?", (ponto["id"],))
        conn.commit()
        conn.close()
        self.assertNotIn(site, models.get_user_permitted_site_names(uid))
        self.assertIn("O acesso a esta fazenda terminou", c.get(f"/app/pontos/{ponto['id']}").get_data(as_text=True))
        self.assertEqual(webapp._send_site_whatsapp(site)[2], "Fazenda adicional sem pagamento -- relatório não enviado.")
        # o plano continua valendo
        self.assertEqual(c.get("/app/regiao").status_code, 200)
        # pagou (webhook): volta, e o pagamento nao mexe na validade do plano
        pago_antes = models.get_assinatura(uid)["pago_ate"]
        with mock.patch.object(webapp.asaas, "token_webhook", return_value="tok"):
            webapp.app.test_client().post("/webhooks/asaas", headers={"asaas-access-token": "tok"}, json={
                "event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_faz", "value": 83.33, "status": "CONFIRMED",
                                                          "dueDate": models._hoje_cuiaba(), "subscription": "sub_fazenda"}})
        self.assertIn(site, models.get_user_permitted_site_names(uid))
        self.assertEqual(models.get_cliente_ponto(uid, ponto["id"])["adicional_status"], "ativa")
        self.assertEqual(models.get_assinatura(uid)["pago_ate"], pago_antes)
        self.assertEqual(models.credito_para_upgrade(uid, "sub_principal"), 10.0)  # fazenda nao vira credito

    def test_free_nao_cobra_area_a_mais(self):
        uid = models.criar_cliente("Zzz Adic Free", "segredo1", "", "5565999991235", "", models.plano_free()["id"])
        models.atualizar_assinatura(uid, status="ativa", cortesia_ate="2099-01-01")
        with mock.patch.object(webapp.asaas, "criar_assinatura") as criar:
            self._nova(self._logado(uid))
        criar.assert_not_called()
        ponto = models.get_cliente_pontos(uid)[0]
        self.assertIsNone(ponto["adicional_status"])
        self.assertTrue(models.ponto_liberado(ponto))

    def test_sem_acesso_cai_na_assinatura_so_com_conta_e_assinatura(self):
        uid = models.criar_cliente("Zzz Adic Vencido", "segredo1", "", "5565999991236", "", self.basico["id"])
        conn = models.get_db()
        conn.execute("UPDATE assinaturas SET criado_em = '2026-01-01 08:00:00' WHERE user_id = ?", (uid,))
        conn.commit()
        conn.close()
        c = webapp.app.test_client()
        resp = c.post("/login", data={"username": "Zzz Adic Vencido", "password": "segredo1"}, follow_redirects=True)
        self.assertEqual(resp.request.path, "/app/assinatura")
        assin = c.get("/app/assinatura").get_data(as_text=True)
        self.assertNotIn("</span>Gráficos</a>", assin)
        self.assertNotIn("</span>Alertas</a>", assin)
        self.assertIn("</span>Assinatura</a>", assin)
        self.assertIn("</span>Conta</a>", assin)
        conta = c.get("/app")
        self.assertEqual(conta.status_code, 200)
        self.assertNotIn("+ Adicionar fazenda monitorada", conta.get_data(as_text=True))
        self.assertIn("/app/assinatura", c.get("/app/regiao").headers["Location"])

    def test_forma_de_pagamento(self):
        uid = self._cliente_pagante("Zzz Adic Forma")
        c = self._logado(uid)
        html = c.get("/app/assinatura").get_data(as_text=True)
        self.assertIn("Forma de pagamento", html)
        self.assertIn("PIX", html)
        self.assertIn("Cartão de crédito", html)
        with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                mock.patch.object(webapp.asaas, "alterar_forma_pagamento") as alterar:
            c.post("/app/assinatura/forma-pagamento", data={"forma_pagamento": "CREDIT_CARD"})
        alterar.assert_called_once_with("sub_principal", "CREDIT_CARD")
        self.assertEqual(models.get_assinatura(uid)["forma_pagamento"], "CREDIT_CARD")
        c.post("/app/assinatura/forma-pagamento", data={"forma_pagamento": "BOLETO"})
        self.assertEqual(models.get_assinatura(uid)["forma_pagamento"], "CREDIT_CARD")


    def test_editar_dados_da_conta_e_fazenda(self):
        """Botao Editar na Conta e em cada fazenda: corrigir tudo que foi
        digitado no cadastro (pedido do Alan, 08/11/2026)."""
        uid = self._cliente_pagante("Zzz Adic Editar")
        models.criar_cliente("Zzz Adic Outro", "segredo1", "outro@zzz.com", "5565999991299", "", None)
        c = self._logado(uid)
        with mock.patch.object(webapp.asaas, "configurado", return_value=False):
            self._nova(c)
        ponto = models.get_cliente_pontos(uid)[0]
        self.assertIn("/app/conta/editar", c.get("/app").get_data(as_text=True))
        # dados da conta: celular de outra conta e' recusado
        r = c.post("/app/conta/editar", data={"nome_completo": "Zzz Joao Editado", "grupo_economico": "Grupo Zzz",
                                             "codigo_pais": "55", "numero_telefone": "65999991299", "email": ""},
                   follow_redirects=True)
        self.assertIn("Esse celular já está em outra conta", r.get_data(as_text=True))
        c.post("/app/conta/editar", data={"nome_completo": "Zzz Joao Editado", "grupo_economico": "Grupo Zzz",
                                          "codigo_pais": "55", "numero_telefone": "(65)99999-1300", "email": "joao@zzz.com"})
        user, cad = models.get_user_by_id(uid), models.get_cadastro_do_usuario(uid)
        self.assertEqual((user["telefone"], user["email"]), ("5565999991300", "joao@zzz.com"))
        self.assertEqual((cad["nome_completo"], cad["grupo_economico"]), ("Zzz Joao Editado", "Grupo Zzz"))
        # trocar o grupo renomeia a fazenda
        ponto = models.get_cliente_ponto(uid, ponto["id"])
        self.assertEqual(models.get_virtual_farm(ponto["site_name"])["nome"], "Grupo Zzz - Faz Mourão")
        # fazenda: tela de edicao ja' vem preenchida
        tela = c.get(f"/app/pontos/{ponto['id']}/editar").get_data(as_text=True)
        self.assertIn('value="Faz Mourão"', tela)
        self.assertIn('name="area_cultura__Soja" value="100"', tela)
        c.post(f"/app/pontos/{ponto['id']}/editar", data={
            "nome": "Faz Mourão II", "cidade": "Sinop", "uf": "MT", "grupo_cultura": "cereais", "culturas": ["Soja"],
            "area_cultura__Soja": "250", "lat": "-12.6", "lon": "-55.8"})
        ponto = models.get_cliente_ponto(uid, ponto["id"])
        self.assertEqual((ponto["nome"], ponto["culturas"], ponto["area_ha"], ponto["cidade"], ponto["lat"]),
                         ("Faz Mourão II", "Soja (250 ha)", 250.0, "Sinop", -12.6))
        vf = models.get_virtual_farm(ponto["site_name"])
        self.assertEqual((vf["nome"], vf["lat"]), ("Grupo Zzz - Faz Mourão II", -12.6))
        cad = models.get_cadastro_do_usuario(uid)
        self.assertEqual((cad["culturas"], cad["area_ha"], cad["empresa"]), ("Soja (250 ha)", 250.0, "Faz Mourão II"))

    def test_relatorio_txt_e_edicao_automatica(self):
        """Aba Relatorios (texto da fazenda com cabecalho, pra WhatsApp) e
        edicao salva sozinha (X-Autosave -> JSON) -- pedido do Alan, 08/11/2026."""
        uid = self._cliente_pagante("Zzz Adic Relat")
        c = self._logado(uid)
        with mock.patch.object(webapp.asaas, "configurado", return_value=False):
            self._nova(c)
        ponto = models.get_cliente_pontos(uid)[0]
        with mock.patch.object(webapp, "_texto_relatorio", return_value="*Faz Mourão*\nClima ok"):
            tela = c.get("/app/relatorios").get_data(as_text=True)
            ver = c.get(f"/app/relatorios/{ponto['id']}.txt?ver=1")
            txt = c.get(f"/app/relatorios/{ponto['id']}.txt")
        # cartoes recolhidos por fazenda; o texto carrega ao abrir (08/11/2026)
        self.assertIn('class="toggle-icon">▶</span>Faz Mourão', tela)
        self.assertIn(f"/app/relatorios/{ponto['id']}.txt?ver=1", tela)
        self.assertNotIn("Content-Disposition", ver.headers)
        self.assertTrue(ver.get_data(as_text=True).startswith("━" * 20 + "\n*RELATÓRIO DE MONITORAMENTO*\n*ONEAGRO - "))
        self.assertIn("Clima ok", ver.get_data(as_text=True))
        self.assertEqual(txt.mimetype, "text/plain")
        self.assertIn("attachment", txt.headers["Content-Disposition"])
        self.assertIn("*RELATÓRIO DE MONITORAMENTO*", txt.get_data(as_text=True))
        # edicao automatica: sem botao Salvar, devolve JSON
        tela = c.get(f"/app/pontos/{ponto['id']}/editar").get_data(as_text=True)
        self.assertNotIn("Salvar alterações", tela)
        self.assertIn("salvas automaticamente", tela)
        r = c.post(f"/app/pontos/{ponto['id']}/editar", headers={"X-Autosave": "1"}, data={
            "nome": "Faz Auto", "grupo_cultura": "cereais", "culturas": ["Soja"], "area_cultura__Soja": "120",
            "lat": "-12.5", "lon": "-55.7"})
        self.assertEqual(r.get_json(), {"ok": True})
        self.assertEqual(models.get_cliente_ponto(uid, ponto["id"])["nome"], "Faz Auto")
        r = c.post(f"/app/pontos/{ponto['id']}/editar", headers={"X-Autosave": "1"}, data={"nome": "", "lat": "-12.5", "lon": "-55.7"})
        self.assertFalse(r.get_json()["ok"])
        r = c.post("/app/conta/editar", headers={"X-Autosave": "1"}, data={
            "nome_completo": "Zzz Auto Nome", "grupo_economico": "", "codigo_pais": "55", "numero_telefone": "65999991400"})
        self.assertEqual(r.get_json(), {"ok": True})
        self.assertNotIn("Salvar alterações", c.get("/app/conta/editar").get_data(as_text=True))

    def test_cabecalho_todos_os_relatorios_e_free_enxuto(self):
        """Cabecalho no formato do Alan, todas as fazendas numa mensagem so',
        relatorio do Free sem Clima agora/Ultima leitura/Produtos (08/11/2026)."""
        uid = self._cliente_pagante("Zzz Adic Cabec")
        models.atualizar_dados_cadastro(uid, grupo_economico="Grupo Cab")
        c = self._logado(uid)
        with mock.patch.object(webapp.asaas, "configurado", return_value=False):
            self._nova(c)
            self._nova(c, nome="Faz Retiro", lat="-12.9")
        pontos = models.get_cliente_pontos(uid)
        corpo = "*GRUPO CAB - FAZ MOURÃO - OneAgro*\n_Recomendações todas as safras · Cultura: SOJA / MILHO_\n\nClima ok"
        with mock.patch.object(webapp, "_texto_relatorio", return_value=corpo):
            um = c.get(f"/app/relatorios/{pontos[0]['id']}.txt?ver=1").get_data(as_text=True)
            todos = c.get("/app/relatorios/todos.txt?ver=1").get_data(as_text=True)
            tela = c.get("/app/relatorios").get_data(as_text=True)
        linhas = um.split("\n")
        self.assertEqual(linhas[:3], ["━" * 20, "*RELATÓRIO DE MONITORAMENTO*", f"*ONEAGRO - {webapp._hoje_cuiaba():%d/%m/%Y}*"])
        self.assertEqual(linhas[3:9], ["", "*Grupo Cab - FAZ MOURÃO*", "",
                                       "*Informações relevantes para manejo da(s) cultura(s) de: SOJA / MILHO*", "━" * 20, "Clima ok"])
        self.assertEqual(todos.count("*RELATÓRIO DE MONITORAMENTO*"), 1)
        self.assertEqual(todos.count("Clima ok"), 2)
        self.assertIn("Todas as fazendas (uma mensagem só)", tela)
        # Free: sem Clima agora, Ultima leitura e Produtos
        weather = {"temperatura_atual": 25, "umidade_atual": 80, "previsao_5_dias": [{"data": "2026-11-09", "chuva_mm": 3}]}
        args = dict(weather=weather, produtos={}, leitura_bioscout="07/11/26")
        with webapp.app.test_request_context():
            cheio = webapp._format_whatsapp_message(pontos[0]["site_name"], [], **args)
            enxuto = webapp._format_whatsapp_message(pontos[0]["site_name"], [], simples=True, **args)
        self.assertIn("Clima agora", cheio)
        self.assertNotIn("Clima agora", enxuto)
        self.assertNotIn("Última leitura BioScout", enxuto)
        self.assertIn("Previsão de chuva", enxuto)
        with webapp.app.test_request_context():
            self.assertFalse(webapp._relatorio_simples(pontos[0]["site_name"]))  # Basico
        free = models.criar_cliente("Zzz Adic Free2", "segredo1", "", "5565999991401", "", models.plano_free()["id"])
        models.atualizar_assinatura(free, status="ativa", cortesia_ate="2099-01-01")
        with mock.patch.object(webapp.asaas, "configurado", return_value=False):
            self._nova(self._logado(free), nome="Faz Free")
        with webapp.app.test_request_context():
            self.assertTrue(webapp._relatorio_simples(models.get_cliente_pontos(free)[0]["site_name"]))

if __name__ == "__main__":
    unittest.main()
