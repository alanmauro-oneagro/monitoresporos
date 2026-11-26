"""Planos padrao (Free, Basico, Premium, Premium + Estacao), plano gratis
liberado na hora e preco/abas editaveis pelo Master.

Rodar a partir de webapp/:  python -m unittest tests.test_planos
"""
import json
import os
import sys
import tempfile
import unittest
import urllib.parse
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
        self.assertEqual((free["valor"], free["ativo"], free["recursos"]), (0, 1, ["clima", "whatsapp_texto", "drive_pdf", "drive_txt", "drive_ndvi"]))
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
            # culturas vindas de Manejos nao tem area: falta a area plantada (23/11/2026)
            self.assertEqual(webapp._faltando_na_fazenda(webapp._dados_da_fazenda(site)),
                             ["área plantada de pelo menos uma cultura de Zzz Auto Dados - MT"])
        # fora do Brasil basta o municipio
        self.assertEqual(webapp._faltando_na_fazenda({"cidade": "Pencahue", "uf": "", "culturas": "Uva (10 ha)",
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
        self.assertIn("<h4 class=\"mb-2\">Gerenciar Fazendas</h4>", tela)  # ex-Gerenciar Clientes (23/11/2026)
        self.assertIn("Cadastro incompleto", tela)
        self.assertNotIn("+ Novo cliente", master.get("/admin/users").get_data(as_text=True))  # saiu (23/11/2026)
        # fazenda sem cadastro = cartao igual ao dos produtores, com o selo e o filtro de incompleto (09/11/2026)
        self.assertIn('data-tipo="fazenda"', tela)
        # cartao da fazenda sem cadastro aberto nao repete nome/plano/origem (10/11/2026)
        cartao = tela.split('data-tipo="fazenda"', 1)[1].split('data-tipo=', 1)[0]
        self.assertNotIn("Fazenda sem cadastro", tela)  # virou Cadastro completo/incompleto (23/11/2026)
        self.assertEqual(cartao.count("selo-plano"), 1)
        clima = models.create_virtual_farm("Zzz Modelo So Clima", -12.44, -55.7, 30, tipo="clima")
        self.assertNotIn(clima, webapp_sem_cadastro())  # ponto so' clima: so' no Mapas de Estudo (09/11/2026)
        oculta = models.create_virtual_farm("Zzz Modelo Oculta", -12.45, -55.7, 30)
        models.set_site_ativo(oculta, False)
        self.assertNotIn(oculta, webapp_sem_cadastro())  # oculta vai pra Gerenciar Fazendas Ocultas
        self.assertIn("🙈 Ocultar", tela)
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
        self.assertIn("Cadastro", caixa)
        self.assertIn("grupo-box", master.get("/admin/solicitacoes").get_data(as_text=True))  # lembrado na sessao
        master.get("/admin/solicitacoes?exibir=fazenda")
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
        from unittest import mock
        import data_reader
        uid = models.get_user_by_username("Zzz Usuario Perm")["id"]
        c = self._cliente()
        for url in (f"/admin/users/{uid}/permissions",):
            with mock.patch.object(data_reader, "read_sites", return_value=["Zzz Fazenda Perm Real"]):
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


class BioscoutSemPermissaoTest(unittest.TestCase):
    """403 na contagem de esporos: busca fazenda por fazenda e so' deixa de fora a recusada (pedido do Alan, 14/11/2026)."""

    def test_403_busca_fazenda_por_fazenda(self):
        import urllib.error
        import bioscout_fetch
        pasta = tempfile.mkdtemp()

        def falso(url, headers=None, method="GET", body=None):
            if "Auth/login" in url:
                return {"token": "a.b.c"}
            if "Site/get" in url:
                return [{"siteId": 1, "siteName": "Zzz Boa"}, {"siteId": 2, "siteName": "Zzz Bloqueada"}]
            if "SiteIds=1&SiteIds=2" in url or url.endswith("SiteIds=2"):
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            return [{"tapeScanId": "t1", "particulateId": "p1", "siteId": 1, "count": 3}]

        with mock.patch.object(bioscout_fetch, "_http_json", side_effect=falso):
            erros = bioscout_fetch.fetch_recent(pasta, "u", "s", log=lambda *_: None)
        self.assertEqual(len(erros), 1)
        self.assertIn("Zzz Bloqueada", erros[0])
        self.assertNotIn("Zzz Boa", erros[0])
        with open(os.path.join(pasta, "spore_counts.csv"), encoding="utf-8") as f:
            self.assertIn("t1", f.read())

    def test_recusada_vira_aviso_e_some_se_oculta(self):
        import urllib.error
        import bioscout_fetch
        pasta = tempfile.mkdtemp()

        def falso(url, headers=None, method="GET", body=None):
            if "Auth/login" in url:
                return {"token": "a.b.c"}
            if "Site/get" in url:
                return [{"siteId": 1, "siteName": "Zzz Boa"}, {"siteId": 2, "siteName": "Zzz Bloqueada"}]
            if "SiteIds=1&SiteIds=2" in url or url.endswith("SiteIds=2"):
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            return []

        lista = []
        with mock.patch.object(bioscout_fetch, "_http_json", side_effect=falso):
            erros = bioscout_fetch.fetch_recent(pasta, "u", "s", log=lambda *_: None, sem_permissao=lista)
        self.assertEqual((erros, lista), ([], ["Zzz Bloqueada"]))
        if not models.get_user_by_username("Zzz Master Aviso"):
            models.create_user("Zzz Master Aviso", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Aviso")["id"])
            s["_fresh"] = True
        with mock.patch.dict(webapp._fetch_state, {"sem_permissao": ["Zzz Bloqueada"], "last_error": None}), \
                mock.patch.object(webapp, "_maybe_auto_refresh"):
            html = c.get("/dashboard").get_data(as_text=True)
            self.assertIn("aviso-sem-permissao", html)
            self.assertNotIn("falhou", html)
            with mock.patch.object(models, "get_deactivated_site_names", return_value={"Zzz Bloqueada"}):
                self.assertNotIn("aviso-sem-permissao", c.get("/dashboard").get_data(as_text=True))

    def test_uf_pelo_fim_do_nome(self):
        with webapp.app.test_request_context(), \
                mock.patch.object(webapp, "_nome_exibicao", return_value="Zzz 3 Irmaos - MT"), \
                mock.patch.object(webapp, "_uf_por_site", return_value={}):
            self.assertEqual(webapp._uf_da_fazenda("Zzz Sem Dados Nome"), "MT")


class ManifestRetomaJanelaTest(unittest.TestCase):
    """Abrir pelo icone retoma a janela ja' aberta (pedido do Alan, 14/11/2026)."""

    def test_launch_handler(self):
        import json
        r = webapp.app.test_client().get("/static/site.webmanifest")
        d = json.loads(r.get_data(as_text=True))
        self.assertEqual(d["launch_handler"]["client_mode"][0], "focus-existing")
        self.assertEqual((d["id"], d["scope"], d["display"]), ("/", "/", "standalone"))
        r.close()


class TresEstacoesProximasTest(unittest.TestCase):
    """Dados da fazenda / pontos so' clima listam as 3 estacoes oficiais mais proximas (pedido do Alan, 14/11/2026)."""

    def test_tres_estacoes(self):
        with webapp.app.test_request_context():
            site = models.create_virtual_farm("Zzz Tres Estacoes", -11.0, -57.4, 20)
            fz = webapp._dados_da_fazenda(site, completo=True)
            models.delete_virtual_farm(site)
        self.assertEqual(len(fz["estacoes_proximas"]), 3)
        distancias = [e["distancia_km"] for e in fz["estacoes_proximas"]]
        self.assertEqual(distancias, sorted(distancias))


class LoginInstalarAppTest(unittest.TestCase):
    """Convite pra instalar o OneAgro na tela inicial do celular, no login (pedido do Alan, 15/11/2026)."""

    def test_caixa_instalar(self):
        html = webapp.app.test_client().get("/login").get_data(as_text=True)
        for trecho in ('id="instalar-app"', "beforeinstallprompt", "Adicionar à Tela de Início"):
            self.assertIn(trecho, html)


class NivelAcaoTest(unittest.TestCase):
    """Urgencia de aplicacao por concentracao de esporos x dias de risco alto (regra do Alan, 15/11/2026)."""

    def _prev(self, altos):
        return [{"data_fmt": f"{16 + i}/11", "risco": "vermelho" if i < altos else "amarelo"} for i in range(5)]

    def test_regra(self):
        casos = [("Perigo", 3, "preparar"), ("Perigo", 5, "preparar"), ("Perigo", 2, "acompanhar"),
                 ("Atencao", 3, "acompanhar"), ("Atencao", 2, "monitorar"), ("Normal", 5, "monitorar")]
        for status, altos, esperado in casos:
            self.assertEqual(webapp._nivel_acao(status, self._prev(altos))["chave"], esperado, (status, altos))
        self.assertEqual(webapp._nivel_acao("Perigo", [])["chave"], "acompanhar")  # sem previsao

    def test_linhas_sem_leitura(self):
        """Fungo sem leitura = linha da Media; nao monitorado (manual) = linha da Baixa (pedido do Alan, 15/11/2026)."""
        self.assertEqual(webapp._nivel_acao("SemLeitura", self._prev(3))["chave"], "acompanhar")
        self.assertEqual(webapp._nivel_acao("SemLeitura", self._prev(2))["chave"], "monitorar")
        self.assertEqual(webapp._nivel_acao("NaoMonitorada", self._prev(5))["chave"], "monitorar")
        riscos = [{"doenca_en": "Rust", "rotulo": "Ferrugem", "previsao_risco": self._prev(4)},
                  {"doenca_en": "manual:Zzz", "rotulo": "Zzz", "previsao_risco": self._prev(4)}]
        webapp._acao_favorabilidade(riscos, sem_leitura=True)
        self.assertEqual([r["acao"]["chave"] for r in riscos], ["acompanhar", "monitorar"])
        webapp._acao_favorabilidade(riscos)
        self.assertEqual(riscos[0]["acao"]["chave"], "monitorar")
        import export_pdf
        tabela = export_pdf._tabela_riscos_climaticos(riscos)[0]
        self.assertIn("Ação", tabela._cellvalues[0])

    def test_ordem_e_texto(self):
        doencas = [{"doenca": "B", "rotulo": "B", "status": "Atencao", "previsao_risco": self._prev(1),
                    "biologicos": None, "quimicos": None, "classe_label": {}},
                   {"doenca": "A", "rotulo": "A", "status": "Perigo", "previsao_risco": self._prev(4),
                    "biologicos": None, "quimicos": None, "classe_label": {}}]
        webapp._com_nivel_acao(doencas)
        self.assertEqual([d["rotulo"] for d in doencas], ["A", "B"])
        with webapp.app.test_request_context():
            texto = webapp._format_whatsapp_message("Zzz Acao", doencas, weather={"previsao_5_dias": []}, produtos={})
        self.assertIn("🔴 *ENTRAR COM APLICAÇÃO*:", texto)
        self.assertIn("⚠️ *DOENÇAS EM ATENÇÃO / PERIGO*", texto)
        self.assertIn("🍄 *B*", texto)  # na lista de doencas em atencao
        self.assertNotIn(" B", texto.split("*RECOMENDAÇÕES*")[1])  # Monitorar fica fora das recomendacoes


class RegrasNivelAcaoTelaTest(unittest.TestCase):
    """Regras do nivel de acao editaveis em Doenças/Culturas (pedido do Alan, 15/11/2026)."""

    def test_salvar_regras(self):
        if not models.get_user_by_username("Zzz Master Regras"):
            models.create_user("Zzz Master Regras", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Regras")["id"])
            s["_fresh"] = True
        self.assertIn("form-nivel-acao", c.get("/admin/doencas").get_data(as_text=True))
        dados = {"dias": "2", "acao_Perigo_0": "preparar", "acao_Perigo_1": "preparar", "acao_Atencao_0": "acompanhar",
                 "acao_Atencao_1": "monitorar", "acao_Normal_0": "monitorar", "acao_Normal_1": "monitorar",
                 "rotulo_preparar": "Aplicar já", "texto_preparar": "Zzz texto novo"}
        html = c.get("/admin/doencas").get_data(as_text=True)
        self.assertIn("(-) Sem leitura", html)
        self.assertIn("acao_NaoMonitorada_0", html)
        dados["acao_SemLeitura_0"] = "preparar"
        r = c.post("/admin/doencas/nivel-acao", data=dados, headers={"X-Autosave": "1"})
        self.assertTrue(r.get_json()["ok"])
        with webapp.app.test_request_context():
            self.assertEqual(webapp._nivel_acao("SemLeitura", [{"risco": "vermelho"}] * 2)["chave"], "preparar")
        prev = [{"risco": "vermelho"}, {"risco": "vermelho"}, {"risco": "verde"}, {"risco": "verde"}, {"risco": "verde"}]
        with webapp.app.test_request_context():
            acao = webapp._nivel_acao("Perigo", prev)
        self.assertEqual((acao["chave"], acao["rotulo"], acao["texto"]), ("preparar", "Aplicar já", "Zzz texto novo"))
        with webapp.app.test_request_context():
            self.assertEqual(webapp._nivel_acao("Perigo", prev[2:])["chave"], "preparar")  # sem os dias tambem preparar
        models.set_setting("regras_nivel_acao", "{}")
        with webapp.app.test_request_context():
            self.assertEqual(webapp._nivel_acao("Perigo", prev)["chave"], "acompanhar")  # volta ao padrao (3 dias)


class ManejoFungicidasTest(unittest.TestCase):
    """Aba Manejo e Fungicidas: itens 1, 2, 3 do guia de manejo em blocos editaveis + PDF (pedido do Alan, 15/11/2026)."""

    def _cliente(self):
        if not models.get_user_by_username("Zzz Master Manejo"):
            models.create_user("Zzz Master Manejo", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Manejo")["id"])
            s["_fresh"] = True
        return c

    def test_blocos_editaveis_e_pdf(self):
        c = self._cliente()
        models.save_disease_translation("Zzz Ramulose En", "Ramulose", "Colletotrichum gossypii")
        models.save_disease_translation("Zzz Rhizo En", "Rhizoctonia", "Rhizoctonia solani")
        conn = models.get_db()
        conn.execute("DELETE FROM fungicida_monitoramento WHERE doenca IN ('Zzz Ramulose En', 'Zzz Rhizo En') "
                     "OR doenca LIKE 'protocolo:%'")
        conn.commit()
        conn.close()
        models.set_fungicida_monitoramento("Zzz Ramulose En", "Zzz texto antigo do 1o documento.", "Zzz obj antigo")
        models.set_setting("protocolo_manejo_semeado_v3", "[]")
        for s_ in ("guia_objetivo_geral", "guia_fontes"):
            conn = models.get_db(); conn.execute("DELETE FROM app_settings WHERE key = ?", (s_,)); conn.commit(); conn.close()
        try:
            html = c.get("/admin/fungicidas").get_data(as_text=True)
            self.assertIn("Sintomas iniciais", html)
            self.assertIn("Doenças da soja", html)
            self.assertIn('value="protocolo:Mancha de Diplodia"', html)  # sem doenca no site: editavel
            self.assertIn("estrangulamento da haste", html)  # pesquisa gravada na doenca do site
            self.assertIn("Embrapa Soja", html)  # fonte
            dados = models.get_all_fungicida_monitoramento()
            self.assertTrue(dados["Zzz Ramulose En"]["texto"].startswith("Manchas necróticas circulares"))  # v3 trocou o antigo
            self.assertEqual(dados["Zzz Ramulose En"]["fonte"], "Embrapa Algodão")
            r = c.post("/admin/fungicidas", headers={"X-Autosave": "1"}, data={
                "monitoramento_key": ["Zzz Ramulose En", "protocolo:Mancha de Diplodia", "Zzz Rhizo En"],
                "monitoramento": ["Zzz linha 1\nZzz linha 2", "Zzz olhar colmo.", ""],
                "monitoramento_objetivo": ["Zzz objetivo.", "", ""],
                "monitoramento_fonte": ["Zzz fonte", "", ""],
                "guia_objetivo_geral": "Zzz objetivo geral 1\nZzz objetivo geral 2",
                "guia_fontes": "Zzz Fonte A"})
            self.assertTrue(r.get_json()["ok"])
            self.assertEqual(models.get_all_fungicida_monitoramento()["Zzz Ramulose En"],
                             {"texto": "Zzz linha 1\nZzz linha 2", "objetivo": "Zzz objetivo.", "fonte": "Zzz fonte"})
            chamada = {}
            original = webapp.export_pdf.build_guia_manejo_pdf
            webapp.export_pdf.build_guia_manejo_pdf = lambda sec, **k: chamada.update(k, secoes=sec) or b"%PDF"
            try:
                c.get("/admin/fungicidas/guia-manejo.pdf")
            finally:
                webapp.export_pdf.build_guia_manejo_pdf = original
            itens = {i["nome"]: i for _, lista in chamada["secoes"] for i in lista}
            self.assertEqual(itens["Mancha de Diplodia"]["texto"], "Zzz olhar colmo.")
            self.assertNotIn("Rhizoctonia", itens)  # apagada no bloco: sai do PDF e nao volta
            self.assertEqual(chamada["objetivo_geral"], ["Zzz objetivo geral 1", "Zzz objetivo geral 2"])
            self.assertEqual(chamada["fontes"], ["Zzz Fonte A"])
            self.assertEqual(webapp.export_pdf._frases("Zzz linha 1\nZzz linha 2"), ["Zzz linha 1", "Zzz linha 2"])
            pdf = c.get("/admin/fungicidas/guia-manejo.pdf")
            self.assertEqual(pdf.mimetype, "application/pdf")
            self.assertIn("no-store", pdf.headers["Cache-Control"])
        finally:
            models.delete_disease_translation("Zzz Ramulose En")
            models.delete_disease_translation("Zzz Rhizo En")
            conn = models.get_db()
            conn.execute("DELETE FROM fungicida_monitoramento WHERE doenca LIKE 'protocolo:%'")
            conn.execute("DELETE FROM app_settings WHERE key IN ('guia_objetivo_geral', 'guia_fontes')")
            conn.commit()
            conn.close()
        self.assertNotIn("Zzz Ramulose En", models.get_all_fungicida_monitoramento())

    def test_incluir_doenca_no_guia(self):
        c = self._cliente()
        models.save_disease_translation("Zzz Fora En", "Zzz Doenca Fora", "")
        try:
            self.assertIn('value="Zzz Fora En"', c.get("/admin/fungicidas").get_data(as_text=True))
            c.post("/admin/fungicidas", data={"monitoramento_key": "Zzz Fora En", "monitoramento": "",
                                              "monitoramento_objetivo": ""})
            html = c.get("/admin/fungicidas").get_data(as_text=True)
            self.assertIn('data-chave="Zzz Fora En"', html)
        finally:
            models.delete_disease_translation("Zzz Fora En")


class DoencasEManejoVinculosTest(unittest.TestCase):
    """Doenças e Culturas cadastra; Manejo e Fungicidas so' usa (pedido do Alan, 15/11/2026)."""

    def test_vinculos(self):
        if not models.get_user_by_username("Zzz Master Vinculo"):
            models.create_user("Zzz Master Vinculo", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Vinculo")["id"])
            s["_fresh"] = True
        conn = models.get_db()
        conn.execute("DELETE FROM fungicida_monitoramento WHERE doenca LIKE 'protocolo:%'")
        conn.commit()
        conn.close()
        models.set_setting("protocolo_manejo_semeado_v3", "[]")
        chave = None
        try:
            html = c.get("/admin/fungicidas").get_data(as_text=True)
            self.assertIn("Cadastrar em Doenças e Culturas", html)
            self.assertIn("/admin/doencas?nova=Mancha+de+Diplodia", html)
            c.post("/admin/fungicidas", headers={"X-Autosave": "1"}, data={
                "monitoramento_key": "protocolo:Mancha de Diplodia", "monitoramento": "Zzz editado antes do cadastro",
                "monitoramento_objetivo": "Zzz obj", "monitoramento_fonte": "Zzz fonte"})
            d = c.get("/admin/doencas?nova=Mancha+de+Diplodia").get_data(as_text=True)
            self.assertIn("Doenças e Culturas", d)
            self.assertIn('value="Mancha de Diplodia"', d)
            c.post("/admin/doencas/manual", data={"nome_pt": "Mancha de Diplodia"})
            chave = next(k for k, v in models.get_all_disease_translations().items() if v["nome_pt"] == "Mancha de Diplodia")
            with webapp.app.test_request_context():
                link = webapp.url_for("admin_fungicidas", abrir=chave)
            self.assertIn(f'href="{link}"', c.get("/admin/doencas").get_data(as_text=True))
            c.get("/admin/fungicidas")
            dados = models.get_all_fungicida_monitoramento()
            self.assertEqual(dados[chave]["texto"], "Zzz editado antes do cadastro")  # o editado passou pra doenca
            self.assertNotIn("protocolo:Mancha de Diplodia", dados)
        finally:
            if chave:
                models.delete_disease_translation(chave)
            conn = models.get_db()
            conn.execute("DELETE FROM fungicida_monitoramento WHERE doenca LIKE 'protocolo:%'")
            conn.commit()
            conn.close()


class ProdutosFolhaPorDataTest(unittest.TestCase):
    """Produtos de folha agrupados pela data de aplicacao (pedido do Alan, 16/11/2026)."""

    def test_agrupa_e_ordena(self):
        import models
        produtos = {
            "quimico": [{"nome": "Cypress", "data_anotacao": "85DAP"}, {"nome": "Alade", "data_anotacao": "25DAP"},
                        {"nome": "Mancozebe", "data_anotacao": "55DAP"}, {"nome": "Fox Supra", "data_anotacao": "55 DAP"},
                        {"nome": "", "data_anotacao": "10DAP"}, {"nome": "Bravonil", "data_anotacao": ""}],
            "biologico": [{"nome": "Vult", "data_anotacao": "25DAP", "ingrediente_ativo": "Bacillus subtilis"}],
        }
        grupos = models.produtos_por_data(produtos)
        self.assertEqual([g["data"] for g in grupos], ["25DAP", "55DAP", "85DAP", ""])
        self.assertEqual([p["nome"] for p in grupos[0]["quimico"]], ["Alade"])
        self.assertEqual([p["nome"] for p in grupos[1]["quimico"]], ["Mancozebe", "Fox Supra"])
        self.assertEqual(grupos[0]["biologico"][0]["ingrediente_ativo"], "Bacillus subtilis")
        datas = models.produtos_por_data({"quimico": [{"nome": "B", "data_anotacao": "20/12/2026"},
                                                       {"nome": "A", "data_anotacao": "05/11/2026"}]})
        self.assertEqual([g["data"] for g in datas], ["05/11/2026", "20/12/2026"])

    def test_data_digitavel_nas_boxes(self):
        """Data aplicacao das boxes TS/Sulco/Folha e' campo de texto (pedido do Alan, 17/11/2026)."""
        import app as app_mod
        def linha(i, data):
            return {"id": i, "caixa": i, "data_anotacao": data, "talhao": "", "nome": "X", "ingrediente_ativo": ""}
        existentes = {("1", "folha", "quimico"): [linha(1, "25DAP"), linha(2, "2026-11-17"), linha(3, "17/11/2026")]}
        caixas = app_mod._caixas_de_produtos(existentes, "1", "folha")
        self.assertEqual([c["data"] for c in caixas], ["25DAP", "17/11/2026", "17/11/2026"])
        with open(os.path.join(os.path.dirname(app_mod.__file__), "templates", "fazendas.html"), encoding="utf-8") as f:
            html = f.read()
        self.assertIn('<input type="text" name="cx_data_', html)
        self.assertNotIn("inp.type = 'date'", html)

    def test_texto_e_pdf(self):
        import app as app_mod
        import export_pdf
        produtos = {"quimico": [{"nome": "Alade", "data_anotacao": "25DAP"}, {"nome": "Cypress", "data_anotacao": "85DAP"}],
                    "biologico": [{"nome": "Vult", "data_anotacao": "25DAP"}]}
        with app_mod.app.test_request_context():
            doencas = [{"doenca": "A", "rotulo": "A", "status": "Perigo", "previsao_risco": [],
                        "biologicos": None, "quimicos": None, "classe_label": {}}]
            app_mod._com_nivel_acao(doencas)
            texto = app_mod._format_whatsapp_message("Zzz Teste Folha", doencas, weather={"previsao_5_dias": []},
                                                      produtos=produtos)
        self.assertIn("*RECOMENDAÇÕES*:", texto)
        pdf = export_pdf.build_recommendation_pdf("Zzz Teste Folha", "Safra", [], produtos=produtos)
        self.assertTrue(pdf.getvalue()[:4] == b"%PDF")


class PdfMonitoramentoManejoTest(unittest.TestCase):
    """PDF da fazenda no modelo "Monitoramento e Manejo" (pedido do Alan, 16/11/2026)."""

    def test_secoes(self):
        import shutil
        import subprocess
        import export_pdf
        prev = [{"data_fmt": f"{16 + i}/11", "risco": "vermelho"} for i in range(5)]
        doencas = [
            {"doenca": "Antracnose", "doenca_en": "Anthracnose", "rotulo": "Antracnose", "status": "Perigo",
             "previsao_risco": prev, "acao": {"chave": "preparar", "rotulo": "Preparar aplicação", "texto": "Prepare."},
             "biologicos": {"itens": [{"ingrediente": "Trichoderma spp."}], "fonte": "Embrapa -- x"}, "quimicos": None,
             "classe_label": {}, "historico": [], "nota": "Nota da fazenda"},
            {"doenca": "Ferrugem", "doenca_en": "Soybean Rust", "rotulo": "Ferrugem", "status": "Atencao",
             "previsao_risco": prev, "acao": {"chave": "acompanhar", "rotulo": "Acompanhar", "texto": "Acompanhe."},
             "biologicos": None, "quimicos": None, "classe_label": {}, "historico": []},
        ]
        riscos = [{"doenca_en": "Target Spot", "rotulo": "Mancha-alvo", "previsao_risco": prev,
                   "acao": {"chave": "monitorar", "rotulo": "Monitorar", "texto": ""}}]
        weather = {"temperatura_atual": 24.5, "umidade_atual": 90, "chuva_hoje_mm": 3.0, "chuva_hoje_prob": 70,
                   "chuva_24h_mm": 10,
                   "previsao_5_dias": [{"data": f"2026-11-{16 + i}", "chuva_mm": 2.0 + i * 4, "chuva_prob": 60 + i * 5,
                                        "vento_dir": 45, "vento_kmh": 12} for i in range(5)],
                   "vento_30d": {"codigo": "NE", "nome": "Nordeste",
                                 "percentuais": {"N": 20, "NE": 40, "E": 10, "SE": 5, "S": 5, "SW": 5, "W": 5, "NW": 10}}}
        guias = {"Anthracnose": {"texto": "Manchas negras nas nervuras.\nVagens escuras.", "objetivo": "Achar cedo.",
                                 "fonte": "Embrapa Soja (2023)"}}
        produtos = {"quimico": [{"nome": "Alade", "data_anotacao": "25DAP"}], "biologico": []}
        pdf = export_pdf.build_recommendation_pdf("Zzz PDF", "Safra", doencas, weather=weather, produtos=produtos,
                                                  cultura="Soja", riscos_climaticos=riscos, guias=guias,
                                                  data_relatorio="15/11/2026").getvalue()
        self.assertEqual(pdf[:4], b"%PDF")
        if not shutil.which("pdftotext"):
            return
        texto = subprocess.run(["pdftotext", "-", "-"], input=pdf, capture_output=True).stdout.decode()
        for trecho in ("RESUMO DA SEMANA", "1. ENTRAR COM APLICAÇÃO", "2. ACOMPANHAR", "3. MONITORAR",
                       "ROSA DOS VENTOS", "Comece pelos talhões do lado Nordeste", "Probabilidade de chuva",
                       "Janela p/ pulverizar", "Sintomas iniciais a procurar", "Manchas negras nas nervuras",
                       "PLANO DE ACOMPANHAMENTO E MANEJO", "PRODUTOS DE FOLHA POR APLICAÇÃO", "25DAP",
                       "Nota da fazenda", "Sugestão OneAgro", "CRITÉRIO PARA DECIDIR"):
            self.assertIn(trecho, texto)

    def test_ponto_so_clima(self):
        import export_pdf
        pdf = export_pdf.build_recommendation_pdf("Zzz Clima", "Safra", [], weather={"temperatura_atual": 20},
                                                  mostrar_secao_doencas=False).getvalue()
        self.assertEqual(pdf[:4], b"%PDF")


class ProbabilidadeChuvaTest(unittest.TestCase):
    """Probabilidade de chuva sempre no relatorio, mesmo com a previsao principal no MET Norway (16/11/2026)."""

    def test_met_norway_com_probabilidade(self):
        from unittest import mock
        from datetime import timedelta
        import weather_forecast as wf
        hoje = wf._agora_cuiaba().date()
        dias = [(hoje + timedelta(days=i)).isoformat() for i in range(7)]
        horas = {f"{d}T{h:02d}:00": [25, 90, 0.5, 45] for d in dias for h in range(24)}
        horas.update({f"{(hoje - timedelta(days=1)).isoformat()}T{h:02d}:00": [25, 90, 0.5, 45] for h in range(24)})
        daily_met = {"time": dias, "precipitation_sum": [3.0] * 7}

        def abrir(url, timeout=30):
            if "past_days" in url or "models=" in url:
                raise OSError("limite")
            return {"daily": {"time": dias, "precipitation_probability_max": [10, 20, 30, 40, 50, 60, 70],
                              "wind_direction_10m_dominant": [45] * 7, "wind_speed_10m_max": [12] * 7}}
        antigo = wf._store
        wf._store = {"pontos": {}, "grades": {}, "estacoes": {}, "previsoes": {}, "ultima_rodada": None}
        try:
            with mock.patch.object(wf, "_abrir_json", side_effect=abrir), \
                    mock.patch.object(wf, "_met_norway", return_value=(horas, daily_met)), \
                    mock.patch.object(wf, "_salvar"):
                r = wf.get_weather_forecast(-10.5, -54.5)
        finally:
            wf._store = antigo
        self.assertEqual([d["chuva_prob"] for d in r["previsao_5_dias"]], [20, 30, 40, 50, 60])
        self.assertEqual(r["chuva_hoje_prob"], 10)
        self.assertEqual(r["previsao_5_dias"][0]["vento_dir"], 45)


class NdviNuvensDaTelaTest(unittest.TestCase):
    """O Pre-visualizar usa o "Nuvens na fazenda até" que esta' na tela, mesmo antes do autosave (17/11/2026)."""

    def test_usa_limite_enviado(self):
        from unittest import mock
        import ndvi_service
        if not models.get_user_by_username("Zzz Master NDVI"):
            models.create_user("Zzz Master NDVI", "x", is_master=True)
        uid = models.get_user_by_username("Zzz Master NDVI")["id"]
        site = "Zzz Fazenda NDVI Nuvens"
        models.set_ndvi_nuvens(site, 5)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        visto = {}

        def falso(aneis, data_alvo=None, cobertura_maxima=None, **kw):
            visto["limite"] = cobertura_maxima
            return None, "teste"
        with mock.patch.object(webapp, "_checar_acesso_site"), \
                mock.patch.object(models, "get_farm_ndvi_cars_kml", return_value=["<kml/>"]), \
                mock.patch.object(ndvi_service, "parse_kml_poligono", return_value=[[(0, 0)]]), \
                mock.patch.object(ndvi_service, "buscar_ndvi", side_effect=falso):
            c.post("/ndvi/pre_visualizar", data={"site_name": site, "nuvens": "30"})
        self.assertEqual(visto["limite"], 30)
        # o limite da tela NDVI vale so' pra essa busca; o gravado e' o do envio automatico (23/11/2026)
        self.assertEqual(models.get_ndvi_nuvens().get(site), 5)
        models.set_ndvi_nuvens(site, None)


class NdviAgendaSoEmEnviosTest(unittest.TestCase):
    """Programacao automatica do NDVI so' em Programacao de Envios; aba NDVI so' manual (pedido do Alan, 23/11/2026)."""

    def test_telas(self):
        if not models.get_user_by_username("Zzz Master NDVI"):
            models.create_user("Zzz Master NDVI", "x", is_master=True)
        uid = models.get_user_by_username("Zzz Master NDVI")["id"]
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        ndvi = c.get("/ndvi").get_data(as_text=True)
        self.assertNotIn("/ndvi/nuvens", ndvi)
        self.assertNotIn("/ndvi/agendamento/save", ndvi)
        self.assertNotIn("automático 3%", ndvi)


class NdviCarEditarVerTest(unittest.TestCase):
    """CAR da aba NDVI: nome editavel, ver o contorno e baixar o arquivo (pedido do Alan, 17/11/2026)."""

    def test_renomear_ver_baixar(self):
        from unittest import mock
        import ndvi_service
        if not models.get_user_by_username("Zzz Master NDVI"):
            models.create_user("Zzz Master NDVI", "x", is_master=True)
        uid = models.get_user_by_username("Zzz Master NDVI")["id"]
        site = "Zzz Fazenda NDVI CAR"
        anel = [[-55.0, -12.0], [-55.0, -12.1], [-55.1, -12.1], [-55.0, -12.0]]
        models.add_farm_ndvi_car(site, "arquivo.zip", ndvi_service.aneis_para_kml([anel]))
        car_id = models.get_farm_ndvi_cars(site)[0]["id"]
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        try:
            with mock.patch.object(webapp, "_checar_acesso_site"):
                r = c.post("/ndvi/car/renomear", data={"site_name": site, "car_id": car_id, "nome": "Faz Ouro Branco II"},
                           headers={"X-Autosave": "1"})
                self.assertTrue(r.get_json()["ok"])
                self.assertEqual(models.get_farm_ndvi_cars(site)[0]["nome"], "Faz Ouro Branco II")
                r = c.post("/ndvi/car/renomear", data={"site_name": site, "car_id": car_id, "nome": " "},
                           headers={"X-Autosave": "1"})
                self.assertFalse(r.get_json()["ok"])
                d = c.get(f"/ndvi/car/{car_id}/contorno?site_name={site}").get_json()
                self.assertEqual(d["aneis"][0][0], [-55.0, -12.0])
                r = c.get(f"/ndvi/car/{car_id}/kml?site_name={site}")
                self.assertIn(b"<kml", r.data)
                self.assertIn("OneAgro_CAR_Faz_Ouro_Branco_II", r.headers["Content-Disposition"])
                self.assertEqual(c.get(f"/ndvi/car/{car_id}/kml?site_name=Outra").status_code, 404)
        finally:
            models.delete_farm_ndvi_car(car_id, site)


class NdviCarRecorteTest(unittest.TestCase):
    """Recortar um pedaco do CAR e escolher quais CAR geram o NDVI (pedido do Alan, 17/11/2026)."""

    def test_recorte_e_usar(self):
        from unittest import mock
        import json as _json
        import ndvi_service
        if not models.get_user_by_username("Zzz Master NDVI"):
            models.create_user("Zzz Master NDVI", "x", is_master=True)
        uid = models.get_user_by_username("Zzz Master NDVI")["id"]
        site = "Zzz Fazenda NDVI Recorte"
        quadrado = [[-55.0, -12.0], [-54.9, -12.0], [-54.9, -12.1], [-55.0, -12.1], [-55.0, -12.0]]
        grande = models.add_farm_ndvi_car(site, "Grande", ndvi_service.aneis_para_kml([quadrado]))
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        try:
            with mock.patch.object(webapp, "_checar_acesso_site"):
                # desenho passa do contorno: o recorte fica so' dentro do CAR
                pontos = [[-55.05, -12.0], [-54.95, -12.0], [-54.95, -12.05], [-55.05, -12.05]]
                r = c.post(f"/ndvi/car/{grande}/recortar", data={"site_name": site, "pontos": _json.dumps(pontos)},
                           headers={"X-Autosave": "1"}).get_json()
                self.assertTrue(r["ok"], r)
                cars = {x["id"]: x for x in models.get_farm_ndvi_cars(site)}
                self.assertFalse(cars[grande]["usar"])
                self.assertTrue(cars[r["car_id"]]["usar"])
                self.assertEqual(cars[r["car_id"]]["nome"], "Recorte - Grande")
                kmls = models.get_farm_ndvi_cars_kml(site)
                self.assertEqual(len(kmls), 1)
                xs = [p[0] for p in ndvi_service.parse_kml_poligono(kmls[0])[0]]
                self.assertGreaterEqual(min(xs), -55.0)
                # fora do contorno = recusado
                fora = [[-56, -13], [-55.9, -13], [-55.9, -13.1]]
                r2 = c.post(f"/ndvi/car/{grande}/recortar", data={"site_name": site, "pontos": _json.dumps(fora)},
                            headers={"X-Autosave": "1"}).get_json()
                self.assertFalse(r2["ok"])
                # marcar o grande de volta (campo escondido "0" + caixa "1")
                c.post("/ndvi/car/usar", data={"site_name": site, "car_id": grande, "usar": ["0", "1"]},
                       headers={"X-Autosave": "1"})
                self.assertEqual(len(models.get_farm_ndvi_cars_kml(site)), 2)
                # nenhum marcado = usa todos
                for x in models.get_farm_ndvi_cars(site):
                    c.post("/ndvi/car/usar", data={"site_name": site, "car_id": x["id"], "usar": "0"},
                           headers={"X-Autosave": "1"})
                self.assertEqual(len(models.get_farm_ndvi_cars_kml(site)), 2)
        finally:
            for x in models.get_farm_ndvi_cars(site):
                models.delete_farm_ndvi_car(x["id"], site)


class FazendaApagadaSomeTest(unittest.TestCase):
    """Fazenda que saiu do BioScout some das telas de permissao e da lista do usuario (pedido do Alan, 18/11/2026)."""

    def test_some(self):
        from unittest import mock
        import data_reader
        models.init_db()
        models.sync_sites(["Zzz Fazenda Fica", "Zzz Fazenda Apagada"])
        if not models.get_user_by_username("Zzz Usuario Apagada"):
            models.create_user("Zzz Usuario Apagada", "x")
        uid = models.get_user_by_username("Zzz Usuario Apagada")["id"]
        ids = {r["site_name"]: r["id"] for r in models.get_db().execute(
            "SELECT id, site_name FROM sites WHERE site_name LIKE 'Zzz Fazenda %'").fetchall()}
        models.set_user_permissions(uid, [ids["Zzz Fazenda Fica"], ids["Zzz Fazenda Apagada"]])
        with mock.patch.object(data_reader, "read_sites", return_value=["Zzz Fazenda Fica"]):
            nomes = [r["site_name"] for r in models.get_all_sites()]
            self.assertIn("Zzz Fazenda Fica", nomes)
            self.assertNotIn("Zzz Fazenda Apagada", nomes)
            self.assertEqual(models.get_user_permitted_site_names(uid), ["Zzz Fazenda Fica"])
        # sem lista do BioScout ainda: nao filtra
        with mock.patch.object(data_reader, "read_sites", side_effect=FileNotFoundError):
            self.assertIn("Zzz Fazenda Apagada", [r["site_name"] for r in models.get_all_sites()])
        # a permissao continua gravada (se a fazenda voltar no BioScout, volta junto)
        self.assertIn(ids["Zzz Fazenda Apagada"], models.get_user_permitted_site_ids(uid))


class UsuarioDadosDesdeTest(unittest.TestCase):
    """"Ver dados a partir de" por usuario (pedido do Alan, 18/11/2026)."""

    def test_corte(self):
        import data_reader
        models.init_db()
        webapp.app.config["TESTING"] = True
        for nome, master in (("Zzz Master Desde", True), ("Zzz Usuario Desde", False)):
            if not models.get_user_by_username(nome):
                models.create_user(nome, "x", is_master=master)
        mid = models.get_user_by_username("Zzz Master Desde")["id"]
        uid = models.get_user_by_username("Zzz Usuario Desde")["id"]
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(mid)
            s["_fresh"] = True
        todas = data_reader.read_spore_counts()
        datas = sorted({(r.get("samplingStartTime") or "")[:10] for r in todas if r.get("samplingStartTime")})
        self.assertTrue(len(datas) > 2)
        corte = datas[len(datas) // 2]
        try:
            r = c.post(f"/admin/users/{uid}/dados-desde", data={"dados_desde": corte}, headers={"X-Autosave": "1"})
            self.assertTrue(r.get_json()["ok"])
            self.assertEqual(models.get_user_by_id(uid)["dados_desde"], corte)
            self.assertIn("desde", c.get("/admin/users").get_data(as_text=True))
            self.assertIn('value="' + corte + '"', c.get(f"/admin/users/{uid}/editar").get_data(as_text=True))
            # master nao recebe corte
            r = c.post(f"/admin/users/{mid}/dados-desde", data={"dados_desde": corte}, headers={"X-Autosave": "1"})
            self.assertFalse(r.get_json()["ok"])
            # o usuario logado so' ve leituras a partir do corte; o master ve tudo
            with webapp.app.test_request_context():
                webapp.login_user(webapp.User(models.get_user_by_id(uid)))
                cortadas = data_reader.read_spore_counts()
                self.assertTrue(cortadas)
                self.assertTrue(all((r.get("samplingStartTime") or "")[:10] >= corte for r in cortadas))
                self.assertLess(len(cortadas), len(todas))
                lookup = data_reader.build_disease_concentration_lookup(cortadas)
                self.assertTrue(all(p["data"] >= corte for pts in lookup.values() for p in pts))
                self.assertEqual(data_reader.read_site_coordinates(), data_reader.read_site_coordinates())
            with webapp.app.test_request_context():
                webapp.login_user(webapp.User(models.get_user_by_id(mid)))
                self.assertEqual(len(data_reader.read_spore_counts()), len(todas))
                lookup_todo = data_reader.build_disease_concentration_lookup(data_reader.read_spore_counts())
                self.assertTrue(any(p["data"] < corte for pts in lookup_todo.values() for p in pts))
            # sem requisicao (agendador): nunca corta
            self.assertEqual(len(data_reader.read_spore_counts()), len(todas))
            # limpar a data volta a ver tudo
            c.post(f"/admin/users/{uid}/dados-desde", data={"dados_desde": ""}, headers={"X-Autosave": "1"})
            self.assertIsNone(models.get_user_by_id(uid)["dados_desde"])
        finally:
            models.set_user_dados_desde(uid, None)


class ClienteDadosDesdeTest(unittest.TestCase):
    """"Ver dados a partir de" tambem em Gerenciar Clientes (pedido do Alan, 23/11/2026)."""

    def test_campo_no_cartao(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Desde"):
            models.create_user("Zzz Master Desde", "x", is_master=True)
        mid = models.get_user_by_username("Zzz Master Desde")["id"]
        cli = models.get_user_by_username("Zzz Cliente Desde")
        uid = cli["id"] if cli else models.criar_cliente("Zzz Cliente Desde", "segredo1", "", "5565999990991", "", None)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(mid)
            s["_fresh"] = True
        try:
            r = c.post(f"/admin/users/{uid}/dados-desde", data={"dados_desde": "2026-10-01"}, headers={"X-Autosave": "1"})
            self.assertTrue(r.get_json()["ok"])
            h = c.get("/admin/users").get_data(as_text=True)
            self.assertIn(f"/admin/users/{uid}/dados-desde", h)
            self.assertIn('value="2026-10-01"', h)
        finally:
            models.set_user_dados_desde(uid, None)
            models.delete_user(uid)


class FazendaLigadaDadosDesdeTest(unittest.TestCase):
    """"Ver dados a partir de" de cada pessoa ligada a uma fazenda sem cadastro (pedido do Alan, 23/11/2026)."""

    def test_campo_no_dados_da_fazenda(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Desde"):
            models.create_user("Zzz Master Desde", "x", is_master=True)
        mid = models.get_user_by_username("Zzz Master Desde")["id"]
        if not models.get_user_by_username("Zzz Usuario Ligado"):
            models.create_user("Zzz Usuario Ligado", "x")
        uid = models.get_user_by_username("Zzz Usuario Ligado")["id"]
        nome = "Zzz Fazenda Ligada Desde"
        models.create_virtual_farm(nome, -12.5, -55.7, 50, criado_por="Zzz Master Desde")
        site = [v for v in models.get_all_virtual_farms() if v["nome"] == nome][0]["site_name"]
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(mid)
            s["_fresh"] = True
        try:
            c.post(f"/admin/usuarios/{uid}/ligar-fazenda", data={"site_name": site})
            models.set_user_dados_desde(uid, "2026-10-05")
            h = c.get("/admin/fazendas/dados", query_string={"site": site}).get_data(as_text=True)
            self.assertIn(f"/admin/users/{uid}/dados-desde", h)
            self.assertIn('value="2026-10-05"', h)
            self.assertIn("Zzz Usuario Ligado vê dados a partir de", h)
        finally:
            models.set_user_dados_desde(uid, None)
            models.delete_virtual_farm(site)


class DadosDesdeCadastroClienteTest(unittest.TestCase):
    """Cliente do app nasce com "Ver dados a partir de" = data do cadastro (pedido do Alan, 23/11/2026)."""

    def test_cliente_novo_e_campo_so_master(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        uid = models.criar_cliente("Zzz Cliente Data Cad", "segredo1", "", "5565999990993", "", None)
        try:
            self.assertEqual(models.get_user_by_id(uid)["dados_desde"], models._agora_cuiaba()[:10])
            if not models.get_user_by_username("Zzz Admin Desde"):
                models.create_user("Zzz Admin Desde", "x", is_admin=True)
            aid = models.get_user_by_username("Zzz Admin Desde")["id"]
            c = webapp.app.test_client()
            with c.session_transaction() as s:
                s["_user_id"] = str(aid)
                s["_fresh"] = True
            self.assertIn(c.post(f"/admin/users/{uid}/dados-desde", data={"dados_desde": ""}).status_code, (302, 403))
            self.assertEqual(models.get_user_by_id(uid)["dados_desde"], models._agora_cuiaba()[:10])
            self.assertNotIn("vê os dados a partir", c.get("/admin/users").get_data(as_text=True))
        finally:
            models.delete_user(uid)


class CadastroIncompletoVisivelTest(unittest.TestCase):
    """O que falta no cadastro aparece escrito dentro do cartao (no celular nao ha' o title do selo; 23/11/2026)."""

    def test_lista_no_cartao(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Desde"):
            models.create_user("Zzz Master Desde", "x", is_master=True)
        mid = models.get_user_by_username("Zzz Master Desde")["id"]
        uid = models.criar_cliente("Zzz Cliente Incompleto", "segredo1", "", "5565999990996", "", None)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(mid)
            s["_fresh"] = True
        try:
            h = c.get("/admin/users").get_data(as_text=True)
            self.assertIn("Para completar o cadastro, falta:", h)
            self.assertIn("<li>plano</li>", h)
        finally:
            models.delete_user(uid)


class GerenciarFazendasTest(unittest.TestCase):
    """Gerenciar Fazendas (ex-Gerenciar Clientes) so' com fazendas; pessoas na aba Usuarios (pedido do Alan, 23/11/2026)."""

    def test_completo_e_pessoas_em_usuarios(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Desde"):
            models.create_user("Zzz Master Desde", "x", is_master=True)
        mid = models.get_user_by_username("Zzz Master Desde")["id"]
        if not models.get_user_by_username("Zzz Usuario GF"):
            models.create_user("Zzz Usuario GF", "x")
        uid = models.get_user_by_username("Zzz Usuario GF")["id"]
        cli = models.criar_cliente("Zzz Cliente GF", "segredo1", "", "5565999990998", "", None)
        nome = "Zzz Fazenda GF"
        models.create_virtual_farm(nome, -12.5, -55.7, 50, criado_por="Zzz Master Desde")
        site = [v for v in models.get_all_virtual_farms() if v["nome"] == nome][0]["site_name"]
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(mid)
            s["_fresh"] = True
        try:
            def cartao():
                h = c.get("/admin/solicitacoes").get_data(as_text=True)
                self.assertIn("<h4 class=\"mb-2\">Gerenciar Fazendas</h4>", h)
                return h.split('<span class="toggle-icon">▶</span> ' + nome, 1)[1].split('class="card shadow-sm mb-3 cad-card"', 1)[0]
            parte = cartao()
            self.assertIn("⚠ Cadastro incompleto", parte)
            self.assertIn("<li>ligar a um cliente ou usuário</li>", parte)
            # coordenadas na mesma linha do nome (25/11/2026)
            self.assertIn('class="coords-gf"', parte.split('<div class="cad-selos">', 1)[0])
            self.assertIn("📍 -12.50000, -55.70000", parte)
            # dados + alguem ligado = completo (grupo nao e' preciso)
            c.post("/admin/fazendas/dados/salvar", headers={"X-Autosave": "1"}, data={
                "site_name": site, "cidade": "Sorriso", "uf": "MT", "grupo_cultura": "cereais", "culturas": ["Soja"],
                "area_cultura__Soja": "100"})
            c.post(f"/admin/usuarios/{uid}/ligar-fazenda", data={"site_name": site})
            # cultura sem area = ainda incompleto (23/11/2026)
            c.post("/admin/fazendas/dados/salvar", headers={"X-Autosave": "1"}, data={
                "site_name": site, "cidade": "Sorriso", "uf": "MT", "grupo_cultura": "cereais", "culturas": ["Soja"]})
            parte = cartao()
            self.assertIn("⚠ Cadastro incompleto", parte)
            with webapp.app.test_request_context():
                self.assertIn("área plantada de pelo menos uma cultura de x", " ".join(webapp._faltando_na_fazenda(
                    {"cidade": "Sorriso", "uf": "MT", "culturas": "Soja, Milho", "nome_exibicao": "x"})))
            c.post("/admin/fazendas/dados/salvar", headers={"X-Autosave": "1"}, data={
                "site_name": site, "cidade": "Sorriso", "uf": "MT", "grupo_cultura": "cereais", "culturas": ["Soja", "Milho"],
                "area_cultura__Soja": "100"})
            parte = cartao()
            self.assertIn("✔ Cadastro completo", parte)
            tela = c.get("/admin/solicitacoes").get_data(as_text=True)
            self.assertIn(">Nome da Propriedade</a>", tela)
            self.assertIn(">Grupo Econômico</a>", tela)
            self.assertNotIn("Fazenda sem cadastro", parte)
            self.assertIn("Grupo econômico*", c.get("/admin/fazendas/dados", query_string={"site": site}).get_data(as_text=True))
            # Usuarios: cartao da pessoa (assinatura/cortesia) na linha do cliente; menu renomeado
            u = c.get("/admin/users").get_data(as_text=True)
            self.assertIn(f'data-pessoa-row="{cli}"', u)
            self.assertIn("Cortesia até", u)
            self.assertIn(">Gerenciar Fazendas", u)
            self.assertNotIn(">Gerenciar Clientes", u)
            self.assertNotIn("Cortesia até", c.get("/admin/solicitacoes").get_data(as_text=True))
        finally:
            models.delete_virtual_farm(site)
            models.delete_user(cli)


class PlanoNoMapasDeEstudoTest(unittest.TestCase):
    """Plano e Cortesia da fazenda no Mapas de Estudo; "+ Novo cliente" saiu de Usuarios (pedido do Alan, 23/11/2026)."""

    def test_plano_e_cortesia(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Desde"):
            models.create_user("Zzz Master Desde", "x", is_master=True)
        mid = models.get_user_by_username("Zzz Master Desde")["id"]
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(mid)
            s["_fresh"] = True
        intel = next(p for p in models.get_planos() if p["nome"] == "Inteligência")
        nome = "Zzz Ponto Plano"
        try:
            c.post("/mapa-interpolado/adicionar", data={"nome": nome, "lat": "-12.5", "lon": "-55.7", "raio_km": "50",
                                                       "tipo": "doenca", "country_code": "BR",
                                                       "plano_id": str(intel["id"]), "cortesia_ate": "2099-03-31"})
            site = [v for v in models.get_all_virtual_farms() if v["nome"] == nome][0]["site_name"]
            with webapp.app.test_request_context():
                self.assertEqual(webapp._plano_da_fazenda(site)["texto"], "Inteligência · até 31/03/2099")
            r = c.post("/mapa-interpolado/plano", headers={"X-Autosave": "1"},
                       data={"site_name": site, "plano_id": str(intel["id"]), "cortesia_ate": "2020-01-01"})
            self.assertTrue(r.get_json()["ok"])
            self.assertIn("venceu em 01/01/2020", r.get_json()["selo"])
            tela = c.get("/mapa-interpolado").get_data(as_text=True)
            self.assertIn("/mapa-interpolado/plano", tela)
            self.assertIn('value="2020-01-01"', tela)
            self.assertNotIn("+ Novo cliente", c.get("/admin/users").get_data(as_text=True))
        finally:
            for v in models.get_all_virtual_farms():
                if v["nome"] == nome:
                    models.delete_virtual_farm(v["site_name"])


class NovoUsuarioFazendasTest(unittest.TestCase):
    """Novo usuario ja' com fazendas e data de inicio (pedido do Alan, 23/11/2026)."""

    def test_cria_com_fazendas_e_data(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Desde"):
            models.create_user("Zzz Master Desde", "x", is_master=True)
        mid = models.get_user_by_username("Zzz Master Desde")["id"]
        nome = "Zzz Fazenda Novo Usuario"
        models.create_virtual_farm(nome, -12.5, -55.7, 50, criado_por="Zzz Master Desde")
        site = [v for v in models.get_all_virtual_farms() if v["nome"] == nome][0]["site_name"]
        sid = next(s["id"] for s in models.get_all_sites() if s["site_name"] == site)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(mid)
            s["_fresh"] = True
        try:
            tela = c.get("/admin/users").get_data(as_text=True)
            self.assertIn("Fazendas que terá acesso", tela)
            self.assertIn('name="dados_desde" id="novo_dados_desde"', tela)
            c.post("/admin/users/create", data={"username": "Zzz Novo Com Faz", "email": "n@t.com", "codigo_pais": "55",
                                                "numero_telefone": "65999990777", "nivel": "usuario",
                                                "site_ids": [str(sid)], "dados_desde": "2026-10-01"})
            u = models.get_user_by_username("Zzz Novo Com Faz")
            self.assertIn(site, models.get_user_permitted_site_names(u["id"]))
            self.assertIn(sid, models.get_user_report_site_ids(u["id"]))
            self.assertEqual(u["dados_desde"], "2026-10-01")
        finally:
            u = models.get_user_by_username("Zzz Novo Com Faz")
            if u:
                models.delete_user(u["id"])
            models.delete_virtual_farm(site)


class GoogleDriveTest(unittest.TestCase):
    """Copia diaria dos relatorios no Google Drive (pedido do Alan, 26/11/2026): Google simulado."""

    def test_envio_diario_e_conexao(self):
        import urllib.error
        import urllib.request
        import google_drive
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Drive"):
            models.create_user("Zzz Master Drive", "x", is_master=True)
        mid = models.get_user_by_username("Zzz Master Drive")["id"]
        nome = "Zzz Fazenda Drive"
        models.create_virtual_farm(nome, -12.5, -55.7, 50, criado_por="Zzz Master Drive")
        site = [v for v in models.get_all_virtual_farms() if v["nome"] == nome][0]["site_name"]
        gravados = []

        def enviar(token, pasta, arquivo, conteudo, mime):
            gravados.append((pasta, arquivo, mime))
            return "id"
        sem_rede = mock.patch.object(urllib.request, "urlopen", side_effect=urllib.error.URLError("sem rede"))
        patches = [
            sem_rede,
            mock.patch.object(google_drive, "CLIENT_ID", "id"), mock.patch.object(google_drive, "CLIENT_SECRET", "seg"),
            mock.patch.object(google_drive, "token_de_acesso", return_value="tok"),
            mock.patch.object(google_drive, "pastas", return_value={"raiz": "R", "PDF": "P", "TXT": "T", "NDVI": "N"}),
            mock.patch.object(google_drive, "enviar_arquivo", side_effect=enviar),
            mock.patch.object(google_drive, "subpasta", side_effect=lambda tok, nome, pai: f"{pai}/{nome}"),
            mock.patch.object(webapp, "_fazendas_google_drive", return_value=[site]),
        ]
        for p in patches:
            p.start()
        try:
            models.set_setting("gdrive_refresh_token", "rt")
            # sem plano = nada vai pro Drive (o plano decide os arquivos, 26/11/2026)
            with webapp.app.app_context():
                self.assertTrue(webapp._enviar_relatorios_google_drive())
            self.assertEqual(gravados, [])
            pid = models.salvar_plano(None, "Zzz Plano Drive", "", 10.0, "MONTHLY", 1, 50, ["clima", "drive_pdf", "drive_txt"], True, 9)
            models.salvar_plano_fazenda(site, plano_id=pid)
            with webapp.app.app_context():
                self.assertEqual(webapp._arquivos_drive_da_fazenda(site), ["PDF", "TXT"])
                self.assertTrue(webapp._enviar_relatorios_google_drive())
            pastas = sorted(p for p, _a, _m in gravados)
            self.assertEqual(pastas, ["P/Sem cliente", "T/Sem cliente"])
            self.assertTrue(any(a.startswith("OneAgro_Relatorio_Zzz_Fazenda_Drive_") and a.endswith(".pdf") for _p, a, _m in gravados))
            st = webapp._status_google_drive()
            self.assertEqual((st["pdf"], st["txt"], st["ndvi"], st["andamento"]), (1, 1, 0, False))
            # NDVI: so' com o plano liberando, e so' a imagem nova, uma vez
            models.add_farm_ndvi_historico(site, "2026-11-20", b"\x89PNG fake")
            gravados.clear()
            with webapp.app.app_context():
                webapp._enviar_relatorios_google_drive()
            self.assertEqual([a for p, a, _m in gravados if p.startswith("N")], [])
            plano = next(p for p in models.get_planos() if p["id"] == pid)
            models.salvar_plano(pid, plano["nome"], "", 10.0, "MONTHLY", 1, 50, ["clima", "drive_pdf", "drive_txt", "drive_ndvi"], True, 9)
            gravados.clear()
            with webapp.app.app_context():
                webapp._enviar_relatorios_google_drive()
                webapp._enviar_relatorios_google_drive()
            ndvi = [a for p, a, _m in gravados if p == "N/Sem cliente"]
            self.assertEqual(ndvi, ["OneAgro_NDVI_Zzz_Fazenda_Drive_2026-11-20.png"])
            # agendado: antes das 5h nao roda; depois roda uma vez por dia
            models.set_setting("gdrive_ultimo_dia", "")
            models.set_setting("gdrive_tentativa", "")
            from datetime import datetime as dt
            with mock.patch.object(webapp, "_google_drive_em_segundo_plano") as fundo:
                with mock.patch.object(webapp, "_agora_cuiaba_dt", return_value=dt(2026, 11, 26, 4, 59)):
                    webapp._google_drive_agendado()
                fundo.assert_not_called()
                with mock.patch.object(webapp, "_agora_cuiaba_dt", return_value=dt(2026, 11, 26, 5, 0)):
                    webapp._google_drive_agendado()
                    webapp._google_drive_agendado()
                self.assertEqual(fundo.call_count, 1)
            # tela e conexao
            c = webapp.app.test_client()
            with c.session_transaction() as s:
                s["_user_id"] = str(mid)
                s["_fresh"] = True
            # envio em andamento: a tela abre e mostra o progresso (erro 500 em 26/11/2026)
            import json as _json
            agora = webapp._agora_cuiaba_dt().strftime("%Y-%m-%d %H:%M")
            models.set_setting("gdrive_status", _json.dumps({"andamento": True, "inicio": agora, "txt": 2, "fazendas": 9}))
            r = c.get("/admin/exportar")
            self.assertEqual(r.status_code, 200)
            self.assertIn("🔄 Enviando desde", r.get_data(as_text=True))
            models.set_setting("gdrive_status", _json.dumps({"andamento": True, "inicio": "2026-01-01 05:00"}))
            self.assertNotIn("🔄 Enviando desde", c.get("/admin/exportar").get_data(as_text=True))
            tela = c.get("/admin/exportar").get_data(as_text=True)
            self.assertIn("Google Drive — cópia diária", tela)
            envios = c.get("/whatsapp").get_data(as_text=True)
            self.assertIn("Envio para o Google Drive", envios)
            self.assertIn("Zzz Fazenda Drive", envios)
            self.assertNotIn("Enviar por WhatsApp", envios)
            r = c.post("/envios/hora-drive", data={"hora": "6"}, headers={"X-Autosave": "1"}).get_json()
            self.assertTrue(r["ok"])
            self.assertEqual(webapp._hora_google_drive(), 6)
            self.assertFalse(c.post("/envios/hora-drive", data={"hora": "25"}, headers={"X-Autosave": "1"}).get_json()["ok"])
            models.set_setting("gdrive_hora", "")
            self.assertIn("⬆ Enviar agora", tela)
            models.set_setting("gdrive_refresh_token", "")
            r = c.get("/admin/google-drive/conectar")
            self.assertIn("accounts.google.com", r.headers["Location"])
            self.assertIn("google-drive%2Fretorno", r.headers["Location"])
            with c.session_transaction() as s:
                estado = s["gdrive_state"]
            with mock.patch.object(google_drive, "trocar_codigo", return_value={"refresh_token": "novo", "access_token": "a"}), \
                    mock.patch.object(google_drive, "email_da_conta", return_value="zzz@exemplo.com"):
                c.get("/admin/google-drive/retorno", query_string={"state": estado, "code": "abc"})
            self.assertEqual(models.get_setting("gdrive_refresh_token"), "novo")
            self.assertIn("zzz@exemplo.com", c.get("/admin/exportar").get_data(as_text=True))
            with mock.patch.object(google_drive, "revogar"):
                c.post("/admin/google-drive/desconectar")
            self.assertEqual(models.get_setting("gdrive_refresh_token"), "")
        finally:
            for p in patches:
                p.stop()
            models.set_setting("gdrive_refresh_token", "")
            models.set_setting("gdrive_status", "")
            models.set_setting("gdrive_por_fazenda", "")
            models.set_setting("gdrive_ndvi_enviados", "")
            models.delete_virtual_farm(site)
            for p in models.get_planos():
                if p["nome"] == "Zzz Plano Drive":
                    models.excluir_plano(p["id"])

    def test_politica_privacidade_publica(self):
        c = webapp.app.test_client()
        r = c.get("/privacidade")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Uso Limitado", r.get_data(as_text=True))
        self.assertIn("/privacidade", c.get("/login").get_data(as_text=True))

    def test_sem_whatsapp_e_drive_nos_planos(self):
        """WhatsApp saiu do site e o Drive segue o plano (pedido do Alan, 26/11/2026)."""
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Master Drive"):
            models.create_user("Zzz Master Drive", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Drive")["id"])
            s["_fresh"] = True
        # A tela voltou so' pra recuperar senha (pedido do Alan, 26/11/2026).
        self.assertIn("Esqueci minha senha", c.get("/admin/whatsapp").get_data(as_text=True))
        for rota in ("/recommendations", "/ndvi", "/admin/users", "/whatsapp"):
            with self.subTest(rota=rota):
                html = c.get(rota, follow_redirects=True).get_data(as_text=True)
                self.assertNotIn("Enviar por WhatsApp", html)
                self.assertNotIn("Gerenciar WhatsApp", html)
        self.assertIn("Google Drive: relatório em PDF", c.get("/admin/planos").get_data(as_text=True))
        publico = webapp.app.test_client().get("/planos").get_data(as_text=True)
        self.assertNotIn("Google Drive", publico)
        self.assertNotIn("WhatsApp", publico)
        # planos que ja' existiam ganham os 3 arquivos do Drive uma vez so' (o Master tira depois)
        pid = models.salvar_plano(None, "Zzz Plano Antigo", "", 1.0, "MONTHLY", 1, 50, ["clima"], True, 9)
        models.set_setting("recursos_drive_migrados", "")
        conn = models.get_db()
        conn.execute("DELETE FROM app_settings WHERE key = 'recursos_drive_migrados'")
        conn.commit()
        conn.close()
        models.init_db()
        antigo = next(p for p in models.get_planos() if p["id"] == pid)
        self.assertTrue(set(models.RECURSOS_DRIVE) <= set(antigo["recursos"]))
        models.salvar_plano(pid, antigo["nome"], "", 1.0, "MONTHLY", 1, 50, ["clima"], True, 9)
        models.init_db()
        self.assertEqual(next(p for p in models.get_planos() if p["id"] == pid)["recursos"], ["clima"])
        models.excluir_plano(pid)
        self.assertTrue(all(set(models.RECURSOS_DRIVE) <= set(r) for _n, r, _l, _v in models.PLANOS_PADRAO))


class EntrarComGoogleTest(unittest.TestCase):
    """Entrar com Google pelo e-mail da conta (pedido do Alan, 26/11/2026); Google simulado."""

    def test_entrar_com_google(self):
        import google_drive
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Google Um"):
            models.create_user("Zzz Google Um", "x", email="Zzz.Um@Exemplo.com")
        for nome in ("Zzz Google Dois", "Zzz Google Tres"):
            if not models.get_user_by_username(nome):
                models.create_user(nome, "x", email="zzz.repetido@exemplo.com")
        with mock.patch.object(google_drive, "CLIENT_ID", ""), mock.patch.object(google_drive, "CLIENT_SECRET", ""):
            self.assertNotIn("Entrar com Google", webapp.app.test_client().get("/login").get_data(as_text=True))
        with mock.patch.object(google_drive, "CLIENT_ID", "id"), mock.patch.object(google_drive, "CLIENT_SECRET", "seg"):
            c = webapp.app.test_client()
            self.assertIn("Entrar com Google", c.get("/login").get_data(as_text=True))
            self.assertIn("Entrar com Google", c.get("/esqueci-senha").get_data(as_text=True))

            def entrar(email, verificado=True):
                c.get("/logout")
                r = c.get("/login/google")
                self.assertIn("accounts.google.com", r.headers["Location"])
                self.assertIn("login%2Fgoogle%2Fretorno", r.headers["Location"])
                with c.session_transaction() as s:
                    estado = s["google_login_state"]
                with mock.patch.object(google_drive, "dados_do_login",
                                       return_value={"email": email, "email_verified": verificado, "name": "Z"}):
                    return c.get("/login/google/retorno", query_string={"state": estado, "code": "abc"})
            r = entrar("zzz.um@exemplo.com")
            self.assertEqual(r.headers["Location"], "/mapa")
            with c.session_transaction() as s:
                self.assertEqual(s.get("_user_id"), str(models.get_user_by_username("Zzz Google Um")["id"]))
            # e-mail sem conta = cadastro com a conta Google, sem senha
            r = entrar("zzz.novo.google@exemplo.com")
            self.assertTrue(r.headers["Location"].endswith("/solicitar-acesso"))
            with c.session_transaction() as s:
                self.assertEqual(s["google_cadastro"]["email"], "zzz.novo.google@exemplo.com")
            pag = c.get("/solicitar-acesso").get_data(as_text=True)
            self.assertIn("Conta Google conectada", pag)
            self.assertIn('value="zzz.novo.google@exemplo.com"', pag)
            self.assertNotIn("Preencher com Google", pag)
            self.assertNotIn('checked required', pag)  # vindo do login: "Você é" fica pra escolher
            with c.session_transaction() as s:
                s.pop("google_cadastro")
            self.assertIn("Preencher com Google", c.get("/solicitar-acesso").get_data(as_text=True))
            # botao do "Criar conta" leva o "Você é" escolhido e volta com ele marcado
            c.get("/login/google", query_string={"perfil": "tecnico"})
            with c.session_transaction() as s:
                estado = s["google_login_state"]
            with mock.patch.object(google_drive, "dados_do_login",
                                   return_value={"email": "zzz.tec.google@exemplo.com", "email_verified": True, "name": "Zzz T"}):
                c.get("/login/google/retorno", query_string={"state": estado, "code": "abc"})
            pag = c.get("/solicitar-acesso").get_data(as_text=True)
            self.assertIn('id="perfil_tecnico" value="tecnico" checked', pag)
            with c.session_transaction() as s:
                s.pop("google_cadastro")
            for email, verificado in (("zzz.repetido@exemplo.com", True), ("zzz.um@exemplo.com", False)):
                with self.subTest(email=email):
                    r = entrar(email, verificado)
                    self.assertTrue(r.headers["Location"].endswith("/login"))
                    with c.session_transaction() as s:
                        self.assertIsNone(s.get("_user_id"))
            # estado errado nao entra
            c.get("/login/google")
            r = c.get("/login/google/retorno", query_string={"state": "outro", "code": "abc"})
            self.assertTrue(r.headers["Location"].endswith("/login"))


class EsqueciSenhaEmailTest(unittest.TestCase):
    """Senha temporaria por e-mail (pedido do Alan, 26/11/2026)."""

    def test_envia_so_com_usuario_e_email_certos(self):
        import email_envio
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Esqueci Email"):
            models.create_user("Zzz Esqueci Email", "x", email="Zzz.Esqueci@Exemplo.com")
        enviados = []

        with mock.patch.object(email_envio, "enviar", side_effect=lambda *a: enviados.append(a) or (True, "ok")), \
                mock.patch.object(webapp, "_email_em_segundo_plano", lambda f: f()):
            webapp._esqueci_senha_ultima_solicitacao.clear()
            c = webapp.app.test_client()
            pag = c.get("/esqueci-senha").get_data(as_text=True)
            self.assertIn('name="contato"', pag)
            r = c.post("/esqueci-senha", data={"username": "Zzz Esqueci Email", "contato": "outro@exemplo.com"})
            self.assertTrue(r.headers["Location"].endswith("/login"))
            self.assertEqual(enviados, [])
            c.post("/esqueci-senha", data={"username": "Zzz Esqueci Email", "contato": "ZZZ.esqueci@exemplo.com"})
            self.assertEqual(len(enviados), 1)
            para, assunto, texto = enviados[0]
            self.assertEqual(para, "Zzz.Esqueci@Exemplo.com")
            self.assertIn("Senha temporária", texto)
            # pedido repetido logo em seguida nao manda de novo
            c.post("/esqueci-senha", data={"username": "Zzz Esqueci Email", "email": "zzz.esqueci@exemplo.com"})
            self.assertEqual(len(enviados), 1)
        u = models.get_user_by_username("Zzz Esqueci Email")
        models.delete_user(u["id"])

    def test_whatsapp_pelo_celular(self):
        """Conta com celular: vai pelo WhatsApp (conectado so' pra recuperar senha); falhou = e-mail."""
        import email_envio
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Esqueci Whats"):
            models.create_user("Zzz Esqueci Whats", "x", email="zzz.whats@exemplo.com", telefone="5566999990011")
        whats, emails = [], []
        with mock.patch.object(webapp.whatsapp, "send_whatsapp", side_effect=lambda *a: whats.append(a) or (True, "ok")), \
                mock.patch.object(email_envio, "enviar", side_effect=lambda *a: emails.append(a) or (True, "ok")), \
                mock.patch.object(webapp, "_email_em_segundo_plano", lambda f: f()):
            webapp._esqueci_senha_ultima_solicitacao.clear()
            c = webapp.app.test_client()
            c.post("/esqueci-senha", data={"username": "Zzz Esqueci Whats", "contato": "(66)99999-0012"})
            self.assertEqual(whats, [])
            c.post("/esqueci-senha", data={"username": "Zzz Esqueci Whats", "contato": "(66)99999-0011"})
            self.assertEqual(len(whats), 1)
            self.assertEqual(whats[0][0], "5566999990011")
            self.assertIn("Senha temporária", whats[0][1])
            self.assertEqual(emails, [])
        # e-mail digitado = vai por e-mail, mesmo com celular no cadastro (usuario sem diferenciar maiusculas)
        with mock.patch.object(webapp.whatsapp, "send_whatsapp", side_effect=lambda *a: whats.append(a) or (True, "ok")), \
                mock.patch.object(email_envio, "enviar", side_effect=lambda *a: emails.append(a) or (True, "ok")), \
                mock.patch.object(webapp, "_email_em_segundo_plano", lambda f: f()):
            webapp._esqueci_senha_ultima_solicitacao.clear()
            c.post("/esqueci-senha", data={"username": "zzz esqueci whats", "contato": "zzz.whats@exemplo.com"})
            self.assertEqual(len(emails), 1)
            self.assertEqual(len(whats), 1)
        # celular digitado e WhatsApp fora do ar = e-mail do cadastro
        with mock.patch.object(webapp.whatsapp, "send_whatsapp", return_value=(False, "desconectado")), \
                mock.patch.object(email_envio, "enviar", side_effect=lambda *a: emails.append(a) or (True, "ok")), \
                mock.patch.object(webapp, "_email_em_segundo_plano", lambda f: f()):
            webapp._esqueci_senha_ultima_solicitacao.clear()
            c.post("/esqueci-senha", data={"username": "Zzz Esqueci Whats", "contato": "66999990011"})
            self.assertEqual(len(emails), 2)
        log = json.loads(models.get_setting("recuperacao_senha_log"))
        self.assertTrue(log[0]["resultado"].startswith("enviado para zzz.whats"))
        self.assertNotIn("Senha", json.dumps(log, ensure_ascii=False))
        u = models.get_user_by_username("Zzz Esqueci Whats")
        models.delete_user(u["id"])

    def test_tela_whatsapp_so_master(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Whats Master"):
            models.create_user("Zzz Whats Master", "x", is_master=True)
        if not models.get_user_by_username("Zzz Whats Admin"):
            models.create_user("Zzz Whats Admin", "x", is_admin=True)
        for nome, esperado in (("Zzz Whats Master", 200), ("Zzz Whats Admin", 403)):
            c = webapp.app.test_client()
            with c.session_transaction() as s:
                s["_user_id"] = str(models.get_user_by_username(nome)["id"])
                s["_fresh"] = True
            with mock.patch.object(webapp.whatsapp, "get_status", return_value={"connected": False, "qr": None}):
                r = c.get("/admin/whatsapp")
            self.assertIn(r.status_code, (esperado, 302) if esperado != 200 else (200,), nome)
            if esperado == 200:
                self.assertIn("Esqueci minha senha", r.get_data(as_text=True))

    def test_numero_conectado_e_pedidos_no_fim(self):
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Whats Master"):
            models.create_user("Zzz Whats Master", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Whats Master")["id"])
            s["_fresh"] = True
        with mock.patch.object(webapp.whatsapp, "get_status",
                               return_value={"connected": True, "qr": None, "numero": "5543991147595", "nome": "OneAgro"}):
            html = c.get("/admin/whatsapp").get_data(as_text=True)
        self.assertIn("(43)99114-7595", html)
        self.assertGreater(html.index('id="pedidos"'), html.index("💬 WhatsApp"))

    def test_sem_smtp_nao_envia(self):
        import email_envio
        with mock.patch.dict(os.environ, {"SMTP_HOST": "", "SMTP_USER": "", "SMTP_PASSWORD": ""}):
            self.assertFalse(email_envio.configurado())
            ok, _ = email_envio.enviar("a@b.com", "x", "y")
            self.assertFalse(ok)


class EmailPeloGmailTest(unittest.TestCase):
    """E-mail da recuperacao de senha pelo Gmail / Google Workspace (pedido do Alan, 26/11/2026); Google simulado."""

    def test_conectar_enviar_desconectar(self):
        import email_envio
        import google_drive
        models.init_db()
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Gmail Master"):
            models.create_user("Zzz Gmail Master", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Gmail Master")["id"])
            s["_fresh"] = True
        with mock.patch.object(google_drive, "CLIENT_ID", "id"), mock.patch.object(google_drive, "CLIENT_SECRET", "seg"), \
                mock.patch.object(webapp.whatsapp, "get_status", return_value={"connected": False, "qr": None}):
            self.assertIn("Conectar e-mail (Google)", c.get("/admin/whatsapp").get_data(as_text=True))
            r = c.post("/admin/email/conectar")
            self.assertIn("gmail.send", urllib.parse.unquote(r.headers["Location"]))
            self.assertIn("google-drive%2Fretorno", r.headers["Location"])
            with c.session_transaction() as s:
                estado = s["gmail_state"]
            with mock.patch.object(google_drive, "trocar_codigo", return_value={"refresh_token": "rt", "access_token": "at"}), \
                    mock.patch.object(google_drive, "email_do_token", return_value="contato@oneagro.com.br"):
                r = c.get("/admin/google-drive/retorno", query_string={"state": estado, "code": "abc"})
            self.assertTrue(r.headers["Location"].endswith("/admin/whatsapp"))
            self.assertEqual(models.get_setting("gmail_email"), "contato@oneagro.com.br")
            # permissao desmarcada na tela do Google = nao conecta
            models.set_setting("gmail_email", "")
            r = c.post("/admin/email/conectar")
            with c.session_transaction() as s:
                estado = s["gmail_state"]
            with mock.patch.object(google_drive, "trocar_codigo",
                                   return_value={"refresh_token": "rt2", "access_token": "at", "scope": "openid email"}), \
                    mock.patch.object(google_drive, "email_do_token", return_value="contato@oneagro.com.br"), \
                    mock.patch.object(google_drive, "revogar"):
                c.get("/admin/google-drive/retorno", query_string={"state": estado, "code": "abc"})
            self.assertEqual(models.get_setting("gmail_email"), "")
            models.set_setting("gmail_email", "contato@oneagro.com.br")
            self.assertEqual(models.get_setting("gdrive_refresh_token") or "", models.get_setting("gdrive_refresh_token") or "")
            enviados = []
            with mock.patch.object(google_drive, "enviar_gmail", side_effect=lambda *a: enviados.append(a) or "id1"):
                ok, _ = email_envio.enviar("zzz@exemplo.com", "Assunto", "Texto")
                self.assertTrue(ok)
                c.post("/admin/email/teste", data={"para": "zzz.teste@exemplo.com"})
            self.assertEqual(enviados[0][:4], ("rt", "zzz@exemplo.com", "Assunto", "Texto"))
            self.assertEqual(enviados[1][1], "zzz.teste@exemplo.com")
            with mock.patch.object(google_drive, "revogar"):
                c.post("/admin/email/desconectar")
            self.assertEqual(models.get_setting("gmail_email"), "")
