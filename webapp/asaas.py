"""Cobranca recorrente das assinaturas do app via Asaas (Pix, boleto ou
cartao -- `billingType=UNDEFINED` deixa o cliente escolher na fatura).

Configuracao (variaveis de ambiente no Railway):
- ASAAS_API_KEY: chave da conta (Asaas -> Integracoes -> Chave de API).
- ASAAS_AMBIENTE: "producao" para cobrar de verdade; qualquer outro valor
  (ou vazio) usa o sandbox do Asaas, que nao cobra nada.
- ASAAS_WEBHOOK_TOKEN: o mesmo "Token de autenticacao" cadastrado no
  webhook do Asaas (URL: <site>/webhooks/asaas). Sem ele o webhook recusa
  tudo -- senao qualquer um poderia "confirmar" um pagamento.

O app nao guarda cartao nem dado de pagamento -- so' os ids do Asaas.
"""
import json
import os
import urllib.error
import urllib.request

TIMEOUT_S = 20


class AsaasErro(Exception):
    pass


def configurado():
    return bool(os.environ.get("ASAAS_API_KEY"))


def token_webhook():
    return os.environ.get("ASAAS_WEBHOOK_TOKEN") or ""


def _base_url():
    if os.environ.get("ASAAS_AMBIENTE", "").lower() == "producao":
        return "https://api.asaas.com/v3"
    return "https://sandbox.asaas.com/api/v3"


def _chamar(metodo, caminho, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    req = urllib.request.Request(
        _base_url() + caminho, data=dados, method=metodo,
        headers={
            "access_token": os.environ.get("ASAAS_API_KEY", ""),
            "Content-Type": "application/json",
            "User-Agent": "OneAgroMonitor",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            erros = json.loads(e.read() or b"{}").get("errors") or []
            detalhe = "; ".join(err.get("description", "") for err in erros) or str(e)
        except ValueError:
            detalhe = str(e)
        raise AsaasErro(detalhe) from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AsaasErro(f"Asaas indisponivel: {e}") from e


def criar_cliente(nome, cpf_cnpj, email, telefone, referencia):
    resposta = _chamar("POST", "/customers", {
        "name": nome, "cpfCnpj": cpf_cnpj, "email": email or None,
        "mobilePhone": telefone or None, "externalReference": referencia,
        "notificationDisabled": False,
    })
    return resposta["id"]


def criar_assinatura(customer_id, valor, ciclo, descricao, vencimento_iso, referencia):
    resposta = _chamar("POST", "/subscriptions", {
        "customer": customer_id, "billingType": "UNDEFINED", "value": round(float(valor), 2),
        "nextDueDate": vencimento_iso, "cycle": ciclo, "description": descricao,
        "externalReference": referencia,
    })
    return resposta["id"]


def pagamentos_da_assinatura(subscription_id):
    resposta = _chamar("GET", f"/payments?subscription={subscription_id}&limit=20")
    return resposta.get("data") or []


def cancelar_assinatura(subscription_id):
    _chamar("DELETE", f"/subscriptions/{subscription_id}")
