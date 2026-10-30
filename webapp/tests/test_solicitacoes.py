"""Pre-cadastro publico (/solicitar-acesso) com aprovacao do Master.

Rodar a partir de webapp/:  python -m unittest tests.test_solicitacoes
"""
import os
import sys
import tempfile
import unittest

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

    def _logado(self, nome):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username(nome)["id"])
            s["_fresh"] = True
        return c

    def _enviar(self, **extra):
        return webapp.app.test_client().post("/solicitar-acesso", data=_form(**extra))

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
                self.assertEqual(self._enviar(**extra).status_code, 200)
                self.assertIsNone(self._ultima(extra["username"]))

    def test_usuario_aprovado_entra_com_a_senha_dele(self):
        resp = self._enviar(username="Zzz Sol Usuario", consultor="on", regiao="Sorriso e Sinop (MT)")
        self.assertIn("Solicitação enviada", resp.get_data(as_text=True))
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
        self._enviar(username="Zzz Sol Cliente")
        sol = self._ultima("Zzz Sol Cliente")
        master = self._logado("Zzz Teste Master Sol")
        master.post(f"/admin/solicitacoes/{sol['id']}/aprovar", data={"tipo": "cliente", "plano_id": str(self.plano)})
        user = models.get_user_by_username("Zzz Sol Cliente")
        self.assertEqual(user["is_cliente"], 1)
        assinatura = models.get_assinatura(user["id"])
        self.assertEqual((assinatura["plano_id"], assinatura["cpf_cnpj"], assinatura["status"]), (self.plano, CPF_VALIDO, "pendente"))
        self.assertEqual(models.get_solicitacao(sol["id"])["tipo"], "cliente")

    def test_recusar_nao_cria_conta(self):
        self._enviar(username="Zzz Sol Recusada", tipo="cliente", plano_id=str(self.plano))
        sol = self._ultima("Zzz Sol Recusada")
        self.assertEqual(sol["plano_id"], self.plano)
        master = self._logado("Zzz Teste Master Sol")
        self.assertIn("badge bg-danger", master.get("/mapa").get_data(as_text=True))
        master.post(f"/admin/solicitacoes/{sol['id']}/recusar")
        self.assertEqual(models.get_solicitacao(sol["id"])["status"], "recusada")
        self.assertIsNone(models.get_user_by_username("Zzz Sol Recusada"))
        self.assertEqual(master.post(f"/admin/solicitacoes/{sol['id']}/aprovar", data={"tipo": "usuario"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
