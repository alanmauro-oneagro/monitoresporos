"""Abre cada tela (GET sem parametro na URL) com cada tipo de conta --
Master, Administrador, Usuario e Cliente -- e confere que nenhuma da'
erro 500 e que as regras de acesso continuam valendo. Rede de seguranca
da migracao SQLite -> PostgreSQL (Fase 0 do plano no CLAUDE.md).

Rodar a partir de webapp/:  python -m unittest tests.test_telas
Usa um banco temporario e os CSVs reais de data/; toda chamada de rede
(urllib) falha na hora, de proposito -- as telas tem que aguentar
servico externo fora do ar sem quebrar.
"""
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest import mock

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
os.environ.pop("ASAAS_API_KEY", None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
import models  # noqa: E402

CPF_VALIDO = "52998224725"

# Telas que dependem de parametro na query string (sem ele, 400 e'
# resposta valida) ou que disparam acao (logout).
_FORA = {"/logout", "/graficos/dados", "/mapa-interpolado/detectar-pais", "/sw.js"}


def _rotas_get():
    rotas = set()
    for regra in webapp.app.url_map.iter_rules():
        if "GET" in regra.methods and not regra.arguments and not regra.rule.startswith("/static"):
            rotas.add(regra.rule)
    return sorted(rotas - _FORA)


def _sem_rede(*_args, **_kwargs):
    raise urllib.error.URLError("rede desligada no teste")


class TelasPorNivelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        cls.patch_rede = mock.patch.object(urllib.request, "urlopen", side_effect=_sem_rede)
        cls.patch_rede.start()
        if not models.get_user_by_username("Zzz Teste Master Telas"):
            models.create_user("Zzz Teste Master Telas", "x", is_master=True)
        for nome, admin in (("Zzz Teste Admin Telas", True), ("Zzz Teste Usuario Telas", False)):
            if not models.get_user_by_username(nome):
                models.create_user(nome, "x", is_admin=admin)
        if not models.get_user_by_username("Zzz Teste Cliente Telas"):
            plano = models.salvar_plano(None, "Zzz Plano Telas", "", 10.0, "MONTHLY", 1, 50, ["clima"], True, 1)
            models.criar_cliente("Zzz Teste Cliente Telas", "segredo1", "c@t.com", "5565999990000", CPF_VALIDO, plano)
        cls.ids = {
            nivel: models.get_user_by_username(nome)["id"] for nivel, nome in (
                ("master", "Zzz Teste Master Telas"), ("admin", "Zzz Teste Admin Telas"),
                ("usuario", "Zzz Teste Usuario Telas"), ("cliente", "Zzz Teste Cliente Telas"),
            )
        }

    @classmethod
    def tearDownClass(cls):
        cls.patch_rede.stop()

    def _cliente(self, nivel):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(self.ids[nivel])
            s["_fresh"] = True
        return c

    def _status(self, nivel, rota):
        return self._cliente(nivel).get(rota).status_code

    def test_nenhuma_tela_da_erro_500(self):
        rotas = _rotas_get()
        self.assertGreater(len(rotas), 20)
        for nivel in ("master", "admin", "usuario", "cliente"):
            for rota in rotas:
                with self.subTest(nivel=nivel, rota=rota):
                    resp = self._cliente(nivel).get(rota, follow_redirects=True)
                    self.assertLess(resp.status_code, 500)

    def test_sem_login_vai_pro_login(self):
        c = webapp.app.test_client()
        for rota in ("/mapa", "/recommendations", "/fazendas"):
            with self.subTest(rota=rota):
                resp = c.get(rota)
                self.assertEqual(resp.status_code, 302)
                self.assertIn("/login", resp.headers["Location"])
        # telas de administracao respondem 403 direto (sem passar pelo login)
        for rota in ("/admin/users", "/admin/exportar", "/admin/acessos"):
            with self.subTest(rota=rota):
                self.assertEqual(c.get(rota).status_code, 403)

    def test_abas_de_configuracoes_por_nivel(self):
        esperado = {
            # padrao da aba Acessos: Administrador = Usuarios + Mapa Interpolado
            "admin": {"/admin/users": 200, "/mapa-interpolado": 200, "/admin/doencas": 403,
                      "/admin/fungicidas": 403, "/solo": 403, "/ndvi": 403, "/whatsapp": 403},
            "usuario": {"/admin/users": 403, "/mapa-interpolado": 403, "/admin/doencas": 403, "/whatsapp": 403},
            "master": {"/admin/users": 200, "/mapa-interpolado": 200, "/admin/doencas": 200,
                       "/admin/fungicidas": 200, "/solo": 200, "/ndvi": 200, "/whatsapp": 200},
        }
        for nivel, rotas in esperado.items():
            for rota, status in rotas.items():
                with self.subTest(nivel=nivel, rota=rota):
                    self.assertEqual(self._status(nivel, rota), status)

    def test_telas_so_do_master(self):
        for rota in ("/admin/exportar", "/admin/exportar/backup", "/admin/whatsapp", "/admin/fazendas-ocultas",
                     "/admin/acessos", "/admin/planos"):
            for nivel in ("admin", "usuario"):
                with self.subTest(nivel=nivel, rota=rota):
                    self.assertEqual(self._status(nivel, rota), 403)
            with self.subTest(nivel="master", rota=rota):
                self.assertEqual(self._status("master", rota), 200)

    def test_cliente_so_ve_o_app(self):
        for rota in ("/mapa", "/recommendations", "/fazendas", "/dashboard"):
            with self.subTest(rota=rota):
                resp = self._cliente("cliente").get(rota)
                self.assertEqual(resp.status_code, 302)
                self.assertTrue(resp.headers["Location"].endswith("/app"))
        # assinatura ainda pendente: o /app manda pra tela de assinatura
        resp = self._cliente("cliente").get("/app")
        self.assertIn(resp.status_code, (200, 302))
        if resp.status_code == 302:
            self.assertIn("/app/", resp.headers["Location"])

    def test_backup_do_banco(self):
        import gzip
        resp = self._cliente("master").get("/admin/exportar/backup")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(gzip.decompress(resp.data).startswith(b"SQLite format 3"))

    def test_backup_diario_guarda_os_ultimos(self):
        with tempfile.TemporaryDirectory() as pasta, \
                mock.patch.object(models, "BACKUP_DIR", models.Path(pasta)), \
                mock.patch.object(models, "_agora_cuiaba", return_value="2026-10-27 03:10:00"):
            for dia in range(1, 10):
                (models.Path(pasta) / f"{models.DB_PATH.stem}-202610{dia:02d}.db").write_bytes(b"x")
            novo = models.fazer_backup_diario()
            self.assertIsNotNone(novo)
            self.assertIsNone(models.fazer_backup_diario())  # um por dia
            restantes = sorted(p.name for p in models.Path(pasta).glob("*.db"))
            self.assertEqual(len(restantes), models.BACKUP_DIAS)
            self.assertEqual(restantes[-1], novo.name)
        with mock.patch.object(models, "_agora_cuiaba", return_value="2026-10-27 02:59:00"):
            self.assertIsNone(models.fazer_backup_diario())  # antes das 3h nao faz

    def test_restaurar_backup_so_existe_no_qap(self):
        self.assertFalse(webapp.AMBIENTE_QAP)
        self.assertEqual(self._status("master", "/admin/restaurar-backup"), 404)
        self.assertNotIn("AMBIENTE DE TESTE", self._cliente("master").get("/admin/exportar").get_data(as_text=True))
        with mock.patch.object(webapp, "AMBIENTE_QAP", True):
            self.assertEqual(self._status("admin", "/admin/restaurar-backup"), 403)
            self.assertEqual(self._status("master", "/admin/restaurar-backup"), 200)
            self.assertIn("AMBIENTE DE TESTE", self._cliente("master").get("/admin/exportar").get_data(as_text=True))

    def test_restaurar_backup_no_qap(self):
        import io
        backup = self._cliente("master").get("/admin/exportar/backup").data
        models.create_user("Zzz Teste Depois Do Backup", "x")
        with tempfile.TemporaryDirectory() as pasta, \
                mock.patch.object(models, "BACKUP_DIR", models.Path(pasta)), \
                mock.patch.object(webapp, "AMBIENTE_QAP", True):
            invalido = self._cliente("master").post(
                "/admin/restaurar-backup", data={"arquivo": (io.BytesIO(b"nao e banco"), "x.db.gz")},
                content_type="multipart/form-data",
            )
            self.assertEqual(invalido.status_code, 302)
            self.assertIsNotNone(models.get_user_by_username("Zzz Teste Depois Do Backup"))

            ok = self._cliente("master").post(
                "/admin/restaurar-backup", data={"arquivo": (io.BytesIO(backup), "backup.db.gz")},
                content_type="multipart/form-data",
            )
            self.assertEqual(ok.status_code, 302)
            self.assertIn("/login", ok.headers["Location"])
            self.assertIsNone(models.get_user_by_username("Zzz Teste Depois Do Backup"))
            self.assertIsNotNone(models.get_user_by_username("Zzz Teste Master Telas"))
            self.assertEqual(len(list(models.Path(pasta).glob("antes-restauracao-*.db"))), 1)


    def test_qap_primeiro_acesso_sem_login(self):
        import io
        anonimo = webapp.app.test_client()
        self.assertEqual(anonimo.get("/qap/primeiro-acesso").status_code, 404)  # fora do QAP
        with mock.patch.object(webapp, "AMBIENTE_QAP", True):
            # QAP ja' tem Master: rota fechada e sem aviso no login
            self.assertEqual(anonimo.get("/qap/primeiro-acesso").status_code, 404)
            self.assertNotIn("Primeiro acesso", anonimo.get("/login").get_data(as_text=True))
            backup = self._cliente("master").get("/admin/exportar/backup").data
            models.create_user("Zzz Teste Antes Primeiro Acesso", "x")
            with tempfile.TemporaryDirectory() as pasta, \
                    mock.patch.object(models, "BACKUP_DIR", models.Path(pasta)), \
                    mock.patch.object(models, "existe_master", return_value=False):
                self.assertIn("Primeiro acesso", anonimo.get("/login").get_data(as_text=True))
                self.assertEqual(anonimo.get("/qap/primeiro-acesso").status_code, 200)
                ruim = anonimo.post("/qap/primeiro-acesso", data={"arquivo": (io.BytesIO(b"x"), "x.gz")},
                                    content_type="multipart/form-data")
                self.assertIn("/qap/primeiro-acesso", ruim.headers["Location"])
                ok = anonimo.post("/qap/primeiro-acesso", data={"arquivo": (io.BytesIO(backup), "b.db.gz")},
                                  content_type="multipart/form-data")
                self.assertIn("/login", ok.headers["Location"])
            self.assertIsNone(models.get_user_by_username("Zzz Teste Antes Primeiro Acesso"))  # banco restaurado


    def test_qap_vazio_cria_login_do_alan(self):
        with mock.patch.object(models, "existe_master", return_value=False), \
                mock.patch.object(models, "get_user_by_username", return_value=None), \
                mock.patch.object(models, "create_user") as criar:
            webapp._bootstrap_master_qap()  # fora do QAP: nada
            criar.assert_not_called()
            with mock.patch.object(webapp, "AMBIENTE_QAP", True):
                webapp._bootstrap_master_qap()
            criar.assert_called_once_with(models.MASTER_PRINCIPAL, "Oneagro01!", is_master=True)
        with mock.patch.object(webapp, "AMBIENTE_QAP", True), mock.patch.object(models, "create_user") as criar:
            webapp._bootstrap_master_qap()  # ja tem Master: nao cria nem mexe em senha
            criar.assert_not_called()
        self.assertEqual(models.DEFAULT_PASSWORD, "Oneagro01!")


if __name__ == "__main__":
    unittest.main()
