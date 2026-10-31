"""Sinal de celular pelos mapas de cobertura da Anatel (Mosaico SMP).

Rodar a partir de webapp/:  python -m unittest tests.test_sinal_celular
"""
import io
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shapefile  # noqa: E402

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
    "pred_2G_TIM_dbm-95.zip": _zip_shp([_quadrado(-56.0, -13.0, 1.0)]),
    "pred_2G_TIM_dbm-82.zip": _zip_shp([_quadrado(-40.0, -10.0, 0.5)]),
    "pred_5G_SA_NSA_CLARO_dbm-90.zip": _zip_shp([_quadrado(-55.71, -12.51, 0.02), _quadrado(-47.0, -16.0, 0.3)]),
}
PAGINA = "<html>" + "".join(
    f'<a href="/se/public/file/b/smp/{nome}">x</a> <a href="https://sistemas.anatel.gov.br/se/public/file/b/smp/{nome}">y</a>'
    for nome in CAMADAS) + "</html>"


def _abrir_falso(url, timeout):
    if url == sinal_celular.PAGINA:
        return io.BytesIO(PAGINA.encode())
    return io.BytesIO(CAMADAS[url.rsplit("/", 1)[-1]])


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
        self.assertEqual(info["camadas"], "5")
        self.assertTrue(info["baixado_em"])
        self.assertFalse(sinal_celular._vencida())

    def test_cobertura_no_ponto(self):
        r = {o["operadora"]: o for o in sinal_celular.estimar(-12.5, -55.7)}
        self.assertEqual(r["Vivo"]["tecnologias"], [{"tecnologia": "4G", "nivel": "bom"}])
        self.assertEqual(r["Vivo"]["nivel"], "otimo")  # 4G forte
        self.assertEqual(r["Claro"]["nivel"], "otimo")  # 5G forte
        self.assertEqual((r["TIM"]["nivel"], r["TIM"]["tecnologias"][0]["tecnologia"]), ("fraco", "2G"))  # 2G no minimo
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

    def test_falha_no_download_guarda_a_base_anterior(self):
        def fora_do_ar(url, timeout):
            raise OSError("fora do ar")
        with mock.patch.object(sinal_celular, "_abrir", fora_do_ar):
            sinal_celular.atualizar(forcar=True)
        info = sinal_celular.info()
        self.assertIn("fora do ar", info["erro"])
        self.assertTrue(sinal_celular.estimar(-12.5, -55.7))

    def test_telas(self):
        master = self._master()
        html = master.get("/mapa-interpolado").get_data(as_text=True)
        self.assertIn("Mapas de cobertura de celular (Anatel)", html)
        self.assertIn("5 mapas", html)
        with mock.patch.object(sinal_celular, "atualizar_em_segundo_plano") as baixar:
            resp = master.post("/mapa-interpolado/cobertura-anatel", follow_redirects=True)
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
        self.assertIn('title="TIM: fraco -- 2G mínimo"', html)


if __name__ == "__main__":
    unittest.main()
