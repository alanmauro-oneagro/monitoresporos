"""Cadastro publico (/solicitar-acesso): conta criada na hora, tela
"Clientes e cadastros" do Master e CPF/CNPJ so' na hora de assinar.

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
        "empresa": "Fazenda Zzz", "cidade": "Sorriso", "uf": "MT", "cultura_outra": "Soja", "area_ha": "1.500",
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
        for trecho in ("Assinar plano", "Ter acesso à fazenda cadastrada", "Produtor", "Técnico do setor", 'name="nome_completo"'):
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
            "sem modalidade": dict(modalidade=""),
            "sem perfil": dict(perfil=[]),
            "so um nome": dict(nome_completo="Zzz"),
            "usuario repetido": dict(username="zzz teste master cad"),
            "sem fazenda": dict(empresa=""),
            "sem UF": dict(uf=""),
            "sem cultura": dict(cultura_outra=""),
            "sem area": dict(area_ha=""),
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
        """Novos cadastros + Clientes do app viraram "Clientes e cadastros";
        Planos so' edita os planos (pedido do Alan, 01/11/2026)."""
        webapp.app.test_client().post("/solicitar-acesso", data=_form(
            username="Zzz Cad Cliente Junto", modalidade="plano", numero_telefone="65999990013"))
        antigo = models.criar_cliente("Zzz Cliente Antigo", "segredo1", "", "5565999990014", "", self.plano)
        master = self._logado("Zzz Teste Master Cad")
        html = master.get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("<h4 class=\"mb-2\">Clientes e cadastros</h4>", html)
        self.assertEqual(html.count("<dd>Zzz Cad Cliente Junto</dd>"), 1)  # uma vez so', com a assinatura junto
        self.assertIn("Zzz Cliente Antigo", html)  # cliente sem registro de cadastro tambem aparece
        self.assertIn("Zzz Plano Cad", html)  # plano do cliente antigo
        self.assertIn("Cortesia até", html)
        self.assertIn('data-filtro="cliente"', html)
        self.assertIn('class="btn btn-link text-decoration-none p-0 fw-bold cad-toggle"', html)  # recolhido
        self.assertIn('class="d-none mt-3" id="cad-body-0"', html)
        self.assertNotIn("Todas as contas criadas", html)  # texto do topo saiu
        self.assertIn(">Clientes e cadastros", html)  # menu
        self.assertIn(">Planos e Valores</a>", html)
        planos = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("<h4 class=\"mb-2\">Planos e Valores</h4>", planos)
        self.assertNotIn("Zzz Cliente Antigo", planos)
        self.assertNotIn("Cortesia até", planos)
        # acoes de cliente voltam pra Clientes e cadastros
        resp = master.post(f"/admin/clientes/{antigo}/cortesia", data={"cortesia_ate": "2030-01-01", "plano_id": ""})
        self.assertTrue(resp.headers["Location"].endswith("/admin/solicitacoes"))

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
