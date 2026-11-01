"""WhatsApp desconectado: botoes de envio desativados e envio recusado
(pedido do Alan, 01/11/2026).

Rodar a partir de webapp/:  python -m unittest tests.test_whatsapp_desconectado
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("BIOSCOUT_DB_PATH", os.path.join(tempfile.mkdtemp(), "teste.db"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as webapp  # noqa: E402
import models  # noqa: E402
import whatsapp  # noqa: E402

AVISO = "WhatsApp desconectado"


class WhatsappDesconectadoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Whats"):
            models.create_user("Zzz Teste Master Whats", "x", is_master=True)
        if not models.get_user_by_username("Zzz Teste Alvo Whats"):
            models.create_user("Zzz Teste Alvo Whats", "x", telefone="5565999990077")
        nomes = {vf["nome"]: vf["site_name"] for vf in models.get_all_virtual_farms()}
        cls.clima = nomes.get("Zzz Clima Whats") or models.create_virtual_farm("Zzz Clima Whats", -12.5, -55.7, 30, tipo="clima")

    def setUp(self):
        whatsapp._status_cache["em"] = 0.0  # sem cache entre os testes

    def _master(self):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Teste Master Whats")["id"])
            s["_fresh"] = True
        return c

    def _status(self, conectado):
        return mock.patch.object(whatsapp, "get_status", return_value={"connected": conectado})

    def test_telas_com_botoes_desativados(self):
        master = self._master()
        with self._status(False):
            rec = master.get("/recommendations/safra1", follow_redirects=True).get_data(as_text=True)
            self.assertIn(AVISO, rec)
            self.assertNotIn('<details class="whatsapp-destinos', rec)
            self.assertIn('name="enviar_texto" value="1" checked', rec)
            self.assertRegex(rec, r'name="enviar_texto"[^>]*disabled>')
            self.assertIn("conectar de novo", rec)
            self.assertIn(AVISO, master.get("/ndvi").get_data(as_text=True))
            uid = models.get_user_by_username("Zzz Teste Alvo Whats")["id"]
            self.assertIn('value="test" class="btn btn-success" disabled', master.get(f"/admin/users/{uid}/editar").get_data(as_text=True))
        whatsapp._status_cache["em"] = 0.0
        with self._status(True):
            rec = master.get("/recommendations/safra1", follow_redirects=True).get_data(as_text=True)
            self.assertNotIn("⚠ WhatsApp desconectado", rec)

    def test_envio_recusado_sem_conexao(self):
        master = self._master()
        with self._status(False), mock.patch.object(whatsapp, "send_whatsapp") as enviar:
            resp = master.post(f"/alertas-clima/whatsapp/{self.clima}", data={"enviar_texto": "1"}, follow_redirects=True)
            self.assertIn(AVISO, resp.get_data(as_text=True))
            resp = master.post("/ndvi/historico/enviar-whatsapp", data={"site_name": self.clima}, follow_redirects=True)
            self.assertIn(AVISO, resp.get_data(as_text=True))
            resp = master.post(f"/recommendations/whatsapp/{self.clima}", data={"enviar_texto": "1"}, follow_redirects=True)
            self.assertIn(AVISO, resp.get_data(as_text=True))
            uid = models.get_user_by_username("Zzz Teste Alvo Whats")["id"]
            resp = master.post(f"/admin/users/{uid}/editar", data={"action": "test", "codigo_pais": "55",
                                                                 "numero_telefone": "65999990077", "nivel": "usuario"},
                               follow_redirects=True)
            self.assertIn(AVISO, resp.get_data(as_text=True))
            enviar.assert_not_called()

    def test_cache_do_status(self):
        with self._status(True) as status:
            self.assertTrue(whatsapp.conectado())
            self.assertTrue(whatsapp.conectado())
            self.assertEqual(status.call_count, 1)  # 2a pergunta veio do cache


if __name__ == "__main__":
    unittest.main()
