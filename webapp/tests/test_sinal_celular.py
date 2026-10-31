"""Sinal de celular estimado pela base de antenas (ERBs) da Anatel.

Rodar a partir de webapp/:  python -m unittest tests.test_sinal_celular
"""
import gzip
import io
import os
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
import models  # noqa: E402
import sinal_celular  # noqa: E402

# Fazenda de teste em (-12.5, -55.7). 1 grau de latitude ~ 111 km.
CSV_ANATEL = "\n".join([
    "NomeEntidade;NumEstacao;Tecnologia;FreqTxMHz;Latitude;Longitude;Município",
    "TELEFONICA BRASIL S.A.;1;LTE;763.000;-12.5450;-55.7000;Sorriso",        # Vivo 4G 700 a ~5 km -> bom
    "TELEFONICA BRASIL S.A.;2;LTE;1845.000;-12.5100;-55.7000;Sorriso",       # Vivo 4G 1800 a ~1 km -> bom (mais perto)
    "CLARO S.A.;3;LTE;1845.000;12S3500;55W4200;Sorriso",                     # Claro 4G 1800 a ~10 km (DMS) -> fraco
    "TIM S A;4;GSM;935.000;-12.6800;-55.7000;Sorriso",                       # TIM 2G 900 a ~20 km -> fraco
    "OI MOVEL S.A.;5;NR;3550.000;-12.7000;-55.7000;Sorriso",                 # Oi 5G 3500 a ~22 km -> fora do alcance
    "CLARO S.A.;6;LTE;;-12.5;-55.7;Sem frequencia",                          # descartada
    "linha quebrada",
])


def _arquivo(conteudo_bytes):
    f = tempfile.NamedTemporaryFile(delete=False)
    f.write(conteudo_bytes)
    f.close()
    return f.name


class SinalCelularTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Sinal"):
            models.create_user("Zzz Teste Master Sinal", "x", is_master=True)
        sinal_celular.importar(_arquivo(CSV_ANATEL.encode("latin-1")))

    def _master(self):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Teste Master Sinal")["id"])
            s["_fresh"] = True
        return c

    def test_leitura_de_campos(self):
        self.assertAlmostEqual(sinal_celular._coordenada("12S3500"), -(12 + 35 / 60), places=5)
        self.assertAlmostEqual(sinal_celular._coordenada("55W4200"), -(55 + 42 / 60), places=5)
        self.assertAlmostEqual(sinal_celular._coordenada("-12,5"), -12.5)
        self.assertEqual(sinal_celular._operadora("TELEFÔNICA BRASIL S.A."), "Vivo")
        self.assertEqual(sinal_celular._operadora("TIM S A"), "TIM")
        self.assertEqual((sinal_celular._faixa(763), sinal_celular._faixa(1845), sinal_celular._faixa(3550)), (700, 1800, 3500))
        self.assertEqual(sinal_celular._tecnologia("NR"), "5G")

    def test_estimativa_por_operadora(self):
        r = {o["operadora"]: o for o in sinal_celular.estimar(-12.5, -55.7)}
        self.assertEqual((r["Vivo"]["nivel"], r["Vivo"]["faixa"]), ("bom", 1800))
        self.assertEqual((r["Claro"]["nivel"], r["Claro"]["tecnologia"]), ("fraco", "4G"))
        self.assertEqual((r["TIM"]["nivel"], r["TIM"]["faixa"]), ("fraco", 900))
        self.assertNotIn("Oi", r)  # 5G 3500 a 22 km nao alcanca
        self.assertEqual([o["operadora"] for o in sinal_celular.estimar(-12.5, -55.7)][0], "Vivo")  # melhor primeiro
        self.assertEqual(sinal_celular.estimar(-5.0, -45.0), [])  # longe de tudo
        with mock.patch.object(models, "erbs_carregadas", return_value=False):
            self.assertIsNone(sinal_celular.estimar(-12.5, -55.7))

    def test_importa_zip_e_gz(self):
        zip_bytes = io.BytesIO()
        with zipfile.ZipFile(zip_bytes, "w") as z:
            z.writestr("estacoes.csv", CSV_ANATEL)
        for conteudo in (zip_bytes.getvalue(), gzip.compress(CSV_ANATEL.encode("utf-8"))):
            with self.subTest(formato=conteudo[:2]):
                antenas, lidas = sinal_celular.importar(_arquivo(conteudo))
                self.assertEqual((antenas, lidas), (5, 7))
        with self.assertRaises(ValueError):
            sinal_celular.importar(_arquivo(b"a;b;c\n1;2;3\n"))

    def test_telas(self):
        master = self._master()
        # envio do arquivo pelo Mapa Interpolado
        resp = master.post("/mapa-interpolado/antenas", data={"arquivo": (io.BytesIO(CSV_ANATEL.encode("latin-1")), "erbs.csv")},
                           content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("Base de antenas atualizada: 5 antenas", resp.get_data(as_text=True))
        ruim = master.post("/mapa-interpolado/antenas", data={"arquivo": (io.BytesIO(b"x;y\n1;2"), "x.csv")},
                           content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("coluna(s) nao encontrada(s)", ruim.get_data(as_text=True))
        self.assertIn("5 antenas", master.get("/mapa-interpolado").get_data(as_text=True))
        # consulta ao clicar no mapa
        d = master.get("/sinal-celular?lat=-12.5&lon=-55.7").get_json()
        self.assertEqual(d["operadoras"][0]["operadora"], "Vivo")
        self.assertEqual(master.get("/sinal-celular?lat=x").status_code, 400)
        # Manejos: quadro em cada fazenda
        models.create_virtual_farm("Zzz Fazenda Sinal", -12.5, -55.7, 30)
        html = master.get("/fazendas").get_data(as_text=True)
        self.assertIn("Sinal de celular (estimado)", html)
        self.assertIn("Vivo</strong>: 4G 1800 MHz", html)
        # Novos cadastros: pela lavoura do cliente
        plano = models.salvar_plano(None, "Zzz Plano Sinal", "", 0, "MONTHLY", 2, 50, ["clima"], True, 9)
        uid = models.criar_cliente("Zzz Cliente Sinal", "segredo1", "", "5565999990000", "", plano)
        models.criar_cliente_ponto(uid, "Talhao Sinal", -12.5, -55.7)
        models.registrar_cadastro({"username": "Zzz Cliente Sinal", "nome_completo": "Zzz Cliente Sinal", "perfil": "produtor",
                                   "telefone": "5565999990000", "tipo": "cliente", "user_id": uid})
        html = master.get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("Sinal de celular em Talhao Sinal", html)


if __name__ == "__main__":
    unittest.main()
