"""Testes do app de clientes pagantes (planos, cadastro, Asaas, area /app).

Rodar a partir de webapp/:  python -m unittest tests.test_assinaturas
Usa um banco temporario (BIOSCOUT_DB_PATH) e os CSVs reais de data/;
Asaas e previsao do tempo sao simulados (nao acessa rede).
"""
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from unittest import mock

_TMP = tempfile.mkdtemp()
os.environ["BIOSCOUT_DB_PATH"] = os.path.join(_TMP, "teste.db")
os.environ.pop("ASAAS_API_KEY", None)
os.environ["ASAAS_WEBHOOK_TOKEN"] = "tok-teste"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
import models  # noqa: E402
import data_reader  # noqa: E402

CPF_VALIDO = "52998224725"
CNPJ_VALIDO = "11222333000181"
UA_APP = "Mozilla/5.0 (iPhone) OneAgroApp"

_WEATHER = {
    "temperatura_atual": 24.0, "umidade_atual": 95, "chuva_atual_mm": 0.0, "chuva_24h_mm": 12.0,
    "previsao_5_dias": [], "ultimas_24h": [{"temp": 24.0, "umidade": 95, "chuva": 1.0}] * 24,
    "previsao_horaria_por_dia": {}, "fonte_previsao": "Teste",
}


# Campos da tela "Nova fazenda" (tipo de cultura, cultura e area; 08/11/2026).
NOVA_FAZENDA = {"grupo_cultura": "cereais", "culturas": "Soja", "area_cultura__Soja": "100"}

class AssinaturasTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        cls.plano_basico = models.salvar_plano(None, "Básico", "", 49.9, "MONTHLY", 1, 50, ["clima"], True, 1)
        cls.plano_pro = models.salvar_plano(
            None, "Pro", "", 99.9, "MONTHLY", 3, 80, ["clima", "esporos", "risco", "fungicidas"], True, 2,
        )
        cls.patch_weather = mock.patch.object(webapp.weather_forecast, "get_weather_forecast", return_value=dict(_WEATHER))
        cls.patch_weather.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch_weather.stop()

    def setUp(self):
        self.client = webapp.app.test_client()

    def _cadastrar(self, nome, plano_id=None):
        """Cliente ja' aprovado pelo Master (conta nova so' nasce pela
        solicitacao de acesso, ver test_solicitacoes) e logado."""
        uid = models.criar_cliente(nome, "segredo1", "teste@example.com", "5565999990000", CPF_VALIDO,
                                   plano_id or self.plano_pro)
        with self.client.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        return uid

    def _liberar(self, nome):
        user = models.get_user_by_username(nome)
        hoje = models._hoje_cuiaba()
        models.registrar_pagamento(user["id"], f"pay_{user['id']}", 99.9, "CONFIRMED", hoje, None)
        return user

    # --- unidades ---------------------------------------------------------

    def test_cpf_cnpj(self):
        self.assertTrue(webapp._cpf_cnpj_valido(CPF_VALIDO))
        self.assertTrue(webapp._cpf_cnpj_valido(CNPJ_VALIDO))
        self.assertFalse(webapp._cpf_cnpj_valido("52998224724"))
        self.assertFalse(webapp._cpf_cnpj_valido("11111111111"))
        self.assertFalse(webapp._cpf_cnpj_valido("123"))

    def test_somar_meses_fim_de_mes(self):
        self.assertEqual(models._somar_meses("2026-01-31", 1).isoformat(), "2026-02-28")
        self.assertEqual(models._somar_meses("2026-11-15", 3).isoformat(), "2027-02-15")

    def test_pagamento_libera_e_estorno_bloqueia(self):
        uid = models.criar_cliente("Zzz Teste Pagamento", "segredo1", "a@b.com", "5565999990000", CPF_VALIDO, self.plano_pro)
        self.assertFalse(models.assinatura_liberada(models.get_assinatura(uid)))
        hoje = models._hoje_cuiaba()
        models.registrar_pagamento(uid, "pay_x", 99.9, "PENDING", hoje, "https://fatura")
        self.assertFalse(models.assinatura_liberada(models.get_assinatura(uid)))
        models.registrar_pagamento(uid, "pay_x", 99.9, "RECEIVED", hoje, None)
        assinatura = models.get_assinatura(uid)
        self.assertTrue(models.assinatura_liberada(assinatura))
        self.assertEqual(assinatura["status"], "ativa")
        self.assertEqual(models.get_pagamentos(uid)[0]["invoice_url"], "https://fatura")  # nao apaga o link
        models.registrar_pagamento(uid, "pay_x", 99.9, "REFUNDED", hoje, None)
        self.assertFalse(models.assinatura_liberada(models.get_assinatura(uid)))

    def test_pagamento_antigo_expira(self):
        uid = models.criar_cliente("Zzz Teste Expira", "segredo1", "a@b.com", "5565999990000", CPF_VALIDO, self.plano_pro)
        hoje = models.datetime.strptime(models._hoje_cuiaba(), "%Y-%m-%d")
        # pagamento vale 1 ano (pedido do Alan, 08/11/2026): 60 dias atras ainda vale...
        models.registrar_pagamento(uid, "pay_recente", 99.9, "CONFIRMED", (hoje - timedelta(days=60)).date().isoformat(), None)
        self.assertTrue(models.assinatura_liberada(models.get_assinatura(uid)))
        models.registrar_pagamento(uid, "pay_recente", 99.9, "REFUNDED", (hoje - timedelta(days=60)).date().isoformat(), None)
        # ...com mais de 1 ano, expirou
        vencimento = (hoje - timedelta(days=370)).date().isoformat()
        models.registrar_pagamento(uid, "pay_velho", 99.9, "CONFIRMED", vencimento, None)
        self.assertEqual(models.get_assinatura(uid)["pago_ate"], models._somar_meses(vencimento, 12).isoformat())
        self.assertFalse(models.assinatura_liberada(models.get_assinatura(uid)))
        models.atualizar_assinatura(uid, cortesia_ate="2099-01-01")
        self.assertTrue(models.assinatura_liberada(models.get_assinatura(uid)))

    def test_cliente_fora_da_aba_usuarios(self):
        models.criar_cliente("Zzz Teste Oculto", "segredo1", "a@b.com", "5565999990000", CPF_VALIDO, self.plano_pro)
        self.assertNotIn("Zzz Teste Oculto", [u["username"] for u in models.get_all_users()])

    # --- HTTP -------------------------------------------------------------

    def test_planos_publico_e_versao_loja(self):
        resp = self.client.get("/planos")
        self.assertIn("R$ 99,90", resp.get_data(as_text=True))
        resp = self.client.get("/planos", headers={"User-Agent": UA_APP})
        html = resp.get_data(as_text=True)
        self.assertNotIn("R$", html)
        self.assertNotIn("/cadastro", html)
        resp = self.client.get("/login", headers={"User-Agent": UA_APP})
        self.assertNotIn("Conheça os planos", resp.get_data(as_text=True))

    def test_cadastro_sem_asaas_fica_aguardando(self):
        self._cadastrar("Zzz Teste Cadastro")
        user = models.get_user_by_username("Zzz Teste Cadastro")
        self.assertEqual(user["is_cliente"], 1)
        self.assertEqual(user["is_admin"], 0)
        # sem pagamento: usa o Free nos 7 dias do cadastro (pedido do Alan, 08/11/2026) --
        # /app abre, as abas do site (que o Free nao tem) nao
        self.assertEqual(self.client.get("/app").status_code, 200)
        self.assertIn("você usa o plano <strong>Free</strong>", self.client.get("/app/assinatura").get_data(as_text=True))
        self.assertIn("/app", self.client.get("/mapa").headers["Location"])
        self.assertIn("/app", self.client.get("/recommendations").headers["Location"])
        self.assertEqual(self.client.get("/admin/planos").status_code, 302)
        # passou dos 7 dias sem pagamento: bloqueado
        conn = models.get_db()
        conn.execute("UPDATE assinaturas SET criado_em = '2026-01-01 08:00:00' WHERE user_id = ?", (user["id"],))
        conn.commit()
        conn.close()
        self.assertIn("/app/assinatura", self.client.get("/app/regiao").headers["Location"])

    def test_cadastro_direto_virou_solicitacao(self):
        resp = self.client.post("/cadastro", data={"username": "Zzz Teste Direto", "plano_id": str(self.plano_pro)})
        self.assertIn("/solicitar-acesso", resp.headers["Location"])
        self.assertIsNone(models.get_user_by_username("Zzz Teste Direto"))

    def test_cadastro_com_asaas_redireciona_para_fatura(self):
        with mock.patch.dict(os.environ, {"ASAAS_API_KEY": "chave"}), \
                mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_1") as criar_cliente, \
                mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_1") as criar_assinatura, \
                mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[{
                    "id": "pay_1", "value": 99.9, "status": "PENDING",
                    "dueDate": models._hoje_cuiaba(), "invoiceUrl": "https://asaas/fatura/1",
                }]):
            self._cadastrar("Zzz Teste Asaas")
            resp = self.client.post("/app/assinatura/assinar", data={"plano_id": str(self.plano_pro)})
        self.assertEqual(resp.headers["Location"], "https://asaas/fatura/1")
        self.assertEqual(criar_cliente.call_args[0][1], CPF_VALIDO)
        self.assertEqual(criar_assinatura.call_args[0][1:3], (8.33, "MONTHLY"))  # 99,90 / 12 parcelas
        user = models.get_user_by_username("Zzz Teste Asaas")
        self.assertEqual(models.get_assinatura(user["id"])["asaas_subscription_id"], "sub_1")

        # webhook: token errado recusa, certo libera
        evento = {"event": "PAYMENT_RECEIVED", "payment": {
            "id": "pay_1", "subscription": "sub_1", "value": 99.9, "status": "RECEIVED",
            "dueDate": models._hoje_cuiaba(),
        }}
        anonimo = webapp.app.test_client()
        self.assertEqual(anonimo.post("/webhooks/asaas", json=evento).status_code, 401)
        self.assertEqual(anonimo.post("/webhooks/asaas", json=evento, headers={"asaas-access-token": "x"}).status_code, 401)
        resp = anonimo.post("/webhooks/asaas", json=evento, headers={"asaas-access-token": "tok-teste"})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(models.assinatura_liberada(models.get_assinatura(user["id"])))
        self.assertEqual(self.client.get("/app").status_code, 200)

        # cancelamento: chama o Asaas e mantem acesso ate' o fim do periodo pago
        with mock.patch.object(webapp.asaas, "cancelar_assinatura") as cancelar:
            self.client.post("/app/assinatura/cancelar")
        cancelar.assert_called_once_with("sub_1")
        assinatura = models.get_assinatura(user["id"])
        self.assertEqual(assinatura["status"], "cancelada")
        self.assertTrue(models.assinatura_liberada(assinatura))

    def test_lavoura_e_dados_regionais_sem_nome_de_fazenda(self):
        self._cadastrar("Zzz Teste Lavoura")
        self._liberar("Zzz Teste Lavoura")
        site, (lat, lon) = next(iter(data_reader.read_site_coordinates().items()))
        resp = self.client.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao Zzz", "lat": lat + 0.05, "lon": lon, "cultura": ""})
        self.assertEqual(resp.status_code, 302)
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("Talhao Zzz", html)
        self.assertIn("Risco de infecção", html)
        self.assertIn("24.0°C", html)
        for nome_real in data_reader.read_sites():
            self.assertNotIn(nome_real, html, f"nome de fazenda real vazou: {nome_real}")
        self.assertEqual(self.client.get("/app").status_code, 200)
        # sem limite de lavouras (pedido do Alan, 07/11/2026): o plano libera acessos ao site
        for i in range(4):
            self.client.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": f"Extra {i}", "lat": lat, "lon": lon})
        user = models.get_user_by_username("Zzz Teste Lavoura")
        self.assertEqual(len(models.get_cliente_pontos(user["id"])), 5)
        home = self.client.get("/app").get_data(as_text=True)
        self.assertIn("+ Adicionar fazenda monitorada", home)
        self.assertNotIn("lavouras.</p>", home)

    def test_plano_basico_bloqueia_recursos(self):
        self._cadastrar("Zzz Teste Basico", plano_id=self.plano_basico)
        self._liberar("Zzz Teste Basico")
        resp = self.client.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao B", "lat": -15.6, "lon": -56.1})
        html = self.client.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("não faz parte do seu plano", html)
        self.assertIn("Temperatura", html)

    def test_lavoura_de_outro_cliente_nao_abre(self):
        outro = models.criar_cliente("Zzz Teste Outro", "segredo1", "a@b.com", "5565999990000", CPF_VALIDO, self.plano_pro)
        ponto = models.criar_cliente_ponto(outro, "Alheio", -15.6, -56.1)
        self._cadastrar("Zzz Teste Curioso")
        self._liberar("Zzz Teste Curioso")
        self.assertEqual(self.client.get(f"/app/pontos/{ponto}").status_code, 404)

    def test_conta_junta_culturas_e_sem_excluir(self):
        self._cadastrar("Zzz Teste Conta")
        html = self.client.get("/app").get_data(as_text=True)
        self.assertIn("Minha conta", html)
        self.assertIn("Trocar senha", html)
        self.assertIn("Fazendas monitoradas", html)
        self.assertNotIn("Excluir minha conta", html)
        self.assertNotIn(">Culturas</a>", html)
        self.assertIn('class="ativa"><span class="ic">👤</span>Conta', html)
        self.assertEqual(self.client.get("/app/conta").status_code, 302)
        self.client.post("/app/conta/excluir", data={"senha": "segredo1"})
        self.assertIsNotNone(models.get_user_by_username("Zzz Teste Conta"))

    def test_graficos_favorabilidade_e_filtro_de_cultura(self):
        """Aba Graficos (ex-Regiao, pedido do Alan, 08/11/2026): tabela de
        favorabilidade de todas as doencas cadastradas + filtro por cultura;
        graficos 7 + 5 dias so' com "graficos_app" (Premium)."""
        self._cadastrar("Zzz Teste Graficos", plano_id=self.plano_basico)
        self._liberar("Zzz Teste Graficos")
        self.assertIn("Adicionar fazenda monitorada", self.client.get("/app/regiao").get_data(as_text=True))
        self.client.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao G", "lat": -12.5, "lon": -55.7})
        doencas = {"Rust Zzz": {"nome_pt": "Ferrugem Zzz", "nome_cientifico": "Phakopsora zzz", "germ_temp_min": 15,
                                "germ_temp_max": 28, "germ_umidade_min": 80, "germ_horas_molhamento": 6},
                   "Mold Zzz": {"nome_pt": "Mofo Zzz", "nome_cientifico": "Sclerotinia zzz", "germ_temp_min": 10,
                                "germ_temp_max": 25, "germ_umidade_min": 85, "germ_horas_molhamento": 10},
                   "Gray Zzz": {"nome_pt": "Mofo Cinzento Zzz", "nome_cientifico": "Botrytis zzz", "germ_temp_min": 10,
                                "germ_temp_max": 25, "germ_umidade_min": 85, "germ_horas_molhamento": 10}}
        with mock.patch.object(webapp, "_disease_info_cached", return_value=doencas), \
                mock.patch.object(models, "get_doenca_culturas",
                                  return_value={"Rust Zzz": {"Soja"}, "Mold Zzz": {"Milho", "Soja"}, "Gray Zzz": {"Uva"}}), \
                mock.patch.object(webapp, "_weather_ponto_cliente", return_value=None), \
                mock.patch.object(webapp, "_calc_risco_germinacao", return_value="amarelo"), \
                mock.patch.object(webapp, "_calc_previsao_risco_germinacao",
                                  return_value=[{"data_fmt": "09/11", "risco": "vermelho"}]):
            todas = self.client.get("/app/regiao").get_data(as_text=True)
            soja = self.client.get("/app/regiao?cultura=Soja").get_data(as_text=True)
            uva = self.client.get("/app/regiao?cultura=Uva").get_data(as_text=True)
        self.assertIn("Favorabilidade climática", todas)
        self.assertIn('id="filtroCultura"', todas)
        self.assertIn("Ferrugem Zzz", todas)
        self.assertIn("Mofo Zzz", todas)
        # filtro previo: so' doencas das culturas do cliente (Soja) -- pedido do Alan, 08/11/2026
        self.assertNotIn("Mofo Cinzento Zzz", todas)
        self.assertIn("Ferrugem Zzz", soja)
        self.assertNotIn("Mofo Cinzento Zzz", uva)  # cultura que o cliente nao marcou nao vale como filtro
        self.assertNotIn('<option value="Uva"', todas)
        self.assertIn('risco-vermelho">A<', todas)
        # Basico: sem graficos 7 + 5 dias, e o JSON do site fica fechado
        self.assertIn("fazem parte dos planos Premium", todas)
        self.assertEqual(self.client.get("/graficos/dados").status_code, 302)

    def test_graficos_premium_7_dias_e_previsao(self):
        premium = next(p for p in models.get_planos() if p["nome"] == "Premium")
        self.assertIn("graficos_app", premium["recursos"])
        models.salvar_plano(None, "Zzz Graficos", "", 199.9, "MONTHLY", 2, 300, ["clima", "graficos_app"], True, 9)
        plano = next(p for p in models.get_planos() if p["nome"] == "Zzz Graficos")
        self._cadastrar("Zzz Teste Graf Premium", plano_id=plano["id"])
        self._liberar("Zzz Teste Graf Premium")
        self.client.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao P", "lat": -12.5, "lon": -55.7})
        with mock.patch.object(webapp, "_weather_ponto_cliente", return_value=None):
            html = self.client.get("/app/regiao").get_data(as_text=True)
        hoje = webapp._hoje_cuiaba()
        self.assertIn("Esporos e favorabilidade", html)
        self.assertIn(f"inicio={(hoje - webapp.timedelta(days=6)).isoformat()}&fim={hoje.isoformat()}", html)
        self.assertEqual(self.client.get("/graficos/dados?inicio=2026-01-01&fim=2026-01-02").status_code, 200)

    def test_caixas_alto_medio_baixo_sem_esporos_numero(self):
        """So' o Premium + Estacao (recurso "esporos_numero") ve o numero de
        esporos; os outros planos veem Alto/Medio/Baixo (pedido do Alan, 08/11/2026)."""
        estacao = next(p for p in models.get_planos() if p["nome"] == "Premium + Estação")
        self.assertIn("esporos_numero", estacao["recursos"])
        self.assertNotIn("esporos_numero", next(p for p in models.get_planos() if p["nome"] == "Premium")["recursos"])
        cards = [{"doenca": "Ferrugem Zzz", "doenca_en": "Rust Zzz", "concentracao": 123.4, "status": "Perigo",
                  "data": webapp._hoje_cuiaba().isoformat()},
                 {"doenca": "Mancha Zzz", "doenca_en": "Spot Zzz", "concentracao": 7.5, "status": "Normal",
                  "data": webapp._hoje_cuiaba().isoformat()}]
        for nome, recursos, ve_numero in (("Zzz Nivel", ["clima", "esporos"], False),
                                          ("Zzz Numero", ["clima", "esporos", "esporos_numero"], True)):
            models.salvar_plano(None, nome, "", 99.0, "MONTHLY", 2, 50, recursos, True, 9)
            plano = next(p for p in models.get_planos() if p["nome"] == nome)
            self._cadastrar(f"Zzz Teste {nome}", plano_id=plano["id"])
            self._liberar(f"Zzz Teste {nome}")
            self.client.post("/app/pontos/novo", data={**NOVA_FAZENDA, "nome": "Talhao N", "lat": -12.5, "lon": -55.7})
            ponto = models.get_cliente_pontos(models.get_user_by_username(f"Zzz Teste {nome}")["id"])[0]
            with mock.patch.object(webapp, "_cards_ponto_cliente", return_value=(cards, 2)), \
                    mock.patch.object(webapp, "_weather_ponto_cliente", return_value=None):
                html = self.client.get(f"/app/pontos/{ponto['id']}").get_data(as_text=True)
            self.assertEqual("123.4" in html, ve_numero)
            self.assertEqual('fw-bold">Alto</div>' in html, not ve_numero)
            self.assertEqual('fw-bold">Baixo</div>' in html, not ve_numero)

    def test_master_gerencia_planos_e_cortesia(self):
        models.create_user(models.MASTER_PRINCIPAL, "segredo1")
        self.client.post("/login", data={"username": models.MASTER_PRINCIPAL, "password": "segredo1"})
        self.assertEqual(self.client.get("/admin/planos").status_code, 200)
        self.client.post("/admin/planos/salvar", data={
            "nome__novo": "Zzz Anual", "valor__novo": "999,00", "ciclo__novo": "YEARLY", "max_pontos__novo": "5",
            "raio_km__novo": "100", "ordem__novo": "3", "ativo__novo": "on", "recursos__novo": ["clima", "risco", "inventado"],
        })
        plano = next(p for p in models.get_planos() if p["nome"] == "Zzz Anual")
        self.assertEqual((plano["valor"], plano["recursos"]), (999.0, ["clima", "risco"]))
        uid = models.criar_cliente("Zzz Teste Cortesia", "segredo1", "a@b.com", "5565999990000", CPF_VALIDO, plano["id"])
        self.assertFalse(models.excluir_plano(plano["id"]))  # tem assinante
        self.client.post(f"/admin/clientes/{uid}/cortesia", data={"cortesia_ate": "2099-12-31", "plano_id": str(self.plano_pro)})
        assinatura = models.get_assinatura(uid)
        self.assertEqual((assinatura["cortesia_ate"], assinatura["plano_id"]), ("2099-12-31", self.plano_pro))
        self.assertIn("Zzz Teste Cortesia", self.client.get("/admin/planos").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
