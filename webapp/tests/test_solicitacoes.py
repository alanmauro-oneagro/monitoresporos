"""Pre-cadastro publico (/solicitar-acesso) com aprovacao do Master.

Rodar a partir de webapp/:  python -m unittest tests.test_solicitacoes
"""
import os
import re
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
        "tipo": "usuario", "username": "Zzz Teste Solicita", "email": "s@t.com", "codigo_pais": "55",
        "numero_telefone": "65999990001", "cpf_cnpj": CPF_VALIDO, "empresa": "Fazenda Zzz", "cidade": "Sorriso",
        "uf": "MT", "area_ha": "1.500", "mensagem": "Quero acompanhar a ferrugem", "senha": "segredo1",
        "senha_confirmar": "segredo1", "aceite": "on",
    }
    dados.update(extra)
    return dados


class SolicitacaoAcessoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        for nome, master, admin in (("Zzz Teste Master Sol", True, True), ("Zzz Teste Admin Sol", False, True)):
            if not models.get_user_by_username(nome):
                models.create_user(nome, "x", is_admin=admin, is_master=master)
        cls.plano = models.salvar_plano(None, "Zzz Plano Sol", "", 30.0, "MONTHLY", 2, 50, ["clima"], True, 1)
        master = models.get_user_by_username("Zzz Teste Master Sol")
        models.set_user_contato(master["id"], "m@t.com", "5565988887777")
        cls.enviadas = []
        cls.patch_whats = mock.patch.object(
            webapp, "_whatsapp_em_segundo_plano", side_effect=lambda tel, texto: cls.enviadas.append((tel, texto)),
        )
        cls.patch_whats.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch_whats.stop()

    def _codigo_para(self, telefone):
        textos = [t for tel, t in self.enviadas if tel == telefone and "código de confirmação" in t]
        return re.search(r"\*(\d{6})\*", textos[-1]).group(1)

    def _logado(self, nome):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username(nome)["id"])
            s["_fresh"] = True
        return c

    def _enviar(self, **extra):
        return webapp.app.test_client().post("/solicitar-acesso", data=_form(**extra))

    def _enviar_e_confirmar(self, **extra):
        resp = self._enviar(**extra)
        self.assertIn("/solicitar-acesso/confirmar/", resp.headers["Location"])
        telefone = "55" + _form(**extra)["numero_telefone"]
        return webapp.app.test_client().post(resp.headers["Location"], data={"codigo": self._codigo_para(telefone)})

    def _ultima(self, username):
        return models.get_solicitacao_pendente_por_username(username)

    def test_formulario_e_links(self):
        c = webapp.app.test_client()
        html = c.get("/solicitar-acesso").get_data(as_text=True)
        self.assertIn('name="tipo"', html)
        self.assertIn('name="regiao"', html)
        self.assertIn("Solicite seu acesso", c.get("/login").get_data(as_text=True))
        self.assertIn("/solicitar-acesso", c.get("/cadastro?plano=3").headers["Location"])
        self.assertIn("solicitar-acesso?tipo=cliente", c.get("/planos").get_data(as_text=True))

    def test_validacoes(self):
        casos = {
            "cliente sem plano": dict(username="Zzz Val 1", tipo="cliente"),
            "consultor sem regiao": dict(username="Zzz Val 2", consultor="on", regiao=""),
            "sem UF": dict(username="Zzz Val 3", uf=""),
            "CPF invalido": dict(username="Zzz Val 4", cpf_cnpj="11111111111"),
            "senha diferente": dict(username="Zzz Val 5", senha_confirmar="outra1"),
            "sem tipo": dict(username="Zzz Val 6", tipo=""),
        }
        for caso, extra in casos.items():
            with self.subTest(caso=caso):
                resp = self._enviar(**extra)
                self.assertEqual(resp.status_code, 200)
                self.assertIsNone(self._ultima(extra["username"]))

    def test_usuario_aprovado_entra_com_a_senha_dele(self):
        self.enviadas.clear()
        resp = self._enviar_e_confirmar(username="Zzz Sol Usuario", consultor="on", regiao="Sorriso e Sinop (MT)")
        self.assertIn("Solicitação enviada", resp.get_data(as_text=True))
        aviso = [t for tel, t in self.enviadas if tel == "5565988887777"]
        self.assertEqual(len(aviso), 1)
        self.assertIn("Zzz Sol Usuario", aviso[0])
        self.assertIn("Sorriso e Sinop (MT)", aviso[0])
        self.assertIn("/admin/solicitacoes", aviso[0])
        sol = self._ultima("Zzz Sol Usuario")
        self.assertEqual((sol["tipo"], sol["regiao"], sol["area_ha"], sol["consultor"]), ("usuario", "Sorriso e Sinop (MT)", 1500.0, 1))
        self.assertIsNone(models.get_user_by_username("Zzz Sol Usuario"))

        login = webapp.app.test_client().post("/login", data={"username": "Zzz Sol Usuario", "password": "segredo1"})
        self.assertIn("em análise", login.get_data(as_text=True))
        dup = self._enviar(username="zzz sol usuario")
        self.assertIn("Já existe", dup.get_data(as_text=True))

        self.assertEqual(self._logado("Zzz Teste Admin Sol").get("/admin/solicitacoes").status_code, 403)
        master = self._logado("Zzz Teste Master Sol")
        self.assertIn("Zzz Sol Usuario", master.get("/admin/solicitacoes").get_data(as_text=True))
        resp = master.post(f"/admin/solicitacoes/{sol['id']}/aprovar", data={"tipo": "usuario"})
        self.assertIn("/permissions", resp.headers["Location"])

        user = models.get_user_by_username("Zzz Sol Usuario")
        self.assertEqual((user["is_admin"], user["is_cliente"], user["email"]), (0, 0, "s@t.com"))
        self.assertEqual(models.get_solicitacao(sol["id"])["status"], "aprovada")
        login = webapp.app.test_client().post("/login", data={"username": "Zzz Sol Usuario", "password": "segredo1"})
        self.assertEqual(login.status_code, 302)

    def test_master_troca_para_cliente_na_aprovacao(self):
        self._enviar_e_confirmar(username="Zzz Sol Cliente", numero_telefone="65999990003")
        sol = self._ultima("Zzz Sol Cliente")
        master = self._logado("Zzz Teste Master Sol")
        master.post(f"/admin/solicitacoes/{sol['id']}/aprovar", data={"tipo": "cliente", "plano_id": str(self.plano)})
        user = models.get_user_by_username("Zzz Sol Cliente")
        self.assertEqual(user["is_cliente"], 1)
        assinatura = models.get_assinatura(user["id"])
        self.assertEqual((assinatura["plano_id"], assinatura["cpf_cnpj"], assinatura["status"]), (self.plano, CPF_VALIDO, "pendente"))
        self.assertEqual(models.get_solicitacao(sol["id"])["tipo"], "cliente")

    def test_recusar_nao_cria_conta(self):
        self._enviar_e_confirmar(username="Zzz Sol Recusada", tipo="cliente", plano_id=str(self.plano),
                                 numero_telefone="65999990004")
        sol = self._ultima("Zzz Sol Recusada")
        self.assertEqual(sol["plano_id"], self.plano)
        master = self._logado("Zzz Teste Master Sol")
        self.assertIn("badge bg-danger", master.get("/mapa").get_data(as_text=True))
        master.post(f"/admin/solicitacoes/{sol['id']}/recusar")
        self.assertEqual(models.get_solicitacao(sol["id"])["status"], "recusada")
        self.assertIsNone(models.get_user_by_username("Zzz Sol Recusada"))
        self.assertEqual(master.post(f"/admin/solicitacoes/{sol['id']}/aprovar", data={"tipo": "usuario"}).status_code, 404)


    def test_codigo_errado_expirado_e_reenvio(self):
        self.enviadas.clear()
        resp = self._enviar(username="Zzz Sol Codigo", numero_telefone="65999990005")
        url = resp.headers["Location"]
        token = url.rsplit("/", 1)[1]
        c = webapp.app.test_client()
        self.assertIn("terminado em", c.get(url).get_data(as_text=True))
        self.assertIsNone(self._ultima("Zzz Sol Codigo"))  # Master ainda nao ve
        certo = self._codigo_para("5565999990005")
        errado = "000000" if certo != "000000" else "111111"

        c.post(url, data={"codigo": errado})
        sol = models.get_solicitacao_por_token(token)
        self.assertEqual((sol["status"], sol["codigo_tentativas"]), ("verificando", 1))

        # reenvio: so' depois de 1 minuto; gera codigo novo e zera tentativas
        self.assertIn("Aguarde", c.post(url, data={"acao": "reenviar"}, follow_redirects=True).get_data(as_text=True))
        models.gravar_codigo_solicitacao(sol["id"], sol["codigo_hash"], sol["codigo_expira"])  # volta o relogio:
        with mock.patch.object(webapp, "_agora_cuiaba_dt", return_value=webapp._agora_cuiaba_dt() + webapp.timedelta(minutes=2)):
            c.post(url, data={"acao": "reenviar"})
        novo = self._codigo_para("5565999990005")
        self.assertEqual(models.get_solicitacao_por_token(token)["codigo_reenvios"], 1)

        # codigo expirado nao vale
        with mock.patch.object(webapp, "_agora_cuiaba_dt", return_value=webapp._agora_cuiaba_dt() + webapp.timedelta(hours=2)):
            html = c.post(url, data={"codigo": novo}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("expirou", html)
        self.assertEqual(models.get_solicitacao_por_token(token)["status"], "verificando")

        self.assertIn("Solicitação enviada", c.post(url, data={"codigo": novo}).get_data(as_text=True))
        self.assertEqual(models.get_solicitacao_por_token(token)["status"], "pendente")
        self.assertIsNotNone(self._ultima("Zzz Sol Codigo"))

    def test_muitas_tentativas_bloqueia(self):
        resp = self._enviar(username="Zzz Sol Tentativas", numero_telefone="65999990006")
        url = resp.headers["Location"]
        certo = self._codigo_para("5565999990006")
        errado = "000000" if certo != "000000" else "111111"
        c = webapp.app.test_client()
        for _ in range(webapp.CODIGO_MAX_TENTATIVAS):
            c.post(url, data={"codigo": errado})
        html = c.post(url, data={"codigo": certo}, follow_redirects=True).get_data(as_text=True)
        self.assertIn("Muitas tentativas", html)
        self.assertIsNone(self._ultima("Zzz Sol Tentativas"))


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
