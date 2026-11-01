"""Sinal de celular pelos mapas de cobertura da Anatel (Mosaico SMP).

Rodar a partir de webapp/:  python -m unittest tests.test_sinal_celular
"""
import io
import json
import math
import os
import sqlite3
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shapefile  # noqa: E402
from PIL import Image  # noqa: E402

import app as webapp  # noqa: E402
import models  # noqa: E402
import sinal_celular  # noqa: E402


def _quadrado(lon, lat, lado, horario=True):
    q = [(lon, lat), (lon, lat + lado), (lon + lado, lat + lado), (lon + lado, lat), (lon, lat)]
    return q if horario else q[::-1]


def _zip_shp(aneis):
    """Zip com um shapefile de UM poligono (varios aneis), como os da Anatel."""
    shp, shx, dbf = io.BytesIO(), io.BytesIO(), io.BytesIO()
    w = shapefile.Writer(shp=shp, shx=shx, dbf=dbf, shapeType=shapefile.POLYGON)
    w.field("OID", "N")
    w.poly(aneis)
    w.record(1)
    w.close()
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w") as z:
        for ext, buf in (("shp", shp), ("shx", shx), ("dbf", dbf)):
            z.writestr(f"camada.{ext}", buf.getvalue())
    return saida.getvalue()


# Fazenda de teste em (-12.5, -55.7).
CAMADAS = {
    # Vivo 4G forte num quadrado pequeno em volta da fazenda; minimo num
    # quadrado maior com um "buraco" em (-12.0, -55.0)
    "pred_4G_VIVO_dbm-90.zip": _zip_shp([_quadrado(-55.75, -12.55, 0.1)]),
    "pred_4G_VIVO_dbm-110.zip": _zip_shp([_quadrado(-56.0, -13.0, 1.5), _quadrado(-55.1, -12.1, 0.2, horario=False)]),
    "pred_3G_TIM_dbm-102.zip": _zip_shp([_quadrado(-56.0, -13.0, 1.0)]),
    "pred_3G_TIM_dbm-82.zip": _zip_shp([_quadrado(-40.0, -10.0, 0.5)]),
    # 2G e operadoras fora de Vivo/Claro/TIM: nem sao baixados
    "pred_2G_TIM_dbm-82.zip": _zip_shp([_quadrado(-56.0, -13.0, 1.0)]),
    "pred_4G_ALGAR_dbm-90.zip": _zip_shp([_quadrado(-56.0, -13.0, 1.0)]),
    # Claro 5G forte junto com 4G no minimo; o 2o quadrado de 5G nao tem 4G
    # (como o 5G da Vivo no Sudeste) e nao pode contar
    "pred_5G_SA_NSA_CLARO_dbm-90.zip": _zip_shp([_quadrado(-55.71, -12.51, 0.02), _quadrado(-47.0, -16.0, 0.3)]),
    "pred_4G_CLARO_dbm-110.zip": _zip_shp([_quadrado(-55.71, -12.51, 0.02)]),
}
PAGINA = "<html>" + "".join(
    f'<a href="/se/public/file/b/smp/{nome}">x</a> <a href="https://sistemas.anatel.gov.br/se/public/file/b/smp/{nome}">y</a>'
    for nome in CAMADAS) + "</html>"


def _abrir_falso(url, timeout):
    if url == sinal_celular.PAGINA:
        return io.BytesIO(PAGINA.encode())
    return io.BytesIO(CAMADAS[url.rsplit("/", 1)[-1]])


def _fazenda_mapa():
    if not any(vf["nome"] == "Zzz Fazenda Mapa Sinal" for vf in models.get_all_virtual_farms()):
        models.create_virtual_farm("Zzz Fazenda Mapa Sinal", -12.4, -55.6, 30)


class SinalCelularTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Sinal"):
            models.create_user("Zzz Teste Master Sinal", "x", is_master=True)
        cls.pasta = tempfile.mkdtemp()
        cls.patch = mock.patch.object(sinal_celular, "COBERTURA_PATH", Path(cls.pasta) / "cobertura_anatel.db")
        cls.patch.start()
        with mock.patch.object(sinal_celular, "_abrir", _abrir_falso):
            sinal_celular.atualizar(forcar=True)

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()

    def _master(self):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Teste Master Sinal")["id"])
            s["_fresh"] = True
        return c

    def test_nome_dos_arquivos(self):
        self.assertEqual(sinal_celular._camada("pred_4G_VIVO_dbm-90.zip"), ("4G", "Vivo", 90))
        self.assertEqual(sinal_celular._camada("pred_5G_SA_NSA_UNIFIQUE_dbm-110.zip"), ("5G", "Unifique", 110))
        self.assertEqual(sinal_celular._camada("pred_4G_IEZ_dbm-90.zip")[1], "IEZ!")
        self.assertIsNone(sinal_celular._camada("leiame.zip"))

    def test_baixou_tudo(self):
        info = sinal_celular.info()
        self.assertEqual(info["camadas"], "6")  # 8 na pagina, sem o 2G e a Algar
        self.assertTrue(info["baixado_em"])
        self.assertFalse(sinal_celular._vencida())

    def test_cobertura_no_ponto(self):
        r = {o["operadora"]: o for o in sinal_celular.estimar(-12.5, -55.7)}
        self.assertEqual(r["Vivo"]["tecnologias"], [{"tecnologia": "4G", "nivel": "bom"}])
        self.assertEqual(r["Vivo"]["nivel"], "otimo")  # 4G forte
        self.assertEqual(r["Claro"]["nivel"], "otimo")  # 5G forte
        self.assertEqual([x["tecnologia"] for x in r["Claro"]["tecnologias"]], ["5G", "4G"])
        # 5G sem 4G da mesma operadora nao conta (mapa grosseiro da Anatel)
        self.assertEqual({o["operadora"]: o["nivel"] for o in sinal_celular.estimar(-15.85, -46.85)}["Claro"], "sem")
        self.assertEqual((r["TIM"]["nivel"], r["TIM"]["tecnologias"][0]["tecnologia"]), ("fraco", "3G"))  # 3G no minimo
        self.assertEqual(len(r["TIM"]["tecnologias"]), 1)  # 2G forte da TIM nao conta
        self.assertNotIn("Algar", r)
        self.assertEqual(sinal_celular.estimar(-12.5, -55.7)[-1]["operadora"], "TIM")  # pior por ultimo
        # fora do quadrado forte, dentro do minimo: 4G minimo = bom; Claro aparece "sem" pra comparar
        r = {o["operadora"]: o["nivel"] for o in sinal_celular.estimar(-12.3, -55.3)}
        self.assertEqual(r, {"Vivo": "bom", "TIM": "fraco", "Claro": "sem"})
        # no buraco do poligono: sem Vivo
        self.assertEqual({o["operadora"]: o["nivel"] for o in sinal_celular.estimar(-12.0, -55.0)}["Vivo"], "sem")
        self.assertEqual({o["nivel"] for o in sinal_celular.estimar(-5.0, -45.0)}, {"sem"})
        self.assertEqual({o["nivel"] for o in sinal_celular.estimar(40.0, 10.0)}, {"sem"})  # fora do Brasil
        self.assertEqual(sinal_celular._nivel_operadora([{"tecnologia": "3G", "nivel": "bom"}]), "bom")
        with mock.patch.object(sinal_celular, "COBERTURA_PATH", Path(self.pasta) / "nao_existe.db"):
            self.assertIsNone(sinal_celular.estimar(-12.5, -55.7))

    def test_base_antiga_com_2g_e_outras_operadoras(self):
        """Base baixada antes da regra (servidor so' baixa de novo em 30
        dias): o que sobrou de 2G / outras operadoras e' ignorado."""
        lin, col = int((-12.5 - sinal_celular.LAT_MIN) // sinal_celular.RESOLUCAO), int((-55.7 - sinal_celular.LON_MIN) // sinal_celular.RESOLUCAO)
        bx, by = col // sinal_celular.BLOCO, lin // sinal_celular.BLOCO
        conn = sqlite3.connect(sinal_celular.COBERTURA_PATH)
        try:
            ids = [conn.execute("INSERT INTO camadas (arquivo, tecnologia, operadora, dbm) VALUES (?, ?, ?, ?)", c).lastrowid
                   for c in (("velho1", "4G", "Algar", 90), ("velho2", "2G", "Claro", 82))]
            conn.executemany("INSERT INTO blocos (bx, by, camada, cheio, dados) VALUES (?, ?, ?, 1, NULL)", [(bx, by, i) for i in ids])
            conn.commit()
            sinal_celular._cache.clear()
            sinal_celular._grades.clear()
            r = {o["operadora"]: o for o in sinal_celular.estimar(-12.5, -55.7)}
            self.assertNotIn("Algar", r)
            self.assertNotIn("2G", [x["tecnologia"] for x in r["Claro"]["tecnologias"]])
            self.assertEqual(sinal_celular.operadoras(), ["Vivo", "Claro", "TIM"])
        finally:
            conn.execute("DELETE FROM blocos WHERE camada IN (%s)" % ",".join("?" * len(ids)), ids)
            conn.execute("DELETE FROM camadas WHERE arquivo IN ('velho1', 'velho2')")
            conn.commit()
            conn.close()
            sinal_celular._cache.clear()
            sinal_celular._grades.clear()
        self.assertEqual(self._master().get("/sinal-telefonia/tile/Algar/10/353/541.png").status_code, 404)

    def test_falha_no_download_guarda_a_base_anterior(self):
        def fora_do_ar(url, timeout):
            raise OSError("fora do ar")
        with mock.patch.object(sinal_celular, "_abrir", fora_do_ar):
            sinal_celular.atualizar(forcar=True)
        info = sinal_celular.info()
        self.assertIn("fora do ar", info["erro"])
        self.assertTrue(sinal_celular.estimar(-12.5, -55.7))

    def _pixel(self, operadora, lat, lon, z=10):
        n = 2 ** z
        fx = (lon + 180) / 360 * n
        fy = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
        png = sinal_celular.tile(operadora, z, int(fx), int(fy))
        img = Image.open(io.BytesIO(png))
        return img.getpixel((int((fx % 1) * 256), int((fy % 1) * 256)))

    def test_tiles_por_operadora(self):
        self.assertEqual(sinal_celular.operadoras(), ["Vivo", "Claro", "TIM"])
        vivo = self._pixel("Vivo", -12.5, -55.7)
        self.assertEqual(vivo[:3], sinal_celular.CORES_OPERADORA["Vivo"])
        self.assertEqual(vivo[3], 215)  # otimo: cor mais forte
        self.assertEqual(self._pixel("Vivo", -12.3, -55.3)[3], 140)  # bom (4G minimo)
        self.assertEqual(self._pixel("TIM", -12.5, -55.7)[3], 70)  # fraco (3G minimo)
        self.assertEqual(self._pixel("Vivo", -12.0, -55.0)[3], 0)  # buraco
        self.assertEqual(self._pixel("Claro", -12.3, -55.3)[3], 0)
        self.assertEqual(self._pixel("Claro", -12.5, -55.7)[3], 215)  # 5G forte com 4G
        self.assertEqual(self._pixel("Claro", -15.85, -46.85)[3], 0)  # 5G sem 4G
        # zoom baixo sai da visao geral (nivel medio do quadrado: quadrado
        # todo otimo = cor forte; so' um pedaco coberto = cor mais fraca)
        self.assertEqual(self._pixel("Vivo", -12.5, -55.7, z=6)[3], 215)
        self.assertTrue(0 < self._pixel("Vivo", -12.5, -55.7, z=4)[3] < 215)

    def test_acesso_a_aba(self):
        master = self._master()
        resp = master.get("/sinal-telefonia/tile/Vivo/10/353/541.png")
        self.assertEqual((resp.status_code, resp.mimetype), (200, "image/png"))
        self.assertEqual(master.get("/sinal-telefonia/tile/Vivo/3/99/0.png").status_code, 404)
        if not models.get_user_by_username("Zzz Admin Sinal"):
            models.create_user("Zzz Admin Sinal", "x", is_admin=True)
        admin = webapp.app.test_client()
        with admin.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Admin Sinal")["id"])
            s["_fresh"] = True
        anterior = models.get_setting("acessos_por_nivel")
        try:
            self.assertEqual(admin.get("/sinal-telefonia").status_code, 403)  # padrao: so' Master
            models.set_setting("acessos_por_nivel", json.dumps({"admin": ["sinal_telefonia"], "usuario": []}))
            html = admin.get("/sinal-telefonia").get_data(as_text=True)
            self.assertIn("Sinal telefonia", html)
            self.assertIn("var fazendas = [", html)
            self.assertNotIn("Baixar agora", html)  # botao so' do Master
            self.assertEqual(admin.post("/sinal-telefonia/atualizar").status_code, 403)
            self.assertEqual(admin.get("/sinal-celular?lat=-12.5&lon=-55.7").status_code, 200)
            # Usuario: so' as fazendas liberadas pra ele aparecem no mapa
            _fazenda_mapa()
            self.assertIn("Zzz Fazenda Mapa Sinal", admin.get("/sinal-telefonia").get_data(as_text=True))
            if not models.get_user_by_username("Zzz Usuario Sinal"):
                models.create_user("Zzz Usuario Sinal", "x")
            usuario = webapp.app.test_client()
            with usuario.session_transaction() as s:
                s["_user_id"] = str(models.get_user_by_username("Zzz Usuario Sinal")["id"])
                s["_fresh"] = True
            models.set_setting("acessos_por_nivel", json.dumps({"admin": [], "usuario": ["sinal_telefonia"]}))
            html = usuario.get("/sinal-telefonia").get_data(as_text=True)
            self.assertIn("var fazendas = []", html)
        finally:
            models.set_setting("acessos_por_nivel", anterior or "")

    def test_telas(self):
        master = self._master()
        self.assertNotIn("Mapas de cobertura de celular", master.get("/mapa-interpolado").get_data(as_text=True))
        html = master.get("/sinal-telefonia").get_data(as_text=True)
        self.assertIn("Mapas de cobertura de celular (Anatel)", html)
        self.assertIn("6 mapas", html)
        self.assertIn("Baixar agora", html)
        self.assertIn('value="Vivo"', html)
        self.assertIn('href="/sinal-telefonia"', html)  # no menu Configuracoes
        _fazenda_mapa()
        html = master.get("/sinal-telefonia").get_data(as_text=True)
        self.assertIn('"nome": "Zzz Fazenda Mapa Sinal"', html)
        with mock.patch.object(sinal_celular, "atualizar_em_segundo_plano") as baixar:
            resp = master.post("/sinal-telefonia/atualizar", follow_redirects=True)
        baixar.assert_called_once_with(forcar=True)
        self.assertIn("Baixando os mapas de cobertura", resp.get_data(as_text=True))
        # consulta ao clicar no mapa
        d = master.get("/sinal-celular?lat=-12.5&lon=-55.7").get_json()
        self.assertIn("Vivo", [o["operadora"] for o in d["operadoras"]])
        self.assertIn("<strong>Vivo</strong>", d["html"])
        self.assertEqual(master.get("/sinal-celular?lat=x").status_code, 400)
        # Manejos: quadro em cada fazenda
        models.create_virtual_farm("Zzz Fazenda Sinal", -12.5, -55.7, 30)
        html = master.get("/fazendas").get_data(as_text=True)
        self.assertIn("Sinal de celular (Anatel)", html)
        self.assertIn('title="Vivo: ótimo -- 4G forte"', html)
        self.assertIn("#198754", html)  # verde
        # Novos cadastros: pela lavoura do cliente
        plano = models.salvar_plano(None, "Zzz Plano Sinal", "", 0, "MONTHLY", 2, 50, ["clima"], True, 9)
        uid = models.criar_cliente("Zzz Cliente Sinal", "segredo1", "", "5565999990000", "", plano)
        models.criar_cliente_ponto(uid, "Talhao Sinal", -12.5, -55.7)
        models.registrar_cadastro({"username": "Zzz Cliente Sinal", "nome_completo": "Zzz Cliente Sinal", "perfil": "produtor",
                                   "telefone": "5565999990000", "tipo": "cliente", "user_id": uid})
        html = master.get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("Sinal de celular em Talhao Sinal", html)
        self.assertIn('title="TIM: fraco -- 3G mínimo"', html)


if __name__ == "__main__":
    unittest.main()
