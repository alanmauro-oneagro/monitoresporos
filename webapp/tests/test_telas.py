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
import re
import shutil
import subprocess
import sys
import tempfile
import time
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

    @unittest.skipUnless(shutil.which("node"), "sem node pra conferir o JavaScript")
    def test_javascript_das_telas_sem_erro_de_sintaxe(self):
        """Script com erro de sintaxe derruba a tela inteira (o mapa do Mapas de
        Estudo sumiu por um "});" sobrando -- 11/11/2026)."""
        for nivel in ("master", "cliente"):
            for rota in _rotas_get():
                resp = self._cliente(nivel).get(rota, follow_redirects=True)
                if "text/html" not in resp.content_type:
                    continue
                for i, js in enumerate(re.findall(r"<script(?![^>]*\b(?:src|type)=)[^>]*>(.*?)</script>", resp.get_data(as_text=True), re.S)):
                    with self.subTest(nivel=nivel, rota=rota, script=i):
                        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                            f.write(js)
                        r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
                        os.unlink(f.name)
                        self.assertEqual(r.returncode, 0, r.stderr[:500])

    def test_estudo_de_viabilidade_so_no_mapas_de_estudo(self):
        """Estudo de viabilidade fica so' no Mapas de Estudo, agrupado (12/11/2026)."""
        c = self._cliente("master")
        c.post("/mapa-interpolado/adicionar", data={
            "tipo": "estudo", "nome": "Zzz Estudo Viabilidade", "lat": "-12.5", "lon": "-55.7",
            "raio_km": "30", "country_code": "BR"})
        pe = next(p for p in models.get_pontos_estudo() if p["nome"] == "Zzz Estudo Viabilidade")
        self.assertNotIn("Zzz Estudo Viabilidade", {vf["nome"] for vf in models.get_all_virtual_farms()})
        html = c.get("/mapa-interpolado").get_data(as_text=True)
        self.assertIn("📐 Estudo de viabilidade</option>", html)
        self.assertIn('data-grupo="e"', html)
        self.assertIn("Zzz Estudo Viabilidade", html)
        self.assertNotIn("(interpola fazendas reais no raio)", html)
        self.assertNotIn("FAZENDAS_URL", html)  # pino so' marca, nao leva pra Manejos
        self.assertNotIn("mapa-tt-row", html)  # sem quadro de esporos no balao
        self.assertLess(html.index('id="cartao-anatel"'), html.index('id="form-novo-ponto"'))
        for rota in ("/recommendations", "/fazendas", "/dashboard", "/whatsapp"):
            self.assertNotIn("Zzz Estudo Viabilidade", c.get(rota, follow_redirects=True).get_data(as_text=True))
        c.post("/mapa-interpolado/editar", data={
            "site_name": f"estudo:{pe['id']}", "nome": "Zzz Estudo Viabilidade 2", "lat": "-12.6", "lon": "-55.8",
            "raio_km": "40", "country_code": "BR"})
        self.assertEqual(models.get_ponto_estudo(pe["id"])["raio_km"], 40)
        c.post("/mapa-interpolado/remover", data={"site_name": f"estudo:{pe['id']}"})
        self.assertIsNone(models.get_ponto_estudo(pe["id"]))

    def test_ponto_vira_estudo_e_volta(self):
        c = self._cliente("master")
        base = {"lat": "-12.5", "lon": "-55.7", "raio_km": "30", "country_code": "BR"}
        c.post("/mapa-interpolado/adicionar", data={**base, "tipo": "doenca", "nome": "Zzz Vira Estudo"})
        vf = next(v for v in models.get_all_virtual_farms() if v["nome"] == "Zzz Vira Estudo")
        c.post("/mapa-interpolado/editar", data={**base, "site_name": vf["site_name"], "nome": "Zzz Vira Estudo", "tipo": "estudo"})
        self.assertIsNone(models.get_virtual_farm(vf["site_name"]))
        pe = next(p for p in models.get_pontos_estudo() if p["nome"] == "Zzz Vira Estudo")
        html = c.get("/mapa-interpolado").get_data(as_text=True)
        self.assertIn('value="estudo" selected', html)
        c.post("/mapa-interpolado/editar", data={**base, "site_name": f"estudo:{pe['id']}", "nome": "Zzz Vira Estudo", "tipo": "clima"})
        self.assertIsNone(models.get_ponto_estudo(pe["id"]))
        vf2 = next(v for v in models.get_all_virtual_farms() if v["nome"] == "Zzz Vira Estudo")
        self.assertEqual(vf2["tipo"], "clima")
        models.delete_virtual_farm(vf2["site_name"])

    def test_ocultar_em_manejos(self):
        # Ocultar so' em Gerenciar Clientes (pedido do Alan, 12/11/2026)
        self.assertNotIn("ocultar-manejos", self._cliente("master").get("/fazendas").get_data(as_text=True))
        self.assertIn("save_site_ativo", "".join(open(os.path.join(os.path.dirname(webapp.__file__), "templates", "admin_solicitacoes.html")).read().split()))
        self.assertNotIn("ocultar-manejos", self._cliente("cliente").get("/fazendas", follow_redirects=True).get_data(as_text=True))

    def test_salva_sozinho_sem_botao_salvar(self):
        """Todo o site salva sozinho, sem botao Salvar (12/11/2026)."""
        c = self._cliente("master")
        for rota in _rotas_get():
            resp = c.get(rota, follow_redirects=True)
            if "text/html" not in resp.content_type:
                continue
            html = resp.get_data(as_text=True)
            with self.subTest(rota=rota):
                self.assertIsNone(re.search(r">\s*(💾\s*)?Salvar[^<]*</button>", html))
        h = {"X-Autosave": "1"}
        base = {"ciclo__novo": "MONTHLY", "max_pontos__novo": "1", "raio_km__novo": "50", "ordem__novo": "9"}
        r = c.post("/admin/planos/salvar", data={**base, "nome__novo": "Zzz Plano Auto"}, headers=h).get_json()
        self.assertTrue(r["ok"])
        self.assertIn("valor", r["aviso"])
        self.assertNotIn("Zzz Plano Auto", [p["nome"] for p in models.get_planos()])
        r = c.post("/admin/planos/salvar", data={**base, "nome__novo": "Zzz Plano Auto", "valor__novo": "0"}, headers=h).get_json()
        self.assertTrue(r["reload"])
        novo = next(p for p in models.get_planos() if p["nome"] == "Zzz Plano Auto")
        models.excluir_plano(novo["id"])

    def test_grupo_no_editar_ponto(self):
        c = self._cliente("master")
        base = {"lat": "-12.5", "lon": "-55.7", "raio_km": "30", "country_code": "BR"}
        c.post("/mapa-interpolado/adicionar", data={**base, "tipo": "clima", "nome": "Zzz Grupo Novo", "grupo_economico": "Zzz Grupo N"})
        vn = next(v for v in models.get_all_virtual_farms() if v["nome"] == "Zzz Grupo Novo")
        with webapp.app.test_request_context():
            self.assertEqual(webapp._grupo_economico_da_fazenda(vn["site_name"]), "Zzz Grupo N")
        self.assertIn('name="grupo_economico" class="form-control form-control-sm" maxlength="40" list="grupos-economicos" placeholder="(sem grupo)">',
                      c.get("/mapa-interpolado").get_data(as_text=True))
        models.salvar_grupo_fazenda_dados(vn["site_name"], None)
        models.delete_virtual_farm(vn["site_name"])
        c.post("/mapa-interpolado/adicionar", data={**base, "tipo": "estudo", "nome": "Zzz Grupo Estudo"})
        pe = next(p for p in models.get_pontos_estudo() if p["nome"] == "Zzz Grupo Estudo")
        c.post("/mapa-interpolado/editar", data={**base, "site_name": f"estudo:{pe['id']}", "nome": "Zzz Grupo Estudo",
                                                 "tipo": "estudo", "grupo_economico": "Zzz Grupo X"})
        self.assertEqual(models.get_ponto_estudo(pe["id"])["grupo_economico"], "Zzz Grupo X")
        html = c.get("/mapa-interpolado").get_data(as_text=True)
        self.assertIn("👥 Zzz Grupo X", html)
        self.assertIn('<span class="toggle-icon">▶</span> Zzz Grupo Estudo', html)
        # vira ponto de doenca levando o grupo
        c.post("/mapa-interpolado/editar", data={**base, "site_name": f"estudo:{pe['id']}", "nome": "Zzz Grupo Estudo",
                                                 "tipo": "doenca", "grupo_economico": "Zzz Grupo X"})
        vf = next(v for v in models.get_all_virtual_farms() if v["nome"] == "Zzz Grupo Estudo")
        with webapp.app.test_request_context():
            self.assertEqual(webapp._grupo_economico_da_fazenda(vf["site_name"]), "Zzz Grupo X")
        models.salvar_grupo_fazenda_dados(vf["site_name"], None)
        models.delete_virtual_farm(vf["site_name"])

    def test_fungicidas_por_pais_como_doencas(self):
        html = self._cliente("master").get("/admin/fungicidas").get_data(as_text=True)
        self.assertIn('class="mt-3 mb-2 pais-doencas">🌎 Brasil', html)

    def test_manejos_ts_sulco_folha_em_boxes(self):
        """TS/Sulco/Folha em boxes iguais a Aplicacoes, recolhidos (12/11/2026)."""
        site = models.create_virtual_farm("Zzz Boxes Manejo", -12.5, -55.7, 30)
        try:
            # linha antiga (sem caixa) com ingrediente ativo
            models.set_farm_produtos(site, "safra1", "ts", "quimico", [("antes do plantio", "Zzz Prod", "Zzz IA")])
            c = self._cliente("master")
            html = c.get("/fazendas").get_data(as_text=True)
            self.assertIn('class="btn btn-link text-decoration-none p-0 fw-bold secao-toggle"', html)
            self.assertIn('value="Zzz Prod (Zzz IA)"', html)
            self.assertIn('type="text" name="cx_data_ts" class="form-control form-control-sm" value="antes do plantio"', html)
            self.assertNotIn("Nome do produto", html)
            c.post("/fazendas/save", data={
                "site_name": site, "safra": "safra1",
                "cx_data_ts": ["2026-11-10", ""], "cx_talhao_ts": ["T1", ""],
                "cx_quimicos_ts": ["Zzz Prod", ""], "cx_biologicos_ts": ["Zzz Bio", ""]}, headers={"X-Autosave": "1"})
            p = models.get_all_farm_produtos()[site]
            q, b = p[("safra1", "ts", "quimico")], p[("safra1", "ts", "biologico")]
            self.assertEqual((q[0]["nome"], q[0]["ingrediente_ativo"], q[0]["talhao"], q[0]["caixa"]), ("Zzz Prod", "Zzz IA", "T1", 0))
            self.assertEqual((b[0]["nome"], b[0]["caixa"], b[0]["data_anotacao"]), ("Zzz Bio", 0, "2026-11-10"))
            html = c.get("/fazendas").get_data(as_text=True)
            self.assertIn('value="Zzz Bio"', html)
        finally:
            models.delete_virtual_farm(site)

    def test_atualizar_mostra_progresso(self):
        """Botao Atualizar: sem aviso no topo, "Atualizando... XX%" pelo status (11/11/2026)."""
        import threading
        liberar, chegou = threading.Event(), threading.Event()

        def busca_falsa(*_a, progresso=None, **_k):
            progresso(50)
            chegou.set()
            liberar.wait(5)
            return []
        c = self._cliente("master")
        with mock.patch.object(webapp, "_fetch_configured", return_value=True), \
                mock.patch.object(webapp, "BIOSCOUT_USERNAME", "u"), mock.patch.object(webapp, "BIOSCOUT_PASSWORD", "p"), \
                mock.patch.object(webapp.bioscout_fetch, "fetch_recent", side_effect=busca_falsa), \
                mock.patch.object(webapp, "ULTIMA_BUSCA_MARCA", webapp.Path(tempfile.mkdtemp()) / "marca"):
            self.assertIn("botao-atualizar", c.get("/dashboard").get_data(as_text=True))
            self.assertEqual(c.post("/dashboard/atualizar", headers={"X-Progresso": "1"}).get_json(), {"ok": True})
            self.assertTrue(chegou.wait(5))
            self.assertEqual(c.get("/atualizacao/bioscout/status").get_json(), {"rodando": True, "pct": 50})
            self.assertIn("Atualizando... 50%", c.get("/dashboard").get_data(as_text=True))
            liberar.set()
            for _ in range(50):
                if not c.get("/atualizacao/bioscout/status").get_json()["rodando"]:
                    break
                time.sleep(0.1)
            self.assertFalse(c.get("/atualizacao/bioscout/status").get_json()["rodando"])

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
            # padrao da aba Acessos: Administrador = Usuarios + Mapas de Estudo
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
