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
        for nome in ("Essencial", "Básico", "Avançado", "Inteligência"):
            self.assertIn(nome, self.planos)
        free = self.planos["Essencial"]
        self.assertEqual((free["valor"], free["ativo"], free["recursos"]), (0, 1, ["clima", "whatsapp_texto"]))
        # PDF por WhatsApp so' nos Premium (pedido do Alan, 08/11/2026)
        self.assertIn("whatsapp_pdf", self.planos["Avançado"]["recursos"])
        self.assertIn("whatsapp_pdf", self.planos["Inteligência"]["recursos"])
        self.assertNotIn("whatsapp_pdf", self.planos["Básico"]["recursos"])
        self.assertEqual(self.planos["Avançado"]["ativo"], 0)  # pago nasce fora de venda ate' ter preco
        models.init_db()  # rodar de novo nao duplica
        self.assertEqual(sum(1 for p in models.get_planos() if p["nome"] == "Essencial"), 1)

    def test_plano_renomeado_ou_excluido_nao_volta(self):
        # Pedido do Alan, 13/11/2026: o init_db recriava o plano padrao a cada subida.
        plano = self.planos["Inteligência"]
        conn = models.get_db()
        conn.execute("UPDATE planos SET nome = 'Zzz Teste Inteligencia' WHERE id = ?", (plano["id"],))
        conn.commit()
        conn.close()
        try:
            models.init_db()
            self.assertFalse(any(p["nome"] == "Inteligência" for p in models.get_planos()))
        finally:
            conn = models.get_db()
            conn.execute("UPDATE planos SET nome = 'Inteligência' WHERE id = ?", (plano["id"],))
            conn.commit()
            conn.close()

    def test_master_define_preco_e_abas(self):
        master = self._logado(models.get_user_by_username("Zzz Teste Master Planos")["id"])
        premium = self.planos["Avançado"]
        basico = self.planos["Básico"]
        k, b = premium["id"], basico["id"]
        # um envio so' grava todos os planos da tela
        master.post("/admin/planos/salvar", data={
            "plano_ids": [str(k), str(b)],
            f"nome__{k}": "Avançado", f"valor__{k}": "149,90", f"ciclo__{k}": "MONTHLY", f"max_pontos__{k}": "10",
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
        self.assertNotIn(">Salvar</button>", html)  # salva sozinho (12/11/2026)
        self.assertIn('action="/admin/planos/salvar" data-autosave', html)
        # com um plano errado, nenhum e' gravado
        negativo = master.post("/admin/planos/salvar", data={
            "plano_ids": [str(k), str(b)],
            f"nome__{k}": "Avançado", f"valor__{k}": "-1", f"ciclo__{k}": "MONTHLY", f"max_pontos__{k}": "10", f"raio_km__{k}": "50",
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
        self.assertIn("Inteligência", html)
        self.assertIn("Em breve", html)
        self.assertIn(f'id="plano_{self.planos["Inteligência"]["id"]}"', html)
        self.assertNotIn("Aguardando pagamento", html)  # situacao so' depois de escolher
        forcado = c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Inteligência"]["id"])},
                         follow_redirects=True)
        self.assertIn("Escolha um plano", forcado.get_data(as_text=True))
        self.assertIsNone(models.get_assinatura(uid)["plano_id"])
        self.assertIn("/app/assinatura", c.get("/app/regiao").headers["Location"])  # sem plano: bloqueado
        resp = c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Essencial"]["id"])})
        self.assertTrue(resp.headers["Location"].endswith("/app"))
        assinatura = models.get_assinatura(uid)
        self.assertEqual((assinatura["plano_id"], assinatura["status"], assinatura["cpf_cnpj"]),
                         (self.planos["Essencial"]["id"], "ativa", ""))
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
        velho = c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Essencial"]["id"])}, follow_redirects=True)
        self.assertIn("vale 7 dias", velho.get_data(as_text=True))
        self.assertFalse(models.assinatura_liberada(models.get_assinatura(uid)))

    def test_free_antigo_sem_prazo_vira_7_dias(self):
        """Free "sem prazo" de antes da regra passa a 7 dias do cadastro no init_db (pedido do Alan, 08/11/2026)."""
        uid = self._cliente("Zzz Cliente Free Antigo")
        models.atualizar_assinatura(uid, plano_id=self.planos["Essencial"]["id"], status="ativa", cortesia_ate=models.SEM_PRAZO)
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
        c.post("/app/assinatura/assinar", data={"plano_id": str(self.planos["Essencial"]["id"])})
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
            self.assertEqual((plano["nome"], liberada, free_ate), ("Essencial", True, models.fim_free(assinatura)))


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
        self.assertNotIn(f'id="plano_{self.planos["Essencial"]["id"]}"', up)
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
        # topo do cliente igual no Site e no app, com as abas no menu ☰ (08/11/2026)
        self.assertIn('id="menuCliente"', menu)
        self.assertIn("📄 Relatórios", menu)
        self.assertIn('id="menuCliente"', c.get("/app").get_data(as_text=True))
        self.assertNotIn('href="/graficos"', menu)
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


def webapp_sem_cadastro():
    with webapp.app.test_request_context():
        return webapp._fazendas_sem_cadastro()


class ModeloUnicoFazendasTest(unittest.TestCase):
    """Nomes novos dos planos e o modelo unico de fazenda em Gerenciar Clientes
    (pedidos do Alan, 09/11/2026): toda fazenda pertence a um cadastro, que da'
    plano, validade e grupo; BioScout = Inteligência."""

    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        models.init_db()
        for nome, kw in (("Zzz Master Modelo", {"is_master": True}), ("Zzz Admin Modelo", {"is_admin": True})):
            if not models.get_user_by_username(nome):
                models.create_user(nome, "x", **kw)
        cls.real = webapp.read_sites()[0]

    def _logado(self, nome):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username(nome)["id"])
            s["_fresh"] = True
        return c

    def _plano(self, nome):
        return next(p for p in models.get_planos() if p["nome"] == nome)

    def _selo(self, site):
        with webapp.app.test_request_context():
            return webapp._plano_da_fazenda(site)["texto"]

    def test_dados_da_fazenda_preenchidos_sozinhos(self):
        """Estado pelo nome, culturas pelas safras de Manejos e municipio pela
        coordenada; nunca troca o que ja' foi digitado (pedido do Alan, 10/11/2026)."""
        site = models.create_virtual_farm("Zzz Auto Dados - MT", -11.86, -55.5, 30)
        models.set_farm_cultura(site, "safra1", "Soja")
        models.set_farm_cultura(site, "safra2", "Milho")
        with webapp.app.test_request_context():
            fz = webapp._dados_da_fazenda(site)
            self.assertIn("município/estado de", " ".join(webapp._faltando_na_fazenda(fz)))
            webapp._completar_dados_fazendas([fz])
        dados = models.get_fazendas_dados()[site]
        self.assertEqual((dados["uf"], dados["culturas"]), ("MT", "Soja, Milho"))
        with webapp.app.test_request_context():
            webapp._gravar_dados_vazios(site, "Sinop", "PR", "Trigo")
        dados = models.get_fazendas_dados()[site]
        self.assertEqual((dados["cidade"], dados["uf"], dados["culturas"]), ("Sinop", "MT", "Soja, Milho"))
        with webapp.app.test_request_context():
            self.assertEqual(webapp._faltando_na_fazenda(webapp._dados_da_fazenda(site)), [])
        # fora do Brasil basta o municipio
        self.assertEqual(webapp._faltando_na_fazenda({"cidade": "Pencahue", "uf": "", "culturas": "Uva",
                                                      "country_code": "CL", "nome_exibicao": "x"}), [])

    def test_nomes_antigos_viram_novos_no_init_db(self):
        essencial = self._plano("Essencial")
        conn = models.get_db()
        conn.execute("UPDATE planos SET nome = 'Free' WHERE id = ?", (essencial["id"],))
        conn.execute("DELETE FROM app_settings WHERE key = 'planos_padrao_feitos'")  # banco antigo, antes da troca
        conn.commit()
        conn.close()
        models.init_db()
        self.assertNotIn("Free", [p["nome"] for p in models.get_planos()])
        self.assertEqual(self._plano("Essencial")["id"], essencial["id"])
        self.assertEqual(models.plano_free()["id"], essencial["id"])

    def test_cadastro_manual_com_fazendas_da_bioscout_e_do_estudo(self):
        master = self._logado("Zzz Master Modelo")
        e1 = models.create_virtual_farm("Zzz Modelo Est 1", -12.41, -55.7, 30)
        e2 = models.create_virtual_farm("Zzz Modelo Est 2", -12.42, -55.7, 30)
        self.assertEqual(self._selo(e1), "Sem plano")
        self.assertEqual(self._selo(self.real), "Inteligência")
        # dados completados antes de ter cadastro (fazendas_dados) vao junto ao ligar
        # culturas no mesmo formato do app: tipo + caixa por cultura com a area (09/11/2026)
        r = master.post("/admin/fazendas/dados/salvar", headers={"X-Autosave": "1"}, data={
            "site_name": e1, "cidade": "Sorriso", "uf": "MT", "grupo_cultura": "cereais", "culturas": ["Soja", "Milho"],
            "area_cultura__Soja": "100", "area_cultura__Milho": "50"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(models.get_fazendas_dados()[e1]["culturas"], "Soja (100 ha), Milho (50 ha)")
        # grupo numa fazenda ainda sem cadastro (agrupa em Recomendacoes)
        master.post("/admin/fazendas/grupo/salvar", headers={"X-Autosave": "1"},
                    data={"site_name": e2, "grupo_economico": "Grupo Zzz Solto"})
        with webapp.app.test_request_context():
            self.assertEqual(webapp._grupo_economico_da_fazenda(e2), "Grupo Zzz Solto")
        tela = master.get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("Fazendas sem cadastro", tela)
        self.assertIn("+ Novo cliente", tela)
        # fazenda sem cadastro = cartao igual ao dos produtores, com o selo e o filtro de incompleto (09/11/2026)
        self.assertIn('data-tipo="fazenda"', tela)
        # cartao da fazenda sem cadastro aberto nao repete nome/plano/origem (10/11/2026)
        cartao = tela.split('data-tipo="fazenda"', 1)[1].split('data-tipo=', 1)[0]
        self.assertEqual(cartao.count("Fazenda sem cadastro"), 1)
        self.assertEqual(cartao.count("selo-plano"), 1)
        clima = models.create_virtual_farm("Zzz Modelo So Clima", -12.44, -55.7, 30, tipo="clima")
        self.assertNotIn(clima, webapp_sem_cadastro())  # ponto so' clima: so' no Mapas de Estudo (09/11/2026)
        oculta = models.create_virtual_farm("Zzz Modelo Oculta", -12.45, -55.7, 30)
        models.set_site_ativo(oculta, False)
        self.assertNotIn(oculta, webapp_sem_cadastro())  # oculta vai pra Gerenciar Fazendas Ocultas
        self.assertIn("🙈 Ocultar", tela)
        self.assertNotIn('data-tipo="fazenda" data-novo="0" data-incompleto="1"', tela)  # sem o selo (10/11/2026)
        # fazenda sem cadastro com municipio e culturas: sem o selo de incompleto (09/11/2026)
        with webapp.app.test_request_context():
            fz = webapp._dados_da_fazenda(e1)
        self.assertEqual(webapp._faltando_na_fazenda(fz), [])
        self.assertIn('data-filtro="incompleto"', tela)
        self.assertIn("Estação meteorológica de referência (muda em Dados da fazenda)", tela)  # estacao no cartao (09/11/2026)
        editor_livre = master.get("/admin/fazendas/dados", query_string={"site": e2}).get_data(as_text=True)
        # ligar a um usuario da aba Usuarios = liberar + receber; sem ligacao mostra "-" (10/11/2026)
        self.assertIn('<option value="" selected>-</option>', editor_livre)
        self.assertIn("<strong>-</strong>", editor_livre)
        if not models.get_user_by_username("Zzz Usuario Ligar"):
            models.create_user("Zzz Usuario Ligar", "x")
        equipe = models.get_user_by_username("Zzz Usuario Ligar")
        r = master.post(f"/admin/usuarios/{equipe['id']}/ligar-fazenda", data={"site_name": e2})
        self.assertEqual(r.status_code, 302)
        self.assertEqual([u["username"] for u in models.usuarios_ligados_a_fazenda(e2)], ["Zzz Usuario Ligar"])
        self.assertIn(e2, webapp_sem_cadastro())  # continua sem cadastro de cliente
        master.post(f"/admin/usuarios/{equipe['id']}/desligar-fazenda", data={"site_name": e2})
        self.assertEqual(models.usuarios_ligados_a_fazenda(e2), [])
        self.assertIn("ligar-a-cliente", editor_livre) if models.get_all_clientes() else None
        # exibir por grupo: a fazenda solta com grupo entra na caixa do grupo
        master.post("/admin/fazendas/grupo/salvar", headers={"X-Autosave": "1"},
                    data={"site_name": e1, "grupo_economico": "Grupo Zzz Exibir"})
        por_grupo = master.get("/admin/solicitacoes?exibir=grupo").get_data(as_text=True)
        self.assertIn("👥 Grupo Zzz Exibir", por_grupo)
        # situacao na caixa fechada, sem abrir (pedido do Alan, 10/11/2026)
        caixa = por_grupo.split("👥 Grupo Zzz Exibir", 1)[1].split("grupo-body-", 1)[0]
        self.assertIn("grupo-resumo", caixa)
        self.assertIn("Fazenda sem cadastro", caixa)
        self.assertIn("grupo-box", master.get("/admin/solicitacoes").get_data(as_text=True))  # lembrado na sessao
        master.get("/admin/solicitacoes?exibir=produtor")
        avancado = self._plano("Avançado")
        r = master.post("/admin/clientes/novo", data={
            "nome_completo": "Zzz Grupo Modelo Dono", "username": "Zzz Modelo Login", "password": "segredo1",
            "codigo_pais": "55", "numero_telefone": "65999997101", "email": "", "grupo_economico": "Grupo Zzz Modelo",
            "plano_id": str(avancado["id"]), "cortesia_ate": "2099-01-31", "site": [e1, e2, self.real]})
        self.assertEqual(r.status_code, 302)
        uid = models.get_user_by_username("Zzz Modelo Login")["id"]
        pontos = {p["site_name"]: p for p in models.get_cliente_pontos(uid)}
        self.assertEqual(set(pontos), {e1, e2, self.real})
        self.assertTrue(all(p["vinculada"] for p in pontos.values()))
        self.assertEqual((pontos[e1]["cidade"], pontos[e1]["area_ha"]), ("Sorriso", 150))
        self.assertIn(e1, models.get_user_permitted_site_names(uid))
        self.assertEqual(self._selo(e1), "Avançado · até 31/01/2099")
        self.assertEqual(self._selo(self.real), "Inteligência · até 31/01/2099")  # BioScout = Inteligência
        with webapp.app.test_request_context():
            self.assertEqual(webapp._grupo_economico_da_fazenda(e2), "Grupo Zzz Modelo")
        # grupo do cadastro editavel (salva sozinho)
        master.post(f"/admin/clientes/{uid}/grupo", headers={"X-Autosave": "1"}, data={"grupo_economico": "Grupo Zzz Novo"})
        with webapp.app.test_request_context():
            self.assertEqual(webapp._grupo_economico_da_fazenda(e2), "Grupo Zzz Novo")
        master.post("/admin/fazendas/grupo/salvar", headers={"X-Autosave": "1"},
                    data={"site_name": e1, "grupo_economico": "Grupo Zzz Novo"})
        self.assertEqual(models.get_cadastro_do_usuario(uid)["grupo_economico"], "Grupo Zzz Novo")
        # editor de dados da fazenda
        editor = master.get("/admin/fazendas/dados", query_string={"site": e2}).get_data(as_text=True)
        for campo in ('name="cidade"', 'name="culturas"', 'name="grupo_cultura"', 'name="area_cultura__Soja"',
                      'name="cultura_outra"', 'name="grupo_economico"', 'name="nome_exibicao"', 'name="country_code"',
                      'name="estacao_codigo"'):
            self.assertIn(campo, editor)
        master.post("/admin/fazendas/dados/salvar", headers={"X-Autosave": "1"}, data={
            "site_name": e2, "cidade": "Sinop", "uf": "MT", "grupo_cultura": "cereais", "culturas": "Soja",
            "area_cultura__Soja": "40"})
        self.assertEqual(models.get_ponto_por_id(pontos[e2]["id"])["area_ha"], 40)
        self.assertIn("Soja (140 ha)", models.get_cadastro_do_usuario(uid)["culturas"])
        # Administrador nao mexe nos dados (so' Master)
        admin = self._logado("Zzz Admin Modelo")
        self.assertEqual(admin.get("/admin/fazendas/dados", query_string={"site": e2}).status_code, 403)
        self.assertEqual(admin.post("/admin/clientes/novo", data={}).status_code, 403)
        # desligar: a fazenda continua no site, sem a permissao do cliente
        master.post(f"/admin/clientes/{uid}/desvincular/{pontos[e2]['id']}")
        self.assertTrue(models.get_virtual_farm(e2))
        self.assertNotIn(e2, models.get_user_permitted_site_names(uid))
        self.assertEqual(models.get_fazendas_dados()[e2]["cidade"], "Sinop")
        # excluir o cliente nao apaga as fazendas ligadas
        models.delete_user(uid)
        self.assertTrue(models.get_virtual_farm(e1))

    def test_campos_duplicados_sairam_de_manejos_e_mapas_de_estudo(self):
        master = self._logado("Zzz Master Modelo")
        models.create_virtual_farm("Zzz Modelo Dup", -12.43, -55.7, 30)
        manejos = master.get("/fazendas").get_data(as_text=True)
        for campo in ('name="grupo_economico"', 'name="nome_exibicao"', 'name="estacao_codigo"', "Ocultar fazenda"):
            self.assertNotIn(campo, manejos)
        estudo = master.get("/mapa-interpolado").get_data(as_text=True)
        self.assertNotIn("Plano e grupo", estudo)
        self.assertNotIn("🙈 Ocultar", estudo)
        graficos = master.get("/graficos").get_data(as_text=True)
        self.assertIn('id="filtroPlano"', graficos)


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
        self.assertIn("Tabela de apoio: preço em <span class=\"moeda-simbolo\">U$</span> pela área monitorada", html)
        self.assertEqual(html.count(" data-campo=\"usd_ha\" value="), 9)  # 9 faixas fixas
        for i, ate in enumerate((100, 250, 500, 1000, 2000, 3000, 5000, 10000)):  # limites editaveis (13/11/2026)
            self.assertIn(f'name="ate_ha_{i}" data-col="{i}" data-campo="ate_ha" value="{ate}"', html)
        self.assertIn(">acima de 10.000 ha<", html)
        self.assertNotIn("Raio (km)", html)  # coluna Raio saiu da tabela de apoio (13/11/2026)
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
        self.assertIn("maiores que zero", ruim.get_data(as_text=True))
        self.assertEqual(self._logado("Zzz Teste Admin Tabela").post("/admin/planos/tabela", data=dados).status_code, 403)

    def test_area_das_faixas_editavel(self):
        master = self._logado("Zzz Teste Master Tabela")
        dados = {f"usd_ha_{i}": "" for i in range(9)}
        dados.update({f"ate_ha_{i}": str(a) for i, a in enumerate((100, 300, 500, 1000, 2000, 3000, 5000, 12000))})
        dados["usd_ha_1"] = "2"
        try:
            r = master.post("/admin/planos/tabela", data=dados, headers={"X-Autosave": "1"}).get_json()
            self.assertTrue(r["ok"])
            self.assertEqual(webapp._faixas_preco(), [100, 300, 500, 1000, 2000, 3000, 5000, 12000, None])
            self.assertEqual(webapp.preco_pela_area(280)["faixa"], "até 300 ha")
            self.assertEqual(webapp.preco_pela_area(280)["usd_ha"], 2)
            self.assertEqual(webapp.preco_pela_area(20000)["faixa"], "acima de 12.000 ha")
            # fora de ordem: grava ordenado por area (13/11/2026), o valor acompanha a area
            fora = master.post("/admin/planos/tabela", data={**dados, "ate_ha_7": "250", "usd_ha_7": "9"},
                               headers={"X-Autosave": "1"}).get_json()
            self.assertTrue(fora["ok"])
            self.assertEqual(webapp._faixas_preco(), [100, 250, 300, 500, 1000, 2000, 3000, 5000, None])
            self.assertEqual(webapp.preco_pela_area(250)["usd_ha"], 9)
            repetida = master.post("/admin/planos/tabela", data={**dados, "ate_ha_2": "300"}, headers={"X-Autosave": "1"}).get_json()
            self.assertFalse(repetida["ok"])
            self.assertIn("mesma área", repetida["message"])
        finally:
            models.set_setting("tabela_precificacao", "[]")

    def test_preco_fixo_por_faixa(self):
        """Preco fixo por faixa (coluna, 13/11/2026): 350 ha em "ate' 400" fixa paga 400 ha;
        faixa sem a marca segue pela area."""
        master = self._logado("Zzz Teste Master Tabela")
        h = {"X-Autosave": "1"}
        dados = {"ate_ha_0": "300", "ate_ha_1": "400", "usd_ha_0": "14", "usd_ha_1": "13", "usd_ha_2": "3",
                 "moeda": "BRL", "tem_preco_fixo": "1"}
        try:
            master.post("/admin/planos/tabela", data=dados, headers=h)
            p = webapp.preco_pela_area(350)
            self.assertEqual((p["total_usd"], p["preco_fixo"]), (350 * 13, False))
            master.post("/admin/planos/tabela", data={**dados, "fixo_1": "on"}, headers=h)
            p = webapp.preco_pela_area(350)
            self.assertEqual((p["faixa"], p["total_usd"], p["area_cobrada"], p["preco_fixo"]), ("até 400 ha", 400 * 13, 400, True))
            self.assertEqual(webapp.preco_pela_area(250)["total_usd"], 250 * 14)  # faixa 300 nao fixa
            self.assertEqual(webapp.preco_pela_area(1000)["total_usd"], 3000)  # "acima de": pela area
            html = master.get("/admin/planos").get_data(as_text=True)
            self.assertIn('name="fixo_1" data-col="1" data-campo="fixo" aria-label="Preço fixo até 400 ha" checked', html)
            self.assertNotIn('name="fixo_2"', html)
            plano = {"preco_por_area": True, "parcelas": 1, "valor": 0}
            self.assertEqual(webapp._plano_para_area(plano, 350)["valor"], 5200)
            self.assertIn('"fixo": true', webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True))
            # fora de ordem: a marca acompanha a faixa
            master.post("/admin/planos/tabela", data={**dados, "ate_ha_0": "400", "ate_ha_1": "300", "fixo_0": "on"}, headers=h)
            self.assertEqual(webapp._fixo_por_faixa(), {300: False, 400: True})
        finally:
            models.set_setting("tabela_precificacao", "[]")
            models.set_setting("tabela_preco_fixo", "0")
            models.set_setting("tabela_precificacao_moeda", "USD")

    def test_quantidade_de_faixas_editavel(self):
        master = self._logado("Zzz Teste Master Tabela")
        h = {"X-Autosave": "1"}
        try:
            r = master.post("/admin/planos/tabela", data={"ate_ha_0": "500", "ate_ha_1": "2000", "usd_ha_0": "3",
                                                          "usd_ha_1": "2", "usd_ha_2": "1"}, headers=h).get_json()
            self.assertTrue(r["ok"])
            self.assertEqual(webapp._faixas_preco(), [500, 2000, None])
            self.assertEqual(webapp.preco_pela_area(800)["usd_ha"], 2)
            self.assertEqual((webapp.preco_pela_area(9000)["faixa"], webapp.preco_pela_area(9000)["usd_ha"]), ("acima de 2.000 ha", 1))
            html = master.get("/admin/planos").get_data(as_text=True)
            self.assertEqual(html.count('data-campo="usd_ha" value='), 3)
            self.assertIn('id="adicionar-faixa"', html)
            dados = {f"ate_ha_{i}": str((i + 1) * 100) for i in range(12)}
            self.assertTrue(master.post("/admin/planos/tabela", data=dados, headers=h).get_json()["ok"])
            self.assertEqual(len(webapp._faixas_preco()), 13)
            muitas = {f"ate_ha_{i}": str((i + 1) * 100) for i in range(webapp.MAX_FAIXAS + 1)}
            self.assertFalse(master.post("/admin/planos/tabela", data=muitas, headers=h).get_json()["ok"])
        finally:
            models.set_setting("tabela_precificacao", "[]")

    def test_preco_pela_area_busca_a_faixa_de_cima(self):
        import json
        models.set_setting("tabela_precificacao", json.dumps(
            [{"ate_ha": a, "usd_ha": v} for a, v in ((100, 1), (250, 0.9), (500, 0.8), (1000, 0.7), (None, 0.1))]))
        p = webapp.preco_pela_area(300)
        self.assertEqual((p["faixa"], p["usd_ha"], p["total_usd"], p["total_brl"]), ("até 500 ha", 0.8, 240, 1200))
        self.assertAlmostEqual(p["raio_km"], webapp._raio_km(300))
        self.assertEqual(webapp.preco_pela_area(100)["faixa"], "até 100 ha")  # limite fica na propria faixa
        self.assertEqual(webapp.preco_pela_area("100.5")["faixa"], "até 250 ha")
        # 4 faixas gravadas = 4 faixas (quantidade editavel, 13/11/2026): 1.500 ha cai no "acima de 1.000"
        p = webapp.preco_pela_area(1500)
        self.assertEqual((p["faixa"], p["usd_ha"], p["total_usd"]), ("acima de 1.000 ha", 0.1, 150))
        self.assertEqual(webapp.preco_pela_area(25000)["total_usd"], 2500)
        models.set_setting("tabela_precificacao", "[]")

    def test_tabela_em_reais(self):
        """Moeda da tabela de apoio U$ ou R$ (pedido do Alan, 09/11/2026): em R$
        o valor por ha ja' e' em reais, sem conversao; plano pela area sai em R$."""
        master = self._logado("Zzz Teste Master Tabela")
        dados = {f"usd_ha_{i}": "" for i in range(9)}
        dados.update({"usd_ha_2": "10", "moeda": "BRL"})
        try:
            master.post("/admin/planos/tabela", data=dados)
            p = webapp.preco_pela_area(300)
            self.assertEqual((p["moeda"], p["simbolo"], p["total_usd"], p["total_brl"]), ("BRL", "R$", 3000, 3000))
            html = master.get("/admin/planos").get_data(as_text=True)
            self.assertIn('<option value="BRL" selected', html)
            self.assertIn("R$ 5.000,00", html)  # 500 ha x R$ 10
            plano = {"preco_por_area": 1, "parcelas": 12, "nome": "Zzz", "moeda": "USD", "simbolo": "U$"}
            pa = webapp._plano_para_area(plano, 300)
            self.assertEqual((pa["moeda"], pa["simbolo"], pa["valor"], pa["valor_parcela"]), ("BRL", "R$", 3000, 250))
        finally:
            models.set_setting("tabela_precificacao_moeda", "USD")
        self.assertEqual(webapp.preco_pela_area(300)["moeda"], "USD")
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
        for rotulo in ("<td>Lavouras</td>", "<td>Raio (km)</td>", "Raio de alcance das estações", "raio-mapa", "Esporos estimados na região", "Mapa da região com as estações"):
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
        self.assertIn(">Conta</h1>", home)
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


class DoencaNomeEditadoTest(unittest.TestCase):
    """Nome trocado a mao (mesmo de volta pro ingles) nunca e' traduzido sozinho (pedido do Alan, 13/11/2026)."""

    def test_nome_em_ingles_escolhido_a_mao_fica(self):
        models.init_db()
        en, en2 = "Zzz Grey Mould", "Zzz Grey mould"
        mapa = {en: "Zzz Mofo Cinzento", en2: "Zzz Mofo Cinzento"}
        models.ensure_disease_translations([en], mapa)
        self.assertEqual(models.get_all_disease_translations()[en]["nome_pt"], "Zzz Mofo Cinzento")
        models.save_disease_translation(en, en, "Botrytis spp.")  # Master troca de volta pro ingles
        models.ensure_disease_translations([en], mapa)
        self.assertEqual(models.get_all_disease_translations()[en]["nome_pt"], en)
        self.assertNotIn(en, webapp.doencas_sem_traducao())
        # grafia duplicada vinda de outro dispositivo: a juncao mantem o nome escolhido
        conn = models.get_db()
        conn.execute("INSERT INTO disease_translations (display_name_en, nome_pt) VALUES (?, ?)", (en2, "Zzz Mofo Cinzento"))
        conn.commit()
        conn.close()
        models.merge_duplicate_disease_translations({en2: en})
        models.ensure_disease_translations([en], mapa)
        self.assertEqual(models.get_all_disease_translations()[en]["nome_pt"], en)
        # salvar o formulario de novo com o mesmo nome nao desmarca
        models.save_disease_translation(en, en, "Botrytis spp.")
        models.ensure_disease_translations([en], mapa)
        self.assertEqual(models.get_all_disease_translations()[en]["nome_pt"], en)


class PermissoesNomeENdviNuvensTest(unittest.TestCase):
    """Permissoes com o nome de exibicao e limite de nuvens do NDVI (pedido do Alan, 13/11/2026)."""

    @classmethod
    def setUpClass(cls):
        models.init_db()
        webapp.app.config["TESTING"] = True
        models.sync_sites(["Zzz Fazenda Perm Real"])
        models.set_site_display_name("Zzz Fazenda Perm Real", "Zzz Nome Novo Perm")
        if not models.get_user_by_username("Zzz Master Perm"):
            models.create_user("Zzz Master Perm", "x", is_master=True)
        if not models.get_user_by_username("Zzz Usuario Perm"):
            models.create_user("Zzz Usuario Perm", "x")

    def _cliente(self):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Perm")["id"])
            s["_fresh"] = True
        return c

    def test_permissoes_mostram_nome_de_exibicao(self):
        uid = models.get_user_by_username("Zzz Usuario Perm")["id"]
        c = self._cliente()
        for url in (f"/admin/users/{uid}/permissions",):
            h = c.get(url).get_data(as_text=True)
            self.assertIn("Zzz Nome Novo Perm", h)
            self.assertNotIn(">Zzz Fazenda Perm Real<", h)

    def test_nuvens_ndvi(self):
        c = self._cliente()
        r = c.post("/ndvi/nuvens", data={"site_name": "Zzz Fazenda Perm Real", "nuvens": "20"}, headers={"X-Autosave": "1"})
        self.assertTrue(r.get_json()["ok"])
        self.assertEqual(models.get_ndvi_nuvens()["Zzz Fazenda Perm Real"], 20)
        r = c.post("/ndvi/nuvens", data={"site_name": "Zzz Fazenda Perm Real", "nuvens": "17"}, headers={"X-Autosave": "1"})
        self.assertFalse(r.get_json()["ok"])
        c.post("/ndvi/nuvens", data={"site_name": "Zzz Fazenda Perm Real", "nuvens": ""}, headers={"X-Autosave": "1"})
        self.assertNotIn("Zzz Fazenda Perm Real", models.get_ndvi_nuvens())


class NdviNuvensNaFazendaTest(unittest.TestCase):
    """O limite de nuvens vale SOBRE A FAZENDA, nao no quadro inteiro do satelite (pedido do Alan, 13/11/2026)."""

    def _png(self, nuvem, boa):
        import io
        from PIL import Image
        img = Image.new("RGBA", (10, 10), (0, 0, 0, 0))
        px = [(0, 0, 0, 26)] * nuvem + [(0, 150, 0, 255)] * boa
        for i, cor in enumerate(px):
            img.putpixel((i % 10, i // 10), cor)
        b = io.BytesIO()
        img.save(b, "PNG")
        return b.getvalue()

    def test_mede_e_escolhe_dia_com_fazenda_limpa(self):
        import ndvi_service as n
        self.assertAlmostEqual(n.nuvens_na_fazenda(self._png(30, 70)), 30.0)
        self.assertIsNone(n.nuvens_na_fazenda(self._png(0, 0)))
        cenas = [{"properties": {"datetime": f"2026-11-{d:02d}T13:00:00Z", "eo:cloud_cover": c}}
                 for d, c in ((11, 2), (6, 40), (1, 95))]
        imagens = {"2026-11-11": self._png(90, 10), "2026-11-06": self._png(3, 97)}

        class Resp:
            def __init__(self, b):
                self.b = b
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def read(self):
                return self.b

        def falso(req, timeout=None):
            import json
            dia = json.loads(req.data)["input"]["data"][0]["dataFilter"]["timeRange"]["from"][:10]
            return Resp(imagens[dia])

        aneis = [[[[-56.9, -14.5], [-56.8, -14.5], [-56.8, -14.4], [-56.9, -14.5]]]]
        from datetime import date
        with mock.patch.object(n, "_obter_token", return_value=("t", None)), \
                mock.patch.object(n, "_buscar_cenas", return_value=cenas), \
                mock.patch.object(n.urllib.request, "urlopen", falso):
            res, erro = n.buscar_ndvi(aneis, data_alvo=date(2026, 11, 12), cobertura_maxima=5)
            self.assertIsNone(erro)
            self.assertEqual(res["data"], date(2026, 11, 6))
            self.assertEqual(res["cobertura_nuvens"], 3.0)
            res, erro = n.buscar_ndvi(aneis, data_alvo=date(2026, 11, 12), cobertura_maxima=0)
            self.assertIsNone(res)
            self.assertIn("11/11: 90%", erro)


class AtendimentoPaisesEstadosTest(unittest.TestCase):
    """Paises atendidos e raio por estado no mapa de atendimento (pedido do Alan, 13/11/2026)."""

    def test_salvar(self):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Atend"):
            models.create_user("Zzz Master Atend", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Atend")["id"])
            s["_fresh"] = True
        html = c.get("/admin/planos").get_data(as_text=True)
        self.assertIn('name="pais_atendido" value="BR"', html)
        self.assertIn('name="uf_raio__MT"', html)
        r = c.post("/admin/planos/atendimento", headers={"X-Autosave": "1"}, data={
            "pais_atendido": ["BR", "XX"], "uf_on__MT": "on", "uf_raio__MT": "80", "uf_raio__PR": "30", "uf_on__GO": "on"})
        self.assertTrue(r.get_json()["ok"])
        cfg = webapp._atendimento_config()
        self.assertEqual(cfg["paises"], ["BR"])
        self.assertEqual(cfg["raio_uf"], {"MT": 80.0})
        models.set_setting("atendimento_paises", "null")
        models.set_setting("atendimento_raio_uf", "{}")


class RaioPorEstadoEstimadasTest(unittest.TestCase):
    """Raio por estado vale tambem nas fazendas estimadas do app (pedido do Alan, 13/11/2026)."""

    def test_criacao_e_atualizacao(self):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master RaioUF"):
            models.create_user("Zzz Master RaioUF", "x", is_master=True)
        if not models.get_user_by_username("Zzz Cliente RaioUF"):
            models.criar_cliente("Zzz Cliente RaioUF", "x", "", "5543999990555", "", None)
        uid = models.get_user_by_username("Zzz Cliente RaioUF")["id"]
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master RaioUF")["id"])
            s["_fresh"] = True
        c.post("/admin/planos/atendimento", headers={"X-Autosave": "1"},
               data={"pais_atendido": ["BR"], "uf_on__MT": "on", "uf_raio__MT": "120"})
        plano = {"raio_km": 50}
        with webapp.app.test_request_context():
            pmt = models.criar_cliente_ponto(uid, "Zzz Faz MT", -12.5, -55.7)
            models.set_culturas_do_ponto(pmt, "Soja (100 ha)", 100, "Sorriso", "MT")
            site_mt = webapp._criar_fazenda_estimada_da_lavoura(uid, pmt, "Zzz Faz MT", -12.5, -55.7, plano, username="Zzz Cliente RaioUF")
            ppr = models.criar_cliente_ponto(uid, "Zzz Faz PR", -24.0, -52.4)
            models.set_culturas_do_ponto(ppr, "Soja (100 ha)", 100, "Campo Mourão", "PR")
            site_pr = webapp._criar_fazenda_estimada_da_lavoura(uid, ppr, "Zzz Faz PR", -24.0, -52.4, plano, username="Zzz Cliente RaioUF")
        self.assertEqual(models.get_virtual_farm(site_mt)["raio_km"], 120)
        self.assertEqual(models.get_virtual_farm(site_pr)["raio_km"], 50)
        # PR ganha raio proprio, MT perde (volta pro plano do cliente; sem plano = 50)
        r = c.post("/admin/planos/atendimento", headers={"X-Autosave": "1"},
                   data={"pais_atendido": ["BR"], "uf_on__PR": "on", "uf_raio__PR": "30"})
        self.assertIn("fazenda(s) estimada(s)", r.get_json()["message"])
        self.assertEqual(models.get_virtual_farm(site_pr)["raio_km"], 30)
        self.assertEqual(models.get_virtual_farm(site_mt)["raio_km"], 50)
        models.set_setting("atendimento_raio_uf", "{}")
        models.delete_user(uid)


class DoencaPorEstadoTest(unittest.TestCase):
    """Oidio e Alternaria so' em SP/MS/PR/SC/RS nas Recomendações/Alertas/relatorios; o quadro de
    favorabilidade nao muda (pedido do Alan, 14/11/2026)."""

    def test_oidio_so_nos_estados_do_sul(self):
        models.salvar_fazenda_dados("Zzz Estado PR", "Londrina", "PR", "", None)
        models.salvar_fazenda_dados("Zzz Estado MT", "Sinop", "MT", "", None)
        info = {"Zzz Powdery": {"nome_pt": "Oídio Zzz", "nome_cientifico": "Erysiphe diffusa"},
                "Zzz Rust": {"nome_pt": "Ferrugem Zzz", "nome_cientifico": "Phakopsora"}}
        cards = [{"doenca_en": "Zzz Powdery", "doenca": "Oídio Zzz"}, {"doenca_en": "Zzz Rust", "doenca": "Ferrugem Zzz"}]
        with webapp.app.test_request_context(), mock.patch.object(webapp, "_disease_info_cached", return_value=info):
            self.assertEqual(webapp._estados_por_doenca(), {"Zzz Powdery": set(webapp.ESTADOS_DOENCA_SECUNDARIA)})
            r = webapp._filtrar_por_estado({"Zzz Estado PR": cards, "Zzz Estado MT": cards})
            self.assertEqual([c["doenca_en"] for c in r["Zzz Estado PR"]], ["Zzz Powdery", "Zzz Rust"])
            self.assertEqual([c["doenca_en"] for c in r["Zzz Estado MT"]], ["Zzz Rust"])
            # quadro de favorabilidade do app (cliente-ponto) fica com tudo
            self.assertEqual(len(webapp._filtrar_por_estado({"cliente-ponto:1": cards})["cliente-ponto:1"]), 2)
        # escolha gravada em Doenças/Culturas vale no lugar do padrao ([] = todos)
        models.set_doenca_estados("Zzz Powdery", [])
        models.set_doenca_estados("Zzz Rust", ["MT"])
        with webapp.app.test_request_context(), mock.patch.object(webapp, "_disease_info_cached", return_value=info):
            r = webapp._filtrar_por_estado({"Zzz Estado PR": cards})
            self.assertEqual([c["doenca_en"] for c in r["Zzz Estado PR"]], ["Zzz Powdery"])
        models.set_setting("doenca_estados", "{}")

    def test_tela_doencas_tem_estados(self):
        models.add_manual_disease("Oídio Zzz Tela")
        master = models.get_user_by_username("Zzz Master Estados")
        if not master:
            models.create_user("Zzz Master Estados", "x", is_master=True)
            master = models.get_user_by_username("Zzz Master Estados")
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(master["id"])
            s["_fresh"] = True
        html = c.get("/admin/doencas").get_data(as_text=True)
        self.assertIn("Estados onde aparece", html)
        self.assertIn("MS, PR, RS, SC, SP", html)
