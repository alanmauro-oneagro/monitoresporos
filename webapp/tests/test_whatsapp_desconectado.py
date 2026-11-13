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

    def test_cliente_nao_ve_o_aviso(self):
        """Cliente do app nao ve o aviso do WhatsApp (pedido do Alan, 08/11/2026)."""
        plano = models.salvar_plano(None, "Zzz Plano Aviso Whats", "", 0, "MONTHLY", 1, 30, ["clima", "site_recomendacoes"], True, 9)
        uid = models.criar_cliente("Zzz Cli Aviso Whats", "segredo1", "", "5565999990181", "", plano)
        models.atualizar_assinatura(uid, status="ativa", cortesia_ate="2099-01-01")
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(uid)
            s["_fresh"] = True
        with self._status(False):
            resp = c.get("/recommendations/safra1", follow_redirects=True)
            self.assertEqual(resp.status_code, 200)
            self.assertNotIn("⚠ WhatsApp desconectado", resp.get_data(as_text=True))
            self.assertIn("⚠ WhatsApp desconectado", self._master().get("/recommendations/safra1", follow_redirects=True).get_data(as_text=True))

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


class ApiOficialMetaTest(unittest.TestCase):
    """Parametros da API oficial da Meta (pedido do Alan, 13/11/2026): webhook, modelos sem PDF e NDVI."""

    @classmethod
    def setUpClass(cls):
        import whatsapp_cloud_api
        cls.w = whatsapp_cloud_api
        models.init_db()
        webapp.app.config["TESTING"] = True

    def _assinar(self, corpo, segredo):
        import hashlib
        import hmac
        return "sha256=" + hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()

    def test_webhook_verificacao_e_avisos(self):
        import json
        c = webapp.app.test_client()
        with mock.patch.object(self.w, "WEBHOOK_VERIFY_TOKEN", "zzz-token"), mock.patch.object(self.w, "APP_SECRET", "zzz-segredo"):
            r = c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=zzz-token&hub.challenge=123")
            self.assertEqual((r.status_code, r.get_data(as_text=True)), (200, "123"))
            self.assertEqual(c.get("/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=errado&hub.challenge=1").status_code, 403)
            models.log_whatsapp_envio("Zzz Fazenda Meta", "Zzz Pessoa", "5565999990001", True, "Modelo oficial (resumo): enviado [wamid.ZZZ1]")
            corpo = json.dumps({"entry": [{"changes": [{"value": {
                "statuses": [{"id": "wamid.ZZZ1", "status": "read"}],
                "contacts": [{"wa_id": "5565999990001", "profile": {"name": "Zzz Pessoa"}}],
                "messages": [{"from": "5565999990001", "type": "text", "text": {"body": "Zzz obrigado"}}],
            }}]}]}).encode()
            self.assertEqual(c.post("/webhooks/whatsapp", data=corpo, content_type="application/json",
                                    headers={"X-Hub-Signature-256": "sha256=errado"}).status_code, 403)
            r = c.post("/webhooks/whatsapp", data=corpo, content_type="application/json",
                       headers={"X-Hub-Signature-256": self._assinar(corpo, "zzz-segredo")})
            self.assertEqual(r.status_code, 200)
        log = [l for l in models.get_whatsapp_envio_log(site_name="Zzz Fazenda Meta")]
        self.assertEqual(log[0]["status_meta"], "lido")
        self.assertTrue(any(m["texto"] == "Zzz obrigado" for m in models.whatsapp_recebidas()))
        # falha da Meta marca a linha como falha
        models.atualizar_status_meta("wamid.ZZZ1", "failed", "Fora da janela")
        log = models.get_whatsapp_envio_log(site_name="Zzz Fazenda Meta")
        self.assertEqual((log[0]["ok"], log[0]["status_meta"]), (0, "falhou (Fora da janela)"))

    def test_modelos_texto_e_ndvi(self):
        enviados = []
        with mock.patch.object(self.w, "_configurado", return_value=True), \
                mock.patch.object(self.w, "_upload_media", return_value=(True, "MEDIA1")), \
                mock.patch.object(self.w, "_post_json", side_effect=lambda p, payload, **k: (enviados.append(payload) or True, {"messages": [{"id": "wamid.X"}]})):
            ok, msg = self.w.send_relatorio_texto_modelo("5565999990001", ["Zzz", "Fazenda", "13/11/2026", "Ferrugem\\n(Perigo)"])
            self.assertEqual((ok, msg), (True, "enviado [wamid.X]"))
            ok, msg = self.w.send_ndvi_modelo("5565999990001", b"png", ["Zzz", "Fazenda", "13/11/2026"])
            self.assertTrue(ok)
        texto, ndvi = enviados
        self.assertEqual(texto["template"]["name"], self.w.TEMPLATE_RELATORIO_TEXTO)
        self.assertEqual(len(texto["template"]["components"]), 1)  # so' corpo, sem cabecalho
        self.assertEqual(ndvi["template"]["name"], self.w.TEMPLATE_NDVI)
        self.assertEqual(ndvi["template"]["components"][0]["parameters"][0]["image"]["id"], "MEDIA1")

    def test_tela_mostra_parametros_sem_valor(self):
        if not models.get_user_by_username("Zzz Master Meta"):
            models.create_user("Zzz Master Meta", "x", is_master=True)
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username("Zzz Master Meta")["id"])
            s["_fresh"] = True
        with mock.patch.object(self.w, "ACCESS_TOKEN", "zzz-segredo-nao-mostrar"):
            html = c.get("/admin/whatsapp").get_data(as_text=True)
        self.assertIn("WHATSAPP_CLOUD_PHONE_NUMBER_ID", html)
        self.assertIn("/webhooks/whatsapp", html)
        self.assertNotIn("zzz-segredo-nao-mostrar", html)
