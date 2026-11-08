"""Cadastro publico (/solicitar-acesso): conta criada na hora, tela
"Gerenciar Clientes" do Master e CPF/CNPJ so' na hora de assinar.

Rodar a partir de webapp/:  python -m unittest tests.test_solicitacoes
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


def _form(**extra):
    dados = {
        "modalidade": "fazenda", "perfil": ["produtor"], "nome_completo": "Zzz Teste da Silva Cadastro",
        "username": "Zzz Cadastro", "codigo_pais": "55", "numero_telefone": "65999990001", "email": "",
        "empresa": "Fazenda Zzz", "cidade": "Sorriso", "uf": "MT", "grupo_cultura": "hf", "cultura_outra": "Soja", "area_outra": "1.500",
        "mensagem": "Quero acompanhar a ferrugem", "senha": "segredo1", "senha_confirmar": "segredo1", "aceite": "on",
    }
    dados.update(extra)
    return dados


class CadastroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Cad"):
            models.create_user("Zzz Teste Master Cad", "x", is_master=True, telefone="5565988887777")
        if not models.get_user_by_username("Zzz Teste Admin Cad"):
            models.create_user("Zzz Teste Admin Cad", "x", is_admin=True)
        cls.plano = models.salvar_plano(None, "Zzz Plano Cad", "", 30.0, "MONTHLY", 2, 50, ["clima"], True, 1)
        cls.enviadas = []
        cls.patch_whats = mock.patch.object(
            webapp, "_whatsapp_em_segundo_plano", side_effect=lambda tel, texto: cls.enviadas.append((tel, texto)),
        )
        cls.patch_whats.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch_whats.stop()

    def _logado(self, nome):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username(nome)["id"])
            s["_fresh"] = True
        return c

    def _cadastro(self, username):
        return next((c for c in models.get_cadastros() if c["username"] == username), None)

    def test_links_e_formulario(self):
        c = webapp.app.test_client()
        html = c.get("/solicitar-acesso").get_data(as_text=True)
        for trecho in ("Produtor", "Técnico do setor", 'name="nome_completo"'):
            self.assertIn(trecho, html)
        self.assertNotIn('name="cpf_cnpj"', html)
        self.assertIn("Cadastre-se", c.get("/login").get_data(as_text=True))
        self.assertIn("modalidade=plano", c.get("/cadastro?plano=3").headers["Location"])
        self.assertIn("solicitar-acesso?modalidade=plano", c.get("/planos").get_data(as_text=True))

    def test_sugestao_e_duplicidade_de_usuario(self):
        self.assertEqual(webapp._sugerir_usuario("joão da silva pereira"), "João Pereira")
        models.create_user("Zzz Repetido Teste", "x")
        self.assertEqual(webapp._sugerir_usuario("Zzz Fulano Repetido Teste"), "Zzz Teste")
        self.assertEqual(webapp._sugerir_usuario("Zzz Repetido Teste"), "Zzz Teste")
        models.create_user("Zzz Teste", "x")
        self.assertEqual(webapp._sugerir_usuario("Zzz Fulano Teste"), "Zzz Teste 2")
        r = webapp.app.test_client().get("/solicitar-acesso/usuario?usuario=zzz+teste&nome=Zzz+Fulano+Teste").get_json()
        self.assertEqual(r, {"disponivel": False, "sugestao": "Zzz Teste 2"})
        r = webapp.app.test_client().get("/solicitar-acesso/usuario?usuario=Zzz+Livre+123").get_json()
        self.assertTrue(r["disponivel"])

    def test_validacoes(self):
        casos = {
            "sem perfil": dict(perfil=[]),
            "so um nome": dict(nome_completo="Zzz"),
            "usuario repetido": dict(username="zzz teste master cad"),
            "sem fazenda": dict(empresa=""),
            "sem UF": dict(uf=""),
            "sem cultura": dict(cultura_outra=""),
            "sem area": dict(area_outra=""),
            "cultura marcada sem area": dict(culturas=["Milho"]),
            "senha curta": dict(senha="123", senha_confirmar="123"),
            "senha diferente": dict(senha_confirmar="outra1"),
        }
        for caso, extra in casos.items():
            with self.subTest(caso=caso):
                extra.setdefault("username", f"Zzz Val {caso}")
                resp = webapp.app.test_client().post("/solicitar-acesso", data=_form(**extra))
                self.assertEqual(resp.status_code, 200)
                self.assertFalse(models.username_em_uso(extra["username"]) and caso != "usuario repetido")
        html = webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Teste Master Cad")).get_data(as_text=True)
        self.assertIn("já existe", html)
        # mensagem e' opcional
        resp = webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Sem Mensagem", mensagem="",
                                                                            numero_telefone="65999990008"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(models.username_em_uso("Zzz Sem Mensagem"))

    def test_sem_pergunta_e_area_por_cultura(self):
        """Sem "O que voce quer?" (cadastro = assinar plano), grupo de cultura
        antes das culturas e area de cada uma (pedidos do Alan, 08/11/2026)."""
        html = webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True)
        self.assertNotIn("O que você quer?", html)
        self.assertIn('<input type="hidden" name="modalidade" value="plano">', html)
        self.assertIn('name="area_cultura__Milho"', html)
        for grupo in ('value="cereais"', 'value="hf"', 'value="cana"'):
            self.assertIn(grupo, html)
        self.assertIn('data-grupo="cereais"', html)
        self.assertIn("Escolha o seu plano", html)  # planos ja' no cadastro
        self.assertIn('value="fazenda"', webapp.app.test_client().get("/solicitar-acesso?modalidade=fazenda").get_data(as_text=True))
        # Cereais: preco pela area de Soja; cultura de outro grupo nao entra
        dados = _form(username="Zzz Cad Areas", numero_telefone="65999990031", grupo_cultura="cereais",
                      culturas=["Soja", "Milho", "Batata"], cultura_outra="Sorgo", area_outra="50")
        dados.pop("modalidade")
        dados.update({"area_cultura__Soja": "1.000", "area_cultura__Milho": "300", "area_cultura__Batata": "70"})
        resp = webapp.app.test_client().post("/solicitar-acesso", data=dados)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(models.get_user_by_username("Zzz Cad Areas")["is_cliente"], 1)  # sem a pergunta = assinar plano
        reg = self._cadastro("Zzz Cad Areas")
        self.assertEqual((reg["grupo_cultura"], reg["area_ha"], reg["area_preco_ha"]), ("cereais", 1300.0, 1000.0))
        self.assertEqual(reg["culturas"], "Soja (1.000 ha), Milho (300 ha)")
        # HF: soma das culturas, inclusive "Outra cultura"
        dados = _form(username="Zzz Cad HF", numero_telefone="65999990032", grupo_cultura="hf",
                      culturas=["Batata"], cultura_outra="Tomate", area_outra="20,5")
        dados["area_cultura__Batata"] = "30"
        self.assertEqual(webapp.app.test_client().post("/solicitar-acesso", data=dados).status_code, 302)
        reg = self._cadastro("Zzz Cad HF")
        self.assertEqual((reg["area_ha"], reg["area_preco_ha"], reg["culturas"]), (50.5, 50.5, "Batata (30 ha), Tomate (20,5 ha)"))
        # sem grupo = recusa
        sem = _form(username="Zzz Cad Sem Grupo", numero_telefone="65999990033", grupo_cultura="")
        self.assertEqual(webapp.app.test_client().post("/solicitar-acesso", data=sem).status_code, 200)
        # Gerenciar Clientes: uma linha por cultura com a area dela + tipo
        html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        for linha in ("Soja <span class=\"text-muted\">— 1.000 ha</span>", "Milho <span class=\"text-muted\">— 300 ha</span>"):
            self.assertIn(f'<div class="cultura-area-linha">{linha}</div>', html)
        self.assertIn("Cereais <span class=\"text-muted\">-- área para o preço: 1.000 ha</span>", html)
        self.assertEqual(webapp._culturas_com_area("Soja, Milho (2.000 ha)"), [("Soja", None), ("Milho", "2.000 ha")])

    def test_tecnico_nao_escolhe_plano_e_ve_contato(self):
        """Tecnico do setor: sem planos no cadastro, so' o contato (pedido do Alan, 08/11/2026)."""
        html = webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True)
        self.assertIn('id="contato-tecnico"', html)
        for trecho in ("Alan Mauro", "(43) 99114-7595", "wa.me/5543991147595", "mailto:contato@oneagro.com.br"):
            self.assertIn(trecho, html)
        self.assertNotIn("área para o preço", html)
        self.assertNotIn("o preço usa a área de Soja", html)
        plano = models.get_planos(apenas_ativos=True)[0]
        c = webapp.app.test_client()
        resp = c.post("/solicitar-acesso", data=_form(username="Zzz Cad Tecnico", numero_telefone="65999990061",
                                                      modalidade="plano", perfil=["tecnico"], plano_id=str(plano["id"])),
                      follow_redirects=True)
        self.assertIn("entre em contato com Alan Mauro", resp.get_data(as_text=True))
        user = models.get_user_by_username("Zzz Cad Tecnico")
        self.assertIsNone(models.get_assinatura(user["id"])["plano_id"])  # nenhum plano escolhido

    def test_grupo_da_cultura(self):
        self.assertEqual([webapp._grupo_da_cultura(c) for c in ("Soja", "Milho", "Algodão", "Algodao", "Cana", "Cana-de-açúcar", "Feijao", "Batata")],
                         ["cereais", "cereais", "cereais", "cereais", "cana", "cana", "hf", "hf"])
        self.assertEqual(webapp._area_para_preco("cereais", [("Soja", 800), ("Milho", 200)]), 800)
        self.assertEqual(webapp._area_para_preco("cereais", [("Milho", 200), ("Algodao", 100)]), 300)  # sem Soja: soma
        self.assertEqual(webapp._area_para_preco("hf", [("Batata", 10), ("Uva", 5)]), 15)
        self.assertEqual(webapp._area_para_preco("cana", [("Cana", 900)]), 900)

    def test_plano_premium_pelo_preco_da_area(self):
        """Premium: total = area x U$/ha da faixa; parcelas pelo ciclo; sem valor na faixa = sob consulta."""
        import json
        import cotacao
        premium = next(p for p in models.get_planos() if p["nome"] == "Premium")
        self.assertTrue(premium["preco_por_area"])  # ja' nasce ligado no Premium
        models.set_setting("tabela_precificacao", json.dumps([{"ate_ha": 1000, "usd_ha": 1.2}]))
        try:
            with mock.patch.object(cotacao, "dolar", return_value={"valor": 5.0, "data": "2026-11-06"}):
                p = webapp._plano_para_area(premium, 800)
                self.assertEqual((p["valor"], p["moeda"], p["parcelas"], p["valor_parcela"], p["sob_consulta"]),
                                 (960.0, "USD", 12, 80.0, False))
                self.assertTrue(webapp._plano_para_area(premium, 5000)["sob_consulta"])  # faixa ate' 5.000 sem U$/ha
                self.assertTrue(webapp._plano_para_area(premium, None)["sob_consulta"])
                # cliente cereais com 800 ha de soja ve o Premium com o preco dele em Minha assinatura
                models.salvar_plano(premium["id"], "Premium", "", 0, "MONTHLY", 1, 50, premium["recursos"], True, 3,
                                    preco_por_area=True)
                c = webapp.app.test_client()
                dados = _form(username="Zzz Cad Premium", numero_telefone="65999990034", grupo_cultura="cereais",
                              culturas=["Soja", "Milho"], modalidade="plano", plano_id=str(premium["id"]))
                dados.update({"area_cultura__Soja": "800", "area_cultura__Milho": "400"})
                resp = c.post("/solicitar-acesso", data=dados)
                self.assertIn(f"/app/assinatura?plano={premium['id']}", resp.headers["Location"])
                tela = c.get(resp.headers["Location"]).get_data(as_text=True)
                self.assertIn('<span class="plano-parcela">12x de U$ 80,00 (≈ R$ 400,00)</span>', tela)
                self.assertIn("Preço pela sua área: 800 ha (até 1.000 ha).", tela)
                import re
                self.assertRegex(tela, rf'value="{premium["id"]}"\s+data-gratis="0"\s+checked')  # ja' vem marcado
                with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                        mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_p"), \
                        mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_p") as criar, \
                        mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[]):
                    c.post("/app/assinatura/assinar", data={"plano_id": str(premium["id"]), "cpf_cnpj": CPF_VALIDO})
                self.assertEqual(criar.call_args[0][1], 400.0)  # parcela U$ 80 x 5,00
        finally:
            models.set_setting("tabela_precificacao", "")
            models.salvar_plano(premium["id"], premium["nome"], premium["descricao"], premium["valor"], premium["ciclo"],
                                premium["max_pontos"], premium["raio_km"], premium["recursos"], premium["ativo"],
                                premium["ordem"], premium["moeda"], premium["preco_por_area"])

    def test_acesso_a_fazenda_cria_usuario_sem_fazendas(self):
        self.enviadas.clear()
        c = webapp.app.test_client()
        resp = c.post("/solicitar-acesso", data=_form(username="Zzz Cad Fazenda", perfil=["produtor", "tecnico"]))
        self.assertEqual(resp.status_code, 302)
        user = models.get_user_by_username("Zzz Cad Fazenda")
        self.assertEqual((user["is_admin"], user["is_cliente"], user["telefone"]), (0, 0, "5565999990001"))
        self.assertFalse(models.get_user_permitted_site_ids(user["id"]))
        self.assertEqual(c.get("/fazendas").status_code, 200)  # ja' entrou logado
        reg = self._cadastro("Zzz Cad Fazenda")
        self.assertEqual((reg["tipo"], reg["perfil"], reg["area_ha"], reg["nome_completo"]),
                         ("usuario", "produtor,tecnico", 1500.0, "Zzz Teste da Silva Cadastro"))
        aviso = [t for tel, t in self.enviadas if tel == "5565988887777"]
        self.assertEqual(len(aviso), 1)
        self.assertIn("Zzz Cad Fazenda", aviso[0])
        login = webapp.app.test_client().post("/login", data={"username": "Zzz Cad Fazenda", "password": "segredo1"})
        self.assertEqual(login.status_code, 302)

    def test_assinar_plano_cria_cliente_e_pede_cpf_na_assinatura(self):
        c = webapp.app.test_client()
        resp = c.post("/solicitar-acesso", data=_form(username="Zzz Cad Plano", modalidade="plano", numero_telefone="65999990002"))
        self.assertIn("/app/assinatura", resp.headers["Location"])
        user = models.get_user_by_username("Zzz Cad Plano")
        self.assertEqual(user["is_cliente"], 1)
        assinatura = models.get_assinatura(user["id"])
        self.assertEqual((assinatura["cpf_cnpj"], assinatura["plano_id"]), ("", None))
        self.assertIn('name="cpf_cnpj"', c.get("/app/assinatura").get_data(as_text=True))

        sem_cpf = c.post("/app/assinatura/assinar", data={"plano_id": str(self.plano)}, follow_redirects=True)
        self.assertIn("CPF ou CNPJ válido", sem_cpf.get_data(as_text=True))
        c.post("/app/assinatura/assinar", data={"plano_id": str(self.plano), "cpf_cnpj": "529.982.247-25"})
        assinatura = models.get_assinatura(user["id"])
        self.assertEqual((assinatura["cpf_cnpj"], assinatura["plano_id"]), (CPF_VALIDO, self.plano))
        self.assertNotIn('name="cpf_cnpj"', c.get("/app/assinatura").get_data(as_text=True))

    def test_tela_novos_cadastros_do_master(self):
        webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Cad Tela", numero_telefone="65999990003",
                                                                     email="tela@t.com"))
        self.assertEqual(self._logado("Zzz Teste Admin Cad").get("/admin/solicitacoes").status_code, 403)
        master = self._logado("Zzz Teste Master Cad")
        self.assertIn("badge bg-danger", master.get("/mapa").get_data(as_text=True))
        html = master.get("/admin/solicitacoes").get_data(as_text=True)
        for trecho in ("Zzz Cad Tela", "Novo", "wa.me/5565999990003", "mailto:tela@t.com", "Liberar fazendas", "Fazenda Zzz"):
            self.assertIn(trecho, html)
        self.assertEqual(models.contar_cadastros_nao_vistos(), 0)  # abrir a tela marca como visto
        self.assertNotIn(">Novo<", master.get("/admin/solicitacoes").get_data(as_text=True))

    def test_clientes_e_cadastros_numa_aba_so(self):
        """Novos cadastros + Clientes do app viraram "Gerenciar Clientes";
        Planos so' edita os planos (pedido do Alan, 01/11/2026)."""
        webapp.app.test_client().post("/solicitar-acesso", data=_form(
            username="Zzz Cad Cliente Junto", modalidade="plano", numero_telefone="65999990013"))
        antigo = models.criar_cliente("Zzz Cliente Antigo", "segredo1", "", "5565999990014", "", self.plano)
        master = self._logado("Zzz Teste Master Cad")
        html = master.get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("<h4 class=\"mb-2\">Gerenciar Clientes</h4>", html)
        self.assertEqual(html.count("<dd>Zzz Cad Cliente Junto</dd>"), 1)  # uma vez so', com a assinatura junto
        self.assertIn("Zzz Cliente Antigo", html)  # cliente sem registro de cadastro tambem aparece
        self.assertIn("Zzz Plano Cad", html)  # plano do cliente antigo
        self.assertIn("<strong>12x de R$ 2,50</strong>", html)  # plano mensal de R$ 30,00 no total
        self.assertIn("(total R$ 30,00)", html)
        self.assertIn("Cortesia até", html)
        self.assertIn('data-filtro="cliente"', html)
        self.assertIn('class="btn btn-link text-decoration-none p-0 fw-bold cad-toggle"', html)  # recolhido
        self.assertIn('class="d-none mt-3" id="cad-body-0"', html)
        self.assertNotIn("Todas as contas criadas", html)  # texto do topo saiu
        self.assertIn(">Gerenciar Clientes", html)  # menu
        self.assertIn(">Planos e Valores</a>", html)
        planos = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("<h4 class=\"mb-2\">Planos e Valores</h4>", planos)
        self.assertNotIn("Zzz Cliente Antigo", planos)
        self.assertNotIn("Cortesia até", planos)
        # acoes de cliente voltam pra Gerenciar Clientes
        resp = master.post(f"/admin/clientes/{antigo}/cortesia", data={"cortesia_ate": "2030-01-01", "plano_id": ""})
        self.assertTrue(resp.headers["Location"].endswith("/admin/solicitacoes"))

    def test_preco_pela_area_no_cartao_do_cliente(self):
        """Area do cadastro busca a faixa da tabela de apoio (1.500 ha -> ate' 2.000)."""
        import json
        import cotacao
        webapp.app.test_client().post("/solicitar-acesso", data=_form(
            username="Zzz Cad Preco Area", modalidade="plano", numero_telefone="65999990021"))
        models.set_setting("tabela_precificacao", json.dumps([{"ate_ha": 2000, "usd_ha": 0.4}]))
        try:
            with mock.patch.object(cotacao, "dolar", return_value={"valor": 5.0, "data": "2026-11-06"}):
                html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        finally:
            models.set_setting("tabela_precificacao", "")
        self.assertIn("<dt>Preço pela área</dt>", html)
        self.assertIn("<strong>U$ 600,00</strong> (≈ R$ 3.000,00)", html)
        self.assertIn("até 2.000 ha, U$ 0,40/ha", html)

    # Mesma pessoa nao faz 2o cadastro; conta excluida some de Novos
    # cadastros (pedidos do Alan, 01/11/2026).
    def test_segundo_cadastro_barrado(self):
        c = webapp.app.test_client()
        c.post("/solicitar-acesso", data=_form(username="Zzz Cad Dup", numero_telefone="65999990009", email="dup@t.com"))
        self.assertIsNotNone(models.get_user_by_username("Zzz Cad Dup"))
        for extra, aviso in ((dict(numero_telefone="65999990009"), "Este celular já está cadastrado"),
                             # sem o 55 na frente / outro usuario, mesmo celular
                             (dict(codigo_pais="", numero_telefone="5565999990009"), "Este celular já está cadastrado"),
                             (dict(numero_telefone="65999990010", email="DUP@t.com"), "Este e-mail já está cadastrado")):
            with self.subTest(extra=extra):
                resp = webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Cad Dup Dois", **extra))
                self.assertEqual(resp.status_code, 200)
                self.assertIn(aviso, resp.get_data(as_text=True))
                self.assertIsNone(models.get_user_by_username("Zzz Cad Dup Dois"))
        self.assertEqual(sum(1 for c in models.get_cadastros() if c["username"].startswith("Zzz Cad Dup")), 1)

    def test_conta_excluida_sai_de_novos_cadastros(self):
        webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Cad Excluir", numero_telefone="65999990011"))
        user = models.get_user_by_username("Zzz Cad Excluir")
        self.assertIsNotNone(self._cadastro("Zzz Cad Excluir"))
        models.delete_user(user["id"])
        self.assertIsNone(self._cadastro("Zzz Cad Excluir"))
        # registro que ficou de antes da regra (conta apagada por outro caminho)
        models.registrar_cadastro({"username": "Zzz Cad Orfao", "nome_completo": "Zzz Orfao", "perfil": "produtor",
                                   "telefone": "5565999990012", "tipo": "usuario", "user_id": 999999})
        html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        self.assertNotIn("Zzz Cad Orfao", html)
        self.assertNotIn("conta excluída", html)


class TravaWhatsappQapTest(unittest.TestCase):
    def test_qap_so_envia_para_numeros_liberados(self):
        import whatsapp
        with mock.patch.object(whatsapp, "AMBIENTE_QAP", True), \
                mock.patch.object(whatsapp, "_send_whatsapp_bridge", return_value=(True, "ok")) as bridge, \
                mock.patch.object(whatsapp, "INTERVALO_ENTRE_ENVIOS_S", 0):
            with mock.patch.dict(os.environ, {"QAP_WHATSAPP_PERMITIDOS": ""}):
                ok, msg = whatsapp.send_whatsapp("5565999990000", "oi")
            self.assertFalse(ok)
            self.assertIn("QAP", msg)
            with mock.patch.dict(os.environ, {"QAP_WHATSAPP_PERMITIDOS": "+55 65 99999-0000, 5511900000000"}):
                self.assertEqual(whatsapp.send_whatsapp("5565999990000", "oi"), (True, "ok"))
                self.assertFalse(whatsapp.send_whatsapp_document("5565911112222", b"%PDF", "x.pdf")[0])
            self.assertEqual(bridge.call_count, 1)


if __name__ == "__main__":
    unittest.main()
