"""Cadastro publico (/solicitar-acesso): conta criada na hora, tela
"Gerenciar Clientes" do Master e CPF/CNPJ so' na hora de assinar.

Rodar a partir de webapp/:  python -m unittest tests.test_solicitacoes
"""
import os
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
        "modalidade": "fazenda", "perfil": ["produtor"], "nome_completo": "Zzz Teste da Silva Cadastro",
        "username": "Zzz Cadastro", "codigo_pais": "55", "numero_telefone": "65999990001", "email": "",
        "empresa": "Fazenda Zzz", "cidade": "Sorriso", "uf": "MT", "grupo_cultura": "hf", "cultura_outra": "Soja", "area_outra": "1.500",
        "mensagem": "Quero acompanhar a ferrugem", "senha": "segredo1", "senha_confirmar": "segredo1", "aceite": "on",
    }
    dados.update(extra)
    return dados


class CadastroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        webapp.app.config["TESTING"] = True
        if not models.get_user_by_username("Zzz Teste Master Cad"):
            models.create_user("Zzz Teste Master Cad", "x", is_master=True, telefone="5565988887777")
        if not models.get_user_by_username("Zzz Teste Admin Cad"):
            models.create_user("Zzz Teste Admin Cad", "x", is_admin=True)
        cls.plano = models.salvar_plano(None, "Zzz Plano Cad", "", 30.0, "MONTHLY", 2, 50, ["clima"], True, 1)
        cls.enviadas = []
        cls.patch_whats = mock.patch.object(
            webapp, "_whatsapp_em_segundo_plano", side_effect=lambda tel, texto: cls.enviadas.append((tel, texto)),
        )
        cls.patch_whats.start()

    @classmethod
    def tearDownClass(cls):
        cls.patch_whats.stop()

    def _logado(self, nome):
        c = webapp.app.test_client()
        with c.session_transaction() as s:
            s["_user_id"] = str(models.get_user_by_username(nome)["id"])
            s["_fresh"] = True
        return c

    def _cadastro(self, username):
        return next((c for c in models.get_cadastros() if c["username"] == username), None)

    def test_links_e_formulario(self):
        c = webapp.app.test_client()
        html = c.get("/solicitar-acesso").get_data(as_text=True)
        for trecho in ("Produtor", "Técnico do setor", 'name="nome_completo"'):
            self.assertIn(trecho, html)
        self.assertNotIn('name="cpf_cnpj"', html)
        self.assertIn("Cadastre-se", c.get("/login").get_data(as_text=True))
        self.assertIn("modalidade=plano", c.get("/cadastro?plano=3").headers["Location"])
        self.assertIn("solicitar-acesso?modalidade=plano", c.get("/planos").get_data(as_text=True))

    def test_sugestao_e_duplicidade_de_usuario(self):
        self.assertEqual(webapp._sugerir_usuario("joão da silva pereira"), "João Pereira")
        models.create_user("Zzz Repetido Teste", "x")
        self.assertEqual(webapp._sugerir_usuario("Zzz Fulano Repetido Teste"), "Zzz Teste")
        self.assertEqual(webapp._sugerir_usuario("Zzz Repetido Teste"), "Zzz Teste")
        models.create_user("Zzz Teste", "x")
        self.assertEqual(webapp._sugerir_usuario("Zzz Fulano Teste"), "Zzz Teste 2")
        r = webapp.app.test_client().get("/solicitar-acesso/usuario?usuario=zzz+teste&nome=Zzz+Fulano+Teste").get_json()
        self.assertEqual(r, {"disponivel": False, "sugestao": "Zzz Teste 2"})
        r = webapp.app.test_client().get("/solicitar-acesso/usuario?usuario=Zzz+Livre+123").get_json()
        self.assertTrue(r["disponivel"])

    def test_validacoes(self):
        casos = {
            "sem perfil": dict(perfil=[]),
            "so um nome": dict(nome_completo="Zzz"),
            "usuario repetido": dict(username="zzz teste master cad"),
            "sem fazenda": dict(empresa=""),
            "sem UF": dict(uf=""),
            "sem cultura": dict(cultura_outra=""),
            "sem area": dict(area_outra=""),
            "cultura marcada sem area": dict(culturas=["Milho"]),
            "senha curta": dict(senha="123", senha_confirmar="123"),
            "senha diferente": dict(senha_confirmar="outra1"),
        }
        for caso, extra in casos.items():
            with self.subTest(caso=caso):
                extra.setdefault("username", f"Zzz Val {caso}")
                resp = webapp.app.test_client().post("/solicitar-acesso", data=_form(**extra))
                self.assertEqual(resp.status_code, 200)
                self.assertFalse(models.username_em_uso(extra["username"]) and caso != "usuario repetido")
        html = webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Teste Master Cad")).get_data(as_text=True)
        self.assertIn("já existe", html)
        # mensagem e' opcional
        resp = webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Sem Mensagem", mensagem="",
                                                                            numero_telefone="65999990008"))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(models.username_em_uso("Zzz Sem Mensagem"))

    def test_sem_pergunta_e_area_por_cultura(self):
        """Sem "O que voce quer?" (cadastro = assinar plano), grupo de cultura
        antes das culturas e area de cada uma (pedidos do Alan, 08/11/2026)."""
        html = webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True)
        self.assertNotIn("O que você quer?", html)
        self.assertIn('<input type="hidden" name="modalidade" value="plano">', html)
        self.assertIn('name="area_cultura__0__Milho"', html)  # campos por local (Local 1 = indice 0)
        for grupo in ('value="cereais"', 'value="hf"', 'value="outros"'):
            self.assertIn(grupo, html)
        self.assertIn('data-grupo="cereais"', html)
        self.assertIn("Escolha o seu plano", html)  # planos ja' no cadastro
        self.assertIn('value="fazenda"', webapp.app.test_client().get("/solicitar-acesso?modalidade=fazenda").get_data(as_text=True))
        # Cereais: preco pela area de Soja; cultura de outro grupo nao entra
        dados = _form(username="Zzz Cad Areas", numero_telefone="65999990031", grupo_cultura="cereais",
                      culturas=["Soja", "Milho", "Batata"], cultura_outra="Sorgo", area_outra="50")
        dados.pop("modalidade")
        dados.update({"area_cultura__Soja": "1.000", "area_cultura__Milho": "300", "area_cultura__Batata": "70"})
        resp = webapp.app.test_client().post("/solicitar-acesso", data=dados)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(models.get_user_by_username("Zzz Cad Areas")["is_cliente"], 1)  # sem a pergunta = assinar plano
        reg = self._cadastro("Zzz Cad Areas")
        self.assertEqual((reg["grupo_cultura"], reg["area_ha"], reg["area_preco_ha"]), ("cereais", 1300.0, 1000.0))
        self.assertEqual(reg["culturas"], "Soja (1.000 ha), Milho (300 ha)")
        # HF: soma das culturas, inclusive "Outra cultura"
        dados = _form(username="Zzz Cad HF", numero_telefone="65999990032", grupo_cultura="hf",
                      culturas=["Batata"], cultura_outra="Tomate", area_outra="20,5")
        dados["area_cultura__Batata"] = "30"
        self.assertEqual(webapp.app.test_client().post("/solicitar-acesso", data=dados).status_code, 302)
        reg = self._cadastro("Zzz Cad HF")
        self.assertEqual((reg["area_ha"], reg["area_preco_ha"], reg["culturas"]), (50.5, 50.5, "Batata (30 ha), Tomate (20,5 ha)"))
        # sem grupo = recusa
        sem = _form(username="Zzz Cad Sem Grupo", numero_telefone="65999990033", grupo_cultura="")
        self.assertEqual(webapp.app.test_client().post("/solicitar-acesso", data=sem).status_code, 200)
        # Gerenciar Clientes: uma linha por cultura com a area dela + tipo
        html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        for linha in ("Soja <span class=\"text-muted\">— 1.000 ha</span>", "Milho <span class=\"text-muted\">— 300 ha</span>"):
            self.assertIn(f'<div class="cultura-area-linha">{linha}</div>', html)
        self.assertIn("Cereais <span class=\"text-muted\">-- área para o preço: 1.000 ha</span>", html)
        self.assertEqual(webapp._culturas_com_area("Soja, Milho (2.000 ha)"), [("Soja", None), ("Milho", "2.000 ha")])

    def test_tecnico_nao_escolhe_plano_e_ve_contato(self):
        """Tecnico do setor: sem planos no cadastro, so' o contato (pedido do Alan, 08/11/2026)."""
        html = webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True)
        self.assertIn('id="contato-tecnico"', html)
        for trecho in ("Alan Mauro", "(43) 99114-7595", "wa.me/5543991147595", "mailto:contato@oneagro.com.br"):
            self.assertIn(trecho, html)
        self.assertNotIn("área para o preço", html)
        self.assertNotIn("o preço usa a área de Soja", html)
        plano = models.get_planos(apenas_ativos=True)[0]
        c = webapp.app.test_client()
        resp = c.post("/solicitar-acesso", data=_form(username="Zzz Cad Tecnico", numero_telefone="65999990061",
                                                      modalidade="plano", perfil=["tecnico"], plano_id=str(plano["id"])),
                      follow_redirects=True)
        self.assertIn("entre em contato com Alan Mauro", resp.get_data(as_text=True))
        user = models.get_user_by_username("Zzz Cad Tecnico")
        self.assertIsNone(models.get_assinatura(user["id"])["plano_id"])  # nenhum plano escolhido

    def test_perfil_unico_e_varios_tipos_de_cultura(self):
        """Voce e': um so' (radio); Tipo de cultura: varios (pedido do Alan, 08/11/2026)."""
        html = webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True)
        self.assertIn('type="radio" name="perfil" id="perfil_produtor"', html)
        self.assertIn('type="checkbox" class="btn-check" name="grupo_cultura" id="grupo_outros"', html)
        dados = _form(username="Zzz Cad Varios", numero_telefone="65999990071", grupo_cultura=["cereais", "outros"],
                      culturas=["Soja", "Milho", "Cana", "Batata"], cultura_outra="Tomate", area_outra="9")
        dados.update({"area_cultura__Soja": "500", "area_cultura__Milho": "200", "area_cultura__Cana": "300",
                      "area_cultura__Batata": "40"})
        self.assertEqual(webapp.app.test_client().post("/solicitar-acesso", data=dados).status_code, 302)
        reg = self._cadastro("Zzz Cad Varios")
        # Batata e Tomate sao HF (nao marcado) -- ficam de fora; preco = Soja + Cana
        self.assertEqual((reg["grupo_cultura"], reg["area_ha"], reg["area_preco_ha"]), ("cereais,outros", 1000.0, 800.0))
        self.assertEqual(reg["culturas"], "Soja (500 ha), Milho (200 ha), Cana (300 ha)")
        self.assertEqual(webapp.rotulo_grupos("cereais,outros"), "Cereais, Outros")
        self.assertEqual(webapp._area_preco_grupos(["cereais", "hf"], [("Milho", 100, "cereais"), ("Uva", 5, "hf"), ("Milho", 7, "hf")]), 112)

    def test_tipo_da_cultura_na_aba_doencas_culturas(self):
        """Tipo escolhido na frente da cultura (aba Doencas/Culturas) manda no cadastro (pedido do Alan, 08/11/2026)."""
        if not models.get_user_by_username("Zzz Teste Master Cad"):
            models.create_user("Zzz Teste Master Cad", "x", is_master=True)
        master = self._logado("Zzz Teste Master Cad")
        html = master.get("/admin/doencas").get_data(as_text=True)
        self.assertIn("<h4 class=\"mb-2\">Doenças/Culturas</h4>", html)
        self.assertIn(">Doenças/Culturas</a>", html)  # menu
        self.assertIn('name="cultura_grupo__0"', html)
        nomes = models.get_culturas()
        antes = models.get_grupos_culturas()
        slot = nomes.index("Feijao")
        dados = {"form_id": "matriz_culturas"}
        for i, n in enumerate(nomes):
            dados[f"cultura_nome__{i}"] = n
            if n:
                dados[f"cultura_grupo__{i}"] = "cereais" if i == slot else webapp._grupo_da_cultura(n)
        try:
            master.post("/admin/doencas", data=dados)
            self.assertEqual(models.get_grupos_culturas()[slot], "cereais")
            with webapp.app.test_request_context():
                self.assertEqual(webapp._grupo_da_cultura("Feijao"), "cereais")
            cad = webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True)
            import re
            self.assertRegex(cad, r'data-grupo="cereais"[^>]*>\s*<div class="form-check mb-0"[^>]*>\s*<input class="form-check-input" type="checkbox" name="culturas__0" id="cultura_0_\d+" value="Feijao"')
        finally:
            models.set_grupos_culturas(antes)

    def test_renomear_doenca_leva_as_anotacoes(self):
        """Nome da doenca editado em Doencas/Culturas vale no site todo, com as
        anotacoes por fazenda (pedido do Alan, 10/11/2026); nome repetido nao vale."""
        master = self._logado("Zzz Teste Master Cad")
        en = models.add_manual_disease("Zzz Doenca Antiga")
        outra = models.add_manual_disease("Zzz Doenca Outra")
        models.save_recommendation_note("Zzz Fazenda Nota", "Zzz Doenca Antiga", "aplicar cedo")
        try:
            def postar(nome):
                info = models.get_all_disease_info()
                ens = list(info)
                dados = {"display_name_en": ens, "nome_cientifico": [info[e]["nome_cientifico"] or "" for e in ens],
                         "nome_pt": [nome if e == en else info[e]["nome_pt"] for e in ens]}
                return master.post("/admin/doencas", headers={"X-Autosave": "1"}, data=dados).get_json()
            self.assertTrue(postar("Zzz Doenca Nova")["ok"])
            self.assertEqual(models.get_all_disease_translations()[en]["nome_pt"], "Zzz Doenca Nova")
            notas = models.get_all_recommendation_notes()
            self.assertEqual(notas.get(("Zzz Fazenda Nota", "Zzz Doenca Nova")), "aplicar cedo")
            self.assertNotIn(("Zzz Fazenda Nota", "Zzz Doenca Antiga"), notas)
            r = postar("Zzz Doenca Outra")
            self.assertFalse(r["ok"])
            self.assertEqual(models.get_all_disease_translations()[en]["nome_pt"], "Zzz Doenca Nova")
        finally:
            models.delete_disease_translation(en)
            models.delete_disease_translation(outra)
            models.save_recommendation_note("Zzz Fazenda Nota", "Zzz Doenca Nova", "")

    def test_doencas_novas_do_bioscout_em_portugues(self):
        """Brown Spot/Purple Seed Stain/White Mould ganham nome em portugues (inclusive
        quem ja estava no banco em ingles); doenca desconhecida avisa o Master (11/11/2026)."""
        import data_reader
        for en in ("White Mould", "Zzz Unknown Blight"):
            models.delete_disease_translation(en)
        conn = models.get_db()
        conn.execute("INSERT INTO disease_translations (display_name_en, nome_pt, nome_cientifico) VALUES (?, ?, ?)",
                     ("White Mould", "White Mould", "Sclerotinia spp. - Ascospore"))
        conn.commit(); conn.close()
        try:
            models.ensure_disease_translations(
                ["White Mould", "Zzz Unknown Blight"], data_reader.DOENCA_MAP,
                {"White Mould": data_reader.cientifico_em_portugues("Sclerotinia spp. - Ascospore")})
            info = models.get_all_disease_translations()
            self.assertEqual(info["White Mould"]["nome_pt"], "Mofo Branco")
            self.assertEqual(info["White Mould"]["nome_cientifico"], "Sclerotinia spp. - ascósporo")
            with webapp.app.test_request_context():
                self.assertIn("Zzz Unknown Blight", webapp.doencas_sem_traducao())
                self.assertNotIn("White Mould", webapp.doencas_sem_traducao())
            html = self._logado("Zzz Teste Master Cad").get("/dashboard").get_data(as_text=True)
            self.assertIn("ainda sem nome em português", html)
            self.assertIn("Zzz Unknown Blight", html)
        finally:
            for en in ("White Mould", "Zzz Unknown Blight"):
                models.delete_disease_translation(en)

    def test_cana_virou_outros_no_banco(self):
        """Cadastro e tipo das culturas gravados como "cana" passam a "outros" no init_db."""
        uid = models.criar_cliente("Zzz Cad Cana Antiga", "segredo1", "", "5565999990081", "", None)
        models.registrar_cadastro({"username": "Zzz Cad Cana Antiga", "tipo": "cliente", "user_id": uid,
                                   "grupo_cultura": "cereais,cana"})
        antes = models.get_grupos_culturas()
        models.set_grupos_culturas(["cereais", "cana"] + [""] * 10)
        try:
            models.init_db()
            self.assertEqual(self._cadastro("Zzz Cad Cana Antiga")["grupo_cultura"], "cereais,outros")
            self.assertEqual(models.get_grupos_culturas()[:2], ["cereais", "outros"])
        finally:
            models.set_grupos_culturas(antes)

    def test_telefone_brasil_no_formato_ddd(self):
        """DDI 55 = "(66)99616-4261" em todo o site; outro DDI fica como era (pedido do Alan, 08/11/2026)."""
        self.assertEqual(models.fmt_telefone_br("5566996164261"), "(66)99616-4261")
        self.assertEqual(models.fmt_telefone_br("556633221100"), "(66)3322-1100")
        self.assertEqual(models.fmt_telefone_br("56912345678"), "56912345678")  # Chile (11 digitos): como estava
        self.assertEqual(models.fmt_telefone_br("5691234567890"), "(56)91-23456-7890")  # outro DDI: formato antigo
        # mascara do campo no cadastro e nas telas internas
        self.assertIn('input[name^="numero_telefone"]', webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True))
        self.assertIn('input[name^="numero_telefone"]', self._logado("Zzz Teste Master Cad").get("/admin/users").get_data(as_text=True))
        # o servidor le so' os digitos do campo formatado
        resp = webapp.app.test_client().post("/solicitar-acesso", data=_form(
            username="Zzz Cad Fone", numero_telefone="(65)99999-0099"))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(models.get_user_by_username("Zzz Cad Fone")["telefone"], "5565999990099")
        html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("(65)99999-0099", html)

    def test_aceite_libera_botao_e_assinatura_so_com_o_plano_e_mapa(self):
        """Criar conta so' com os termos aceitos; tela do CPF so' com o plano
        escolhido e o mapa da area monitorada (pedido do Alan, 08/11/2026)."""
        html = webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True)
        self.assertIn('id="botao-criar" disabled>Criar conta</button>', html)
        planos = [p for p in models.get_planos(apenas_ativos=True) if p["valor"] > 0 and not p["preco_por_area"]]
        if not planos:
            pid = models.salvar_plano(None, "Zzz Plano Mapa", "", 120, "MONTHLY", 1, 30, ["clima", "fazenda_estimada"], True, 9)
        else:
            pid = planos[0]["id"]
        plano = models.get_plano(pid)
        c = webapp.app.test_client()
        resp = c.post("/solicitar-acesso", data=_form(username="Zzz Cad Mapa", numero_telefone="65999990101",
                                                      modalidade="plano", plano_id=str(pid)))
        tela = c.get(resp.headers["Location"]).get_data(as_text=True)
        self.assertIn("Plano escolhido", tela)
        self.assertEqual(tela.count('type="radio" name="plano_id"'), 1)  # so' o plano escolhido
        self.assertIn(f'<span class="plano-nome">{plano["nome"]}</span>', tela)
        self.assertIn("Onde será a fazenda monitorada?", tela)
        self.assertIn('id="mapaArea"', tela)
        self.assertIn('value="Fazenda Zzz"', tela)  # nome da area = fazenda do cadastro
        self.assertIn("Ver todos os planos", tela)
        self.assertGreater(c.get("/app/assinatura").get_data(as_text=True).count('type="radio" name="plano_id"'), 1)
        # sem local: nao segue
        resp = c.post("/app/assinatura/assinar", data={"plano_id": str(pid), "cpf_cnpj": CPF_VALIDO, "pedir_local": "1"},
                      follow_redirects=True)
        self.assertIn("Marque no mapa", resp.get_data(as_text=True))
        user = models.get_user_by_username("Zzz Cad Mapa")
        self.assertEqual(models.get_cliente_pontos(user["id"]), [])
        # coordenadas digitadas (com virgula) viram a 1a lavoura
        c.post("/app/assinatura/assinar", data={"plano_id": str(pid), "cpf_cnpj": CPF_VALIDO, "pedir_local": "1",
                                                "nome_area": "Sede Zzz", "lat": "-15,6010", "lon": "-56.0970"})
        pontos = models.get_cliente_pontos(user["id"])
        self.assertEqual([(p["nome"], round(p["lat"], 4), round(p["lon"], 4)) for p in pontos], [("Sede Zzz", -15.601, -56.097)])
        self.assertNotIn("Onde será a fazenda monitorada?", c.get("/app/assinatura").get_data(as_text=True))  # ja' tem area

    def test_gerenciar_clientes_situacao_e_areas(self):
        """Cartao com a situacao em palavras, formato do relatorio e areas monitoradas (08/11/2026)."""
        plano = models.salvar_plano(None, "Zzz Plano Gerenciar", "", 1200, "MONTHLY", 1, 30,
                                    ["clima", "whatsapp_texto", "whatsapp_pdf"], True, 9)
        free_uid = models.criar_cliente("Zzz Cli Ger Free", "segredo1", "", "5565999990211", "", plano)
        site = models.create_virtual_farm("Zzz Area Ger", -12.5, -55.7, 30)
        models.ligar_ponto_a_fazenda(models.criar_cliente_ponto(free_uid, "Area Ger", -12.5, -55.7), site)
        bloq = models.criar_cliente("Zzz Cli Ger Bloq", "segredo1", "", "5565999990212", "", plano)
        conn = models.get_db()
        conn.execute("UPDATE assinaturas SET criado_em = '2026-01-01 08:00:00' WHERE user_id = ?", (bloq,))
        conn.commit()
        conn.close()
        html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("Usando o Essencial até", html)            # pago aguardando pagamento
        self.assertIn("Bloqueado", html)                    # passou dos 7 dias
        self.assertIn('data-situacao="free"', html)
        self.assertIn('data-situacao="bloqueado"', html)
        self.assertIn('data-filtro="free"', html)
        self.assertIn("Relatório por WhatsApp:</span> texto", html)  # no Free: so' texto
        self.assertIn('<span class="fw-semibold">Zzz Area Ger</span>', html)  # modelo unico (09/11/2026)
        self.assertIn("✏️ Dados da fazenda", html)
        self.assertNotIn(" lavoura", html.split("Gerenciar Clientes</h4>", 1)[1])

    def test_grupo_economico_antes_do_nome_da_fazenda(self):
        """Nome do grupo economico no cadastro vai antes do nome de cada fazenda
        no relatorio: "Grupo Zzz - Sede" (pedido do Alan, 08/11/2026)."""
        dados = _form(username="Zzz Cad Grupo", numero_telefone="65999990251", modalidade="plano", grupo_cultura=["cereais"],
                      grupo_economico="  Grupo   Zzz ")
        for k in ("cultura_outra", "area_outra"):
            dados.pop(k)
        dados.update({"local_idx": ["0"], "local_nome__0": "Sede", "local_cidade__0": "Sinop", "local_uf__0": "MT",
                      "culturas__0": ["Soja"], "area_cultura__0__Soja": "500", "local_lat__0": "-12,5", "local_lon__0": "-55,7"})
        self.assertIn('name="grupo_economico"', webapp.app.test_client().get("/solicitar-acesso").get_data(as_text=True))
        resp = webapp.app.test_client().post("/solicitar-acesso", data=dados)
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True)[-600:])
        self.assertEqual(self._cadastro("Zzz Cad Grupo")["grupo_economico"], "Grupo Zzz")
        uid = models.get_user_by_username("Zzz Cad Grupo")["id"]
        site = models.get_cliente_pontos(uid)[0]["site_name"]
        self.assertEqual(models.get_virtual_farm(site)["nome"], "Grupo Zzz - Sede")
        with webapp.app.test_request_context():
            self.assertEqual(webapp._nome_exibicao(site), "Grupo Zzz - Sede")
        import export_excel
        from openpyxl import load_workbook
        linhas = list(load_workbook(export_excel.build_workbook())["Clientes"].iter_rows(values_only=True))
        linha = dict(zip(linhas[0], next(r for r in linhas if r[1] == "Zzz Cad Grupo")))
        self.assertEqual(linha["Grupo econômico"], "Grupo Zzz")

    def test_varios_locais_e_pessoas_extras(self):
        """Locais de plantio (cada um com culturas/area e ponto no mapa) e ate' 2
        pessoas que recebem o relatorio, escolhendo os locais (pedido do Alan, 08/11/2026)."""
        dados = _form(username="Zzz Cad Locais", numero_telefone="65999990241", modalidade="plano", grupo_cultura=["cereais"])
        for k in ("cultura_outra", "area_outra"):
            dados.pop(k)
        dados.update({
            "local_idx": ["0", "3"],
            "local_cidade__0": "Sinop", "local_uf__0": "MT", "local_cidade__3": "Sorriso", "local_uf__3": "MT",
            "local_nome__0": "Sede", "culturas__0": ["Soja", "Milho"], "area_cultura__0__Soja": "500", "area_cultura__0__Milho": "200",
            "local_lat__0": "-12,50", "local_lon__0": "-55,70",
            "local_nome__3": "Retiro", "culturas__3": ["Soja"], "area_cultura__3__Soja": "300",
            "local_lat__3": "-13.10", "local_lon__3": "-56.20",
            "extra_nome__1": "Zzz Gerente", "codigo_pais__extra1": "55", "numero_telefone__extra1": "(65)99999-0242", "extra_locais__1": ["1"],
            "extra_nome__2": "Zzz Agronomo", "codigo_pais__extra2": "55", "numero_telefone__extra2": "65999990243",
            "extra_locais__2": ["0", "1"],
        })
        resp = webapp.app.test_client().post("/solicitar-acesso", data=dados)
        self.assertEqual(resp.status_code, 302, resp.get_data(as_text=True)[-600:])
        uid = models.get_user_by_username("Zzz Cad Locais")["id"]
        reg = self._cadastro("Zzz Cad Locais")
        self.assertEqual((reg["area_ha"], reg["area_preco_ha"], reg["culturas"]), (1000.0, 800.0, "Soja (800 ha), Milho (200 ha)"))
        pontos = models.get_cliente_pontos(uid)
        self.assertEqual([(p["nome"], p["culturas"], p["area_ha"], p["cidade"], p["uf"]) for p in pontos],
                         [("Sede", "Soja (500 ha), Milho (200 ha)", 700.0, "Sinop", "MT"),
                          ("Retiro", "Soja (300 ha)", 300.0, "Sorriso", "MT")])
        self.assertEqual((reg["empresa"], reg["cidade"], reg["uf"]), ("Sede", "Sinop", "MT"))  # conta = 1a fazenda
        # fazenda sem cidade/UF: nao segue
        sem = dict(dados, username="Zzz Cad Locais 4", numero_telefone="65999990246", local_cidade__3="")
        for k in ("extra_nome__1", "numero_telefone__extra1", "extra_nome__2", "numero_telefone__extra2"):
            sem.pop(k)
        self.assertIn("Informe a cidade e o estado de: Retiro",
                      webapp.app.test_client().post("/solicitar-acesso", data=sem).get_data(as_text=True))
        sede, retiro = pontos[0]["site_name"], pontos[1]["site_name"]
        self.assertTrue(models.get_virtual_farm(sede) and models.get_virtual_farm(retiro))
        recebe = lambda site: sorted(r["username"] for r in models.get_site_whatsapp_recipients(site))
        self.assertEqual(recebe(sede), ["Zzz Agronomo", "Zzz Cad Locais"])
        self.assertEqual(recebe(retiro), ["Zzz Agronomo", "Zzz Cad Locais", "Zzz Gerente"])
        # Safras ja' preenchidas pelas culturas de maior area de cada local (pedido do Alan, 08/11/2026)
        fc = models.get_all_farm_culturas()
        self.assertEqual([fc.get((sede, sf), {}).get("cultura") for sf, _ in models.SAFRAS], ["Soja", "Milho", None])
        self.assertEqual([fc.get((retiro, sf), {}).get("cultura") for sf, _ in models.SAFRAS], ["Soja", None, None])
        # integracao: Gerenciar Clientes, app (Culturas) e Excel
        adm = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("Também recebem o relatório", adm)
        self.assertIn("Zzz Gerente · (65)99999-0242 <span class=\"text-muted\">-- Retiro</span>", adm)
        self.assertIn("Soja (500 ha), Milho (200 ha) · 700 ha", adm)
        import export_excel
        from openpyxl import load_workbook
        wb = load_workbook(export_excel.build_workbook())
        linhas = list(wb["Clientes"].iter_rows(values_only=True))
        linha = dict(zip(linhas[0], next(r for r in linhas if r[1] == "Zzz Cad Locais")))
        self.assertEqual(linha["Locais de plantio"], "Sede (Sinop/MT): Soja (500 ha), Milho (200 ha); Retiro (Sorriso/MT): Soja (300 ha)")
        self.assertIn("Zzz Gerente (65)99999-0242 -- Retiro", linha["Também recebem o relatório"])
        # cliente troca quem recebe pelo app
        models.atualizar_assinatura(uid, plano_id=models.plano_free()["id"], status="ativa", cortesia_ate="2099-01-01")
        c = self._logado("Zzz Cad Locais")
        home = c.get("/app").get_data(as_text=True)
        self.assertIn("Receber relatório", home)
        self.assertIn('value="Zzz Gerente"', home)
        c.post("/app/destinatarios", data={"extra_nome__1": "Zzz Novo", "codigo_pais__extra1": "55",
                                           "numero_telefone__extra1": "65999990249", "extra_pontos__1": [str(pontos[0]["id"])]})
        self.assertEqual([s["nome"] for s in models.get_owner_subordinados(uid)], ["Zzz Novo"])
        # depois de salvar volta pra tela anterior (pedido do Alan, 08/11/2026); link externo nao
        home = c.get("/app", headers={"Referer": "http://localhost/app/regiao"}).get_data(as_text=True)
        self.assertIn('name="voltar" value="/app/regiao"', home)
        dados_dest = {"extra_nome__1": "Zzz Novo", "codigo_pais__extra1": "55", "numero_telefone__extra1": "65999990249",
                      "extra_pontos__1": [str(pontos[0]["id"])]}
        r = c.post("/app/destinatarios", data=dict(dados_dest, voltar="/app/regiao"))
        self.assertTrue(r.headers["Location"].endswith("/app/regiao"))
        r = c.post("/app/destinatarios", data=dict(dados_dest, voltar="https://malicioso.example/x"))
        self.assertTrue(r.headers["Location"].endswith("/app"))
        r = c.post("/app/destinatarios", data=dict(dados_dest, voltar="//malicioso.example/x"))
        self.assertTrue(r.headers["Location"].endswith("/app"))
        self.assertEqual(recebe(sede), ["Zzz Cad Locais", "Zzz Novo"])
        self.assertEqual(recebe(retiro), ["Zzz Cad Locais"])
        # aba Conta salva sozinha (pedido do Alan, 08/11/2026): JSON, sem botao
        self.assertNotIn("Salvar quem recebe", home)
        r = c.post("/app/destinatarios", headers={"X-Autosave": "1"},
                   data={"extra_nome__1": "Zzz Auto", "codigo_pais__extra1": "55", "numero_telefone__extra1": "65999990250"})
        self.assertEqual(r.get_json(), {"ok": True})
        self.assertEqual([s["nome"] for s in models.get_owner_subordinados(uid)], ["Zzz Auto"])
        r = c.post("/app/destinatarios", headers={"X-Autosave": "1"}, data={"extra_nome__1": "Zzz Pela Metade"})
        self.assertFalse(r.get_json()["ok"])
        self.assertEqual([s["nome"] for s in models.get_owner_subordinados(uid)], ["Zzz Auto"])  # nao apaga
        # equipe inclui mais subordinados na aba Usuarios (sem limite); o app nao apaga (10/11/2026)
        usuarios = self._logado("Zzz Teste Master Cad").get("/admin/users").get_data(as_text=True)
        self.assertIn("Zzz Cad Locais", usuarios)  # cliente do app aparece na aba Usuarios
        self.assertIn("Cliente do app", usuarios)
        self.assertIn("<th>Grupo</th>", usuarios)  # coluna Grupo entre Usuario e Email (10/11/2026)
        with webapp.app.test_request_context():
            self.assertEqual(webapp._nome_sem_grupo("x"), "x")
        r = self._logado("Zzz Teste Master Cad").post(f"/admin/users/{uid}/subordinados", data={
            "form_id": "add", "nome": "Zzz Pela Equipe", "codigo_pais": "55", "numero_telefone": "65999990260"})
        self.assertEqual(r.status_code, 302)
        c.post("/app/destinatarios", headers={"X-Autosave": "1"},
               data={"extra_nome__1": "Zzz Auto 2", "codigo_pais__extra1": "55", "numero_telefone__extra1": "65999990251"})
        self.assertEqual(sorted(s["nome"] for s in models.get_owner_subordinados(uid)), ["Zzz Auto 2", "Zzz Pela Equipe"])
        self.assertNotIn("Zzz Pela Equipe", c.get("/app").get_data(as_text=True))  # app mostra so' os 2 dele
        # senha: conferida enquanto digita, botao mostrar senha
        self.assertIn('class="btn btn-outline-secondary mostrar-senha"', home)
        self.assertTrue(c.post("/app/conta/conferir-senha", data={"senha": dados["senha"]}).get_json()["ok"])
        self.assertFalse(c.post("/app/conta/conferir-senha", data={"senha": "errada"}).get_json()["ok"])
        # local sem coordenada: nao segue
        ruim = dict(dados, username="Zzz Cad Locais 2", numero_telefone="65999990244", local_lat__3="")
        ruim.pop("extra_nome__1"); ruim.pop("numero_telefone__extra1"); ruim.pop("extra_nome__2"); ruim.pop("numero_telefone__extra2")
        html = webapp.app.test_client().post("/solicitar-acesso", data=ruim).get_data(as_text=True)
        self.assertIn("Marque no mapa (ou digite as coordenadas) de: Retiro", html)
        # pessoa extra com celular invalido: nao segue
        ruim2 = dict(dados, username="Zzz Cad Locais 3", numero_telefone="65999990245", numero_telefone__extra1="123")
        self.assertIn("Contato 1 que recebe o relatório", webapp.app.test_client().post("/solicitar-acesso", data=ruim2).get_data(as_text=True))

    def test_grupo_da_cultura(self):
        self.assertEqual([webapp._grupo_da_cultura(c) for c in ("Soja", "Milho", "Algodão", "Algodao", "Cana", "Cana-de-açúcar", "Feijao", "Batata")],
                         ["cereais", "cereais", "cereais", "cereais", "outros", "outros", "hf", "hf"])
        self.assertEqual(webapp._area_para_preco("cereais", [("Soja", 800), ("Milho", 200)]), 800)
        self.assertEqual(webapp._area_para_preco("cereais", [("Milho", 200), ("Algodao", 100)]), 300)  # sem Soja: soma
        self.assertEqual(webapp._area_para_preco("hf", [("Batata", 10), ("Uva", 5)]), 15)
        self.assertEqual(webapp._area_para_preco("outros", [("Cana", 900)]), 900)

    def test_plano_premium_pelo_preco_da_area(self):
        """Premium: total = area x U$/ha da faixa; parcelas pelo ciclo; sem valor na faixa = sob consulta."""
        import json
        import cotacao
        premium = next(p for p in models.get_planos() if p["nome"] == "Avançado")
        self.assertTrue(premium["preco_por_area"])  # ja' nasce ligado no Premium
        models.set_setting("tabela_precificacao", json.dumps([{"ate_ha": 1000, "usd_ha": 1.2}]))
        try:
            with mock.patch.object(cotacao, "dolar", return_value={"valor": 5.0, "data": "2026-11-06"}):
                p = webapp._plano_para_area(premium, 800)
                self.assertEqual((p["valor"], p["moeda"], p["parcelas"], p["valor_parcela"], p["sob_consulta"]),
                                 (960.0, "USD", 12, 80.0, False))
                self.assertTrue(webapp._plano_para_area(premium, 5000)["sob_consulta"])  # faixa ate' 5.000 sem U$/ha
                self.assertTrue(webapp._plano_para_area(premium, None)["sob_consulta"])
                # cliente cereais com 800 ha de soja ve o Premium com o preco dele em Minha assinatura
                models.salvar_plano(premium["id"], "Avançado", "", 0, "MONTHLY", 1, 50, premium["recursos"], True, 3,
                                    preco_por_area=True)
                c = webapp.app.test_client()
                dados = _form(username="Zzz Cad Premium", numero_telefone="65999990034", grupo_cultura="cereais",
                              culturas=["Soja", "Milho"], modalidade="plano", plano_id=str(premium["id"]))
                dados.update({"area_cultura__Soja": "800", "area_cultura__Milho": "400"})
                resp = c.post("/solicitar-acesso", data=dados)
                self.assertIn(f"/app/assinatura?plano={premium['id']}", resp.headers["Location"])
                tela = c.get(resp.headers["Location"]).get_data(as_text=True)
                self.assertIn('<span class="preco-n">12x de</span> U$ 80,00</div>', tela)
                self.assertIn('<div class="preco-sub">≈ R$ 400,00 por parcela</div>', tela)
                # area plantada total 1.200 ha: U$ 960 x 5,00 / 1.200 = R$ 4,00 por ha/ano
                self.assertIn('<div class="preco-ha">R$ 4,00 por ha/ano</div>', tela)
                self.assertIn("Preço pela sua área: 800 ha (até 1.000 ha).", tela)
                import re
                self.assertRegex(tela, rf'value="{premium["id"]}"\s+data-gratis="0"\s+checked')  # ja' vem marcado
                with mock.patch.object(webapp.asaas, "configurado", return_value=True), \
                        mock.patch.object(webapp.asaas, "criar_cliente", return_value="cus_p"), \
                        mock.patch.object(webapp.asaas, "criar_assinatura", return_value="sub_p") as criar, \
                        mock.patch.object(webapp.asaas, "pagamentos_da_assinatura", return_value=[]):
                    c.post("/app/assinatura/assinar", data={"plano_id": str(premium["id"]), "cpf_cnpj": CPF_VALIDO})
                self.assertEqual(criar.call_args[0][1], 400.0)  # parcela U$ 80 x 5,00
                # Gerenciar Clientes: mesmo R$ por ha/ano (U$ 960 x 5,00 / 1.200 ha)
                adm = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
                self.assertIn("R$ 4,00 por ha/ano</span>", adm)
        finally:
            models.set_setting("tabela_precificacao", "")
            models.salvar_plano(premium["id"], premium["nome"], premium["descricao"], premium["valor"], premium["ciclo"],
                                premium["max_pontos"], premium["raio_km"], premium["recursos"], premium["ativo"],
                                premium["ordem"], premium["moeda"], premium["preco_por_area"])

    def test_acesso_a_fazenda_cria_usuario_sem_fazendas(self):
        self.enviadas.clear()
        c = webapp.app.test_client()
        resp = c.post("/solicitar-acesso", data=_form(username="Zzz Cad Fazenda", perfil=["produtor", "tecnico"]))
        self.assertEqual(resp.status_code, 302)
        user = models.get_user_by_username("Zzz Cad Fazenda")
        self.assertEqual((user["is_admin"], user["is_cliente"], user["telefone"]), (0, 0, "5565999990001"))
        self.assertFalse(models.get_user_permitted_site_ids(user["id"]))
        self.assertEqual(c.get("/fazendas").status_code, 200)  # ja' entrou logado
        reg = self._cadastro("Zzz Cad Fazenda")
        self.assertEqual((reg["tipo"], reg["perfil"], reg["area_ha"], reg["nome_completo"]),
                         ("usuario", "produtor", 1500.0, "Zzz Teste da Silva Cadastro"))  # um perfil so'

        aviso = [t for tel, t in self.enviadas if tel == "5565988887777"]
        self.assertEqual(len(aviso), 1)
        self.assertIn("Zzz Cad Fazenda", aviso[0])
        login = webapp.app.test_client().post("/login", data={"username": "Zzz Cad Fazenda", "password": "segredo1"})
        self.assertEqual(login.status_code, 302)

    def test_assinar_plano_cria_cliente_e_pede_cpf_na_assinatura(self):
        c = webapp.app.test_client()
        resp = c.post("/solicitar-acesso", data=_form(username="Zzz Cad Plano", modalidade="plano", numero_telefone="65999990002"))
        self.assertIn("/app/assinatura", resp.headers["Location"])
        user = models.get_user_by_username("Zzz Cad Plano")
        self.assertEqual(user["is_cliente"], 1)
        assinatura = models.get_assinatura(user["id"])
        self.assertEqual((assinatura["cpf_cnpj"], assinatura["plano_id"]), ("", None))
        self.assertIn('name="cpf_cnpj"', c.get("/app/assinatura").get_data(as_text=True))

        sem_cpf = c.post("/app/assinatura/assinar", data={"plano_id": str(self.plano)}, follow_redirects=True)
        self.assertIn("CPF ou CNPJ válido", sem_cpf.get_data(as_text=True))
        c.post("/app/assinatura/assinar", data={"plano_id": str(self.plano), "cpf_cnpj": "529.982.247-25"})
        assinatura = models.get_assinatura(user["id"])
        self.assertEqual((assinatura["cpf_cnpj"], assinatura["plano_id"]), (CPF_VALIDO, self.plano))
        self.assertNotIn('name="cpf_cnpj"', c.get("/app/assinatura").get_data(as_text=True))

    def test_tela_novos_cadastros_do_master(self):
        webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Cad Tela", numero_telefone="65999990003",
                                                                     email="tela@t.com"))
        self.assertEqual(self._logado("Zzz Teste Admin Cad").get("/admin/solicitacoes").status_code, 403)
        master = self._logado("Zzz Teste Master Cad")
        self.assertIn("badge bg-danger", master.get("/mapa").get_data(as_text=True))
        html = master.get("/admin/solicitacoes").get_data(as_text=True)
        for trecho in ("Zzz Cad Tela", "Novo", "wa.me/5565999990003", "mailto:tela@t.com", "Liberar fazendas", "Fazenda Zzz"):
            self.assertIn(trecho, html)
        self.assertEqual(models.contar_cadastros_nao_vistos(), 0)  # abrir a tela marca como visto
        self.assertNotIn(">Novo<", master.get("/admin/solicitacoes").get_data(as_text=True))

    def test_clientes_e_cadastros_numa_aba_so(self):
        """Novos cadastros + Clientes do app viraram "Gerenciar Clientes";
        Planos so' edita os planos (pedido do Alan, 01/11/2026)."""
        webapp.app.test_client().post("/solicitar-acesso", data=_form(
            username="Zzz Cad Cliente Junto", modalidade="plano", numero_telefone="65999990013"))
        antigo = models.criar_cliente("Zzz Cliente Antigo", "segredo1", "", "5565999990014", "", self.plano)
        master = self._logado("Zzz Teste Master Cad")
        html = master.get("/admin/solicitacoes").get_data(as_text=True)
        self.assertIn("<h4 class=\"mb-2\">Gerenciar Clientes</h4>", html)
        self.assertEqual(html.count("<dd>Zzz Cad Cliente Junto</dd>"), 1)  # uma vez so', com a assinatura junto
        self.assertIn("Zzz Cliente Antigo", html)  # cliente sem registro de cadastro tambem aparece
        self.assertIn("Zzz Plano Cad", html)  # plano do cliente antigo
        self.assertIn("<strong>12x de R$ 2,50</strong>", html)  # plano mensal de R$ 30,00 no total
        self.assertIn("(total R$ 30,00)", html)
        self.assertIn("Cortesia até", html)
        self.assertIn('data-filtro="cliente"', html)
        self.assertIn('class="btn btn-link text-decoration-none p-0 fw-bold cad-toggle"', html)  # recolhido
        self.assertIn('class="d-none mt-3" id="cad-body-p', html)
        self.assertNotIn("Todas as contas criadas", html)  # texto do topo saiu
        self.assertIn(">Gerenciar Clientes", html)  # menu
        self.assertIn(">Planos e Valores</a>", html)
        planos = master.get("/admin/planos").get_data(as_text=True)
        self.assertIn("<h4 class=\"mb-2\">Planos e Valores</h4>", planos)
        self.assertNotIn("Zzz Cliente Antigo", planos)
        self.assertNotIn("Cortesia até", planos)
        # acoes de cliente voltam pra Gerenciar Clientes
        resp = master.post(f"/admin/clientes/{antigo}/cortesia", data={"cortesia_ate": "2030-01-01", "plano_id": ""})
        self.assertTrue(resp.headers["Location"].endswith("/admin/solicitacoes"))

    def test_preco_pela_area_no_cartao_do_cliente(self):
        """Area do cadastro busca a faixa da tabela de apoio (1.500 ha -> ate' 2.000)."""
        import json
        import cotacao
        webapp.app.test_client().post("/solicitar-acesso", data=_form(
            username="Zzz Cad Preco Area", modalidade="plano", numero_telefone="65999990021"))
        models.set_setting("tabela_precificacao", json.dumps([{"ate_ha": 2000, "usd_ha": 0.4}]))
        try:
            with mock.patch.object(cotacao, "dolar", return_value={"valor": 5.0, "data": "2026-11-06"}):
                html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        finally:
            models.set_setting("tabela_precificacao", "")
        self.assertIn("<dt>Preço pela área</dt>", html)
        self.assertIn("<strong>U$ 600,00</strong> (≈ R$ 3.000,00)", html)
        self.assertIn("até 2.000 ha, U$ 0,40/ha", html)

    # Mesma pessoa nao faz 2o cadastro; conta excluida some de Novos
    # cadastros (pedidos do Alan, 01/11/2026).
    def test_segundo_cadastro_barrado(self):
        c = webapp.app.test_client()
        c.post("/solicitar-acesso", data=_form(username="Zzz Cad Dup", numero_telefone="65999990009", email="dup@t.com"))
        self.assertIsNotNone(models.get_user_by_username("Zzz Cad Dup"))
        for extra, aviso in ((dict(numero_telefone="65999990009"), "Este celular já está cadastrado"),
                             # sem o 55 na frente / outro usuario, mesmo celular
                             (dict(codigo_pais="", numero_telefone="5565999990009"), "Este celular já está cadastrado"),
                             (dict(numero_telefone="65999990010", email="DUP@t.com"), "Este e-mail já está cadastrado")):
            with self.subTest(extra=extra):
                resp = webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Cad Dup Dois", **extra))
                self.assertEqual(resp.status_code, 200)
                self.assertIn(aviso, resp.get_data(as_text=True))
                self.assertIsNone(models.get_user_by_username("Zzz Cad Dup Dois"))
        self.assertEqual(sum(1 for c in models.get_cadastros() if c["username"].startswith("Zzz Cad Dup")), 1)

    def test_conta_excluida_sai_de_novos_cadastros(self):
        webapp.app.test_client().post("/solicitar-acesso", data=_form(username="Zzz Cad Excluir", numero_telefone="65999990011"))
        user = models.get_user_by_username("Zzz Cad Excluir")
        self.assertIsNotNone(self._cadastro("Zzz Cad Excluir"))
        models.delete_user(user["id"])
        self.assertIsNone(self._cadastro("Zzz Cad Excluir"))
        # registro que ficou de antes da regra (conta apagada por outro caminho)
        models.registrar_cadastro({"username": "Zzz Cad Orfao", "nome_completo": "Zzz Orfao", "perfil": "produtor",
                                   "telefone": "5565999990012", "tipo": "usuario", "user_id": 999999})
        html = self._logado("Zzz Teste Master Cad").get("/admin/solicitacoes").get_data(as_text=True)
        self.assertNotIn("Zzz Cad Orfao", html)
        self.assertNotIn("conta excluída", html)


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
