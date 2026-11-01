"""Ordem das abas do menu Configuracoes: so' o Alan ajusta (pedido dele,
01/11/2026); vale pro menu de todo mundo.

Rodar a partir de webapp/:  python -m unittest tests.test_menu_config
"""
import json
import os
import re
import sys
import tempfile
import unittest

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
import models  # noqa: E402


def _itens(html):
    return re.findall(r'class="d-flex align-items-center menu-config-item" data-chave="([a-z_]+)"', html)


class MenuConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        models.init_db()
        if not models.get_user_by_username(models.MASTER_PRINCIPAL):
            models.create_user(models.MASTER_PRINCIPAL, "x", is_master=True)
        if not models.get_user_by_username("Zzz Outro Master Menu"):
            models.create_user("Zzz Outro Master Menu", "x", is_master=True)
        if not models.get_user_by_username("Zzz Admin Menu"):
            models.create_user("Zzz Admin Menu", "x", is_admin=True)

    def setUp(self):
        self.anterior = models.get_setting("ordem_menu_config")

    def tearDown(self):
        models.set_setting("ordem_menu_config", self.anterior or "")

    def _logado(self, nome):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username(nome)["id"])
            s["_fresh"] = True
        return c

    def test_so_o_alan_ordena(self):
        alan = self._logado(models.MASTER_PRINCIPAL)
        html = alan.get("/mapa").get_data(as_text=True)
        self.assertIn("↕ Ordenar abas", html)
        self.assertEqual(_itens(html), [m[0] for m in webapp.MENU_CONFIG])  # ordem padrao
        outro = self._logado("Zzz Outro Master Menu")
        self.assertNotIn("↕ Ordenar abas", outro.get("/mapa").get_data(as_text=True))
        self.assertEqual(outro.post("/admin/menu-config/ordem", json={"ordem": ["planos"]}).status_code, 403)
        self.assertEqual(alan.post("/admin/menu-config/ordem", json={"ordem": ["nao_existe"]}).status_code, 400)

    def test_ordem_salva_vale_pra_todos(self):
        alan = self._logado(models.MASTER_PRINCIPAL)
        resp = alan.post("/admin/menu-config/ordem", json={"ordem": ["planos", "cadastros", "mapa_interpolado", "usuarios"]})
        self.assertEqual(resp.status_code, 200)
        ordem = resp.get_json()["ordem"]
        self.assertEqual(ordem[:4], ["planos", "cadastros", "mapa_interpolado", "usuarios"])
        self.assertEqual(len(ordem), len(webapp.MENU_CONFIG))  # o resto vem depois, na ordem padrao
        self.assertEqual(_itens(alan.get("/mapa").get_data(as_text=True))[:4], ordem[:4])
        # Administrador (padrao: Usuarios + Mapa de Estudos) ve so' as dele, na ordem do Alan
        admin = self._logado("Zzz Admin Menu").get("/mapa").get_data(as_text=True)
        self.assertEqual(_itens(admin), ["mapa_interpolado", "usuarios"])
        self.assertNotIn("↕ Ordenar abas", admin)

    def test_nomes_das_abas(self):
        html = self._logado(models.MASTER_PRINCIPAL).get("/mapa").get_data(as_text=True)
        for nome in ("Mapas NDVI", "Planos e Valores", "Gerenciar Acessos", "Programação de Envios", "Mapas de Solo"):
            self.assertIn(f">{nome}</a>", html)
        alan = self._logado(models.MASTER_PRINCIPAL)
        for url, titulo in (("/ndvi", "Mapas NDVI"), ("/admin/planos", "Planos e Valores"),
                            ("/admin/acessos", "Gerenciar Acessos"), ("/whatsapp", "Programação de Envios"),
                            ("/solo", "Mapas de Solo")):
            with self.subTest(url=url):
                self.assertIn(f"<title>{titulo} - OneAgro App</title>", alan.get(url).get_data(as_text=True))

    def test_sem_aba_bioscout(self):
        html = self._logado(models.MASTER_PRINCIPAL).get("/mapa").get_data(as_text=True)
        self.assertNotIn("bioscout.com.au", html)
        self.assertNotIn(">BioScout</a>", html)

    def test_setting_estragado_cai_na_ordem_padrao(self):
        models.set_setting("ordem_menu_config", "{nao e json")
        html = self._logado(models.MASTER_PRINCIPAL).get("/mapa").get_data(as_text=True)
        self.assertEqual(_itens(html), [m[0] for m in webapp.MENU_CONFIG])


if __name__ == "__main__":
    unittest.main()
