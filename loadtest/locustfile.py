"""Teste de carga do OneAgro Monitor (Fase 0 do plano de migracao, ver
CLAUDE.md). Simula pessoas logadas navegando pelas telas mais usadas.

Rodar SEMPRE contra o staging ou um servidor local -- nunca contra
producao (as telas consultam servicos externos e o banco de verdade):

    pip install locust
    CARGA_USUARIO="Zzz Carga" CARGA_SENHA="..." \\
        locust -f loadtest/locustfile.py --host http://127.0.0.1:8000 \\
        --headless -u 100 -r 10 -t 2m --csv loadtest/resultado

`CARGA_USUARIO` precisa ser um usuario descartavel (Administrador ve
todas as fazendas, o caso mais pesado). Resultado em
loadtest/resultado_stats.csv (tempo medio, p95, falhas por tela).
"""
import os

from locust import HttpUser, between, task

USUARIO = os.environ.get("CARGA_USUARIO", "Zzz Carga")
SENHA = os.environ.get("CARGA_SENHA", "")


class PessoaNavegando(HttpUser):
    wait_time = between(2, 6)  # tempo lendo a tela antes do proximo clique

    def on_start(self):
        resp = self.client.post("/login", data={"username": USUARIO, "password": SENHA}, allow_redirects=False)
        if resp.status_code != 302:
            raise RuntimeError(f"login falhou ({resp.status_code}) -- confira CARGA_USUARIO/CARGA_SENHA")

    @task(5)
    def recomendacoes(self):
        self.client.get("/recommendations", name="Recomendações")

    @task(4)
    def mapa(self):
        self.client.get("/mapa", name="Mapa")

    @task(3)
    def alertas(self):
        self.client.get("/dashboard", name="Alertas")

    @task(3)
    def manejos(self):
        self.client.get("/fazendas", name="Manejos")

    @task(2)
    def graficos(self):
        self.client.get("/graficos", name="Gráficos")
        self.client.get("/graficos/dados", name="Gráficos (dados)")
