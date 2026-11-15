#!/usr/bin/env python3
"""Cobranças no Asaas para a venda de sites.

Só usa a biblioteca padrão do Python. A chave nunca fica neste arquivo:
- na sessão do Claude na nuvem (ambiente "sites"), a rede injeta a chave sozinha;
- no computador, defina a variável de ambiente ASAAS_API_KEY com a chave.

Nenhum comando cria nada no Asaas sem --confirmar: sem ele, só mostra o que faria.

Exemplos:
  python3 asaas.py status
  python3 asaas.py cliente --nome "Tarrafas" --doc 12345678000190 --whatsapp 79999999999 --confirmar
  python3 asaas.py entrada --doc 12345678000190 --confirmar
  python3 asaas.py restante --doc 12345678000190 --confirmar
  python3 asaas.py mensalidade --doc 12345678000190 --confirmar
  python3 asaas.py pagamentos --doc 12345678000190
  python3 asaas.py resumo
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("ASAAS_BASE_URL", "https://api.asaas.com/v3")

PRECO_SITE = 289.00     # valor cheio do site
PISO_SITE = 231.00      # menor valor com desconto; abaixo disso só com o Josué
MENSALIDADE = 45.00     # manutenção mensal, sem desconto
DIAS_VENCIMENTO = 2     # prazo padrão de cada cobrança avulsa
DIAS_1A_MENSALIDADE = 30  # 1ª mensalidade vence 30 dias depois do site no ar

STATUS_PT = {
    "PENDING": "aguardando",
    "RECEIVED": "pago",
    "CONFIRMED": "pago (confirmado)",
    "RECEIVED_IN_CASH": "pago em dinheiro",
    "OVERDUE": "vencido",
    "REFUNDED": "estornado",
    "REFUND_REQUESTED": "estorno pedido",
    "CHARGEBACK_REQUESTED": "contestação",
    "AWAITING_RISK_ANALYSIS": "em análise",
}
PAGOS = ("RECEIVED", "CONFIRMED", "RECEIVED_IN_CASH")


# ---------- acesso à API ----------

def api(method, path, body=None, params=None, falhar=True):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"User-Agent": "sites-cobranca", "Content-Type": "application/json"}
    key = os.environ.get("ASAAS_API_KEY", "").strip()
    if key:
        headers["access_token"] = key
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
        except Exception:
            err = {}
        msg = "; ".join(x.get("description", "") for x in err.get("errors", [])) or str(e)
        if not falhar:
            return None
        if e.code == 401:
            msg += " (chave ausente, errada ou de outro ambiente)"
        raise SystemExit(f"Erro do Asaas ({e.code}): {msg}")


def listar(path, params):
    """Percorre todas as páginas de uma listagem."""
    itens, offset = [], 0
    while True:
        p = dict(params, limit=100, offset=offset)
        r = api("GET", path, params=p)
        itens += r.get("data", [])
        if not r.get("hasMore"):
            return itens
        offset += 100


# ---------- utilidades ----------

def so_digitos(s):
    return re.sub(r"\D", "", s or "")


def doc_valido(doc):
    def dv(nums, pesos):
        s = sum(int(n) * p for n, p in zip(nums, pesos)) % 11
        return "0" if s < 2 else str(11 - s)
    if len(doc) == 11 and len(set(doc)) > 1:
        d1 = dv(doc[:9], range(10, 1, -1))
        d2 = dv(doc[:10], range(11, 1, -1))
        return doc[9:] == d1 + d2
    if len(doc) == 14 and len(set(doc)) > 1:
        p1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
        p2 = [6] + p1
        return doc[12:] == dv(doc[:12], p1) + dv(doc[:13], p2)
    return False


def telefone(s):
    t = so_digitos(s)
    if len(t) in (12, 13) and t.startswith("55"):
        t = t[2:]
    return t


def reais(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def hoje():
    return dt.date.today()


def data_br(iso):
    if not iso:
        return "-"
    return dt.date.fromisoformat(iso[:10]).strftime("%d/%m/%Y")


def mostrar(obj):
    print(json.dumps(obj, ensure_ascii=False, indent=2))


# ---------- clientes ----------

def achar_cliente(doc=None, cliente_id=None):
    if cliente_id:
        return api("GET", f"/customers/{cliente_id}")
    doc = so_digitos(doc)
    r = api("GET", "/customers", params={"cpfCnpj": doc})
    ativos = [c for c in r.get("data", []) if not c.get("deleted")]
    return ativos[0] if ativos else None


def cliente_obrigatorio(a):
    c = achar_cliente(a.doc, a.cliente)
    if not c:
        raise SystemExit("Cliente não encontrado. Cadastre antes com o comando 'cliente'.")
    return c


def cmd_cliente(a):
    doc = so_digitos(a.doc)
    if not doc_valido(doc):
        raise SystemExit(f"CPF/CNPJ inválido: {a.doc}")
    existente = achar_cliente(doc)
    if existente:
        print(f"Cliente já cadastrado: {existente['name']} (id {existente['id']})")
        return existente
    corpo = {"name": a.nome, "cpfCnpj": doc, "notificationDisabled": False}
    if a.whatsapp:
        corpo["mobilePhone"] = telefone(a.whatsapp)
    if a.email:
        corpo["email"] = a.email.strip()
    if a.empresa:
        corpo["company"] = a.empresa
    if not a.confirmar:
        print("Simulação (nada foi criado). Para criar, repita com --confirmar:")
        mostrar(corpo)
        return None
    c = api("POST", "/customers", corpo)
    print(f"Cliente cadastrado: {c['name']} (id {c['id']})")
    return c


# ---------- cobranças ----------

def criar_cobranca(cliente, valor, descricao, vencimento, ref, tipo, confirmar):
    corpo = {
        "customer": cliente["id"],
        "billingType": tipo,
        "value": round(valor, 2),
        "dueDate": vencimento.isoformat(),
        "description": descricao,
        "externalReference": ref,
    }
    if not confirmar:
        print("Simulação (nada foi criado). Para criar, repita com --confirmar:")
        mostrar(dict(corpo, cliente=cliente["name"]))
        return None
    p = api("POST", "/payments", corpo)
    print(f"Cobrança criada: {reais(p['value'])} para {cliente['name']}, vence {data_br(p['dueDate'])}")
    print(f"Link de pagamento (Pix, boleto ou cartão): {p.get('invoiceUrl')}")
    pix = api("GET", f"/payments/{p['id']}/pixQrCode", falhar=False)
    if pix and pix.get("payload"):
        print(f"Pix copia e cola: {pix['payload']}")
    print(f"id: {p['id']}")
    return p


def valor_total(a):
    total = a.valor_total
    if total < PISO_SITE and not a.abaixo_do_piso:
        raise SystemExit(
            f"{reais(total)} está abaixo do piso de {reais(PISO_SITE)}. "
            "Só com autorização do Josué (use --abaixo-do-piso)."
        )
    return total


def ja_existe(cliente, ref):
    r = api("GET", "/payments", params={"customer": cliente["id"], "externalReference": ref})
    vivos = [p for p in r.get("data", []) if not p.get("deleted") and p["status"] != "REFUNDED"]
    return vivos[0] if vivos else None


def nome_curto(cliente, a):
    return a.empresa or cliente.get("company") or cliente["name"]


def cmd_entrada(a, parte="entrada"):
    c = cliente_obrigatorio(a)
    total = valor_total(a)
    entrada = round(total / 2, 2)
    valor = entrada if parte == "entrada" else round(total - entrada, 2)
    ref = f"site-{parte}"
    anterior = ja_existe(c, ref)
    if anterior and not a.repetir:
        print(f"Já existe a cobrança de {parte} deste cliente ({STATUS_PT.get(anterior['status'], anterior['status'])}).")
        print(f"Link: {anterior.get('invoiceUrl')}")
        print("Para criar outra mesmo assim, use --repetir.")
        return anterior
    nome = nome_curto(c, a)
    if parte == "entrada":
        desc = f"Site {nome}: entrada de 50% para iniciar os ajustes (total {reais(total)})"
    else:
        desc = f"Site {nome}: restante de 50% para publicar no domínio (total {reais(total)})"
    venc = hoje() + dt.timedelta(days=a.dias)
    return criar_cobranca(c, valor, desc, venc, ref, a.tipo, a.confirmar)


def cmd_restante(a):
    return cmd_entrada(a, parte="restante")


def cmd_integral(a):
    c = cliente_obrigatorio(a)
    total = valor_total(a)
    nome = nome_curto(c, a)
    desc = f"Site {nome}: valor integral"
    venc = hoje() + dt.timedelta(days=a.dias)
    return criar_cobranca(c, total, desc, venc, "site-integral", a.tipo, a.confirmar)


def cmd_cobrar(a):
    c = cliente_obrigatorio(a)
    venc = hoje() + dt.timedelta(days=a.dias)
    return criar_cobranca(c, a.valor, a.descricao, venc, a.ref or "avulsa", a.tipo, a.confirmar)


def cmd_mensalidade(a):
    c = cliente_obrigatorio(a)
    r = api("GET", "/subscriptions", params={"customer": c["id"]})
    ativas = [s for s in r.get("data", []) if s.get("status") == "ACTIVE" and not s.get("deleted")]
    if ativas and not a.repetir:
        s = ativas[0]
        print(f"Já existe mensalidade ativa: {reais(s['value'])}, próxima em {data_br(s['nextDueDate'])}.")
        return s
    inicio = dt.date.fromisoformat(a.inicio) if a.inicio else hoje() + dt.timedelta(days=DIAS_1A_MENSALIDADE)
    corpo = {
        "customer": c["id"],
        "billingType": a.tipo,
        "value": MENSALIDADE,
        "nextDueDate": inicio.isoformat(),
        "cycle": "MONTHLY",
        "description": f"Site {nome_curto(c, a)}: manutenção mensal (hospedagem, domínio e ajustes)",
        "externalReference": "site-mensalidade",
    }
    if not a.confirmar:
        print("Simulação (nada foi criado). Para criar, repita com --confirmar:")
        mostrar(dict(corpo, cliente=c["name"]))
        return None
    s = api("POST", "/subscriptions", corpo)
    print(f"Mensalidade criada: {reais(s['value'])}/mês para {c['name']}, 1ª em {data_br(s['nextDueDate'])}.")
    print(f"id: {s['id']}")
    return s


def cmd_cancelar(a):
    p = api("GET", f"/payments/{a.id}")
    if p["status"] in PAGOS:
        raise SystemExit("Essa cobrança já foi paga; cancelar não devolve o dinheiro. Nada feito.")
    if not a.confirmar:
        print(f"Simulação: cancelaria {reais(p['value'])} ({p.get('description')}). Repita com --confirmar.")
        return
    api("DELETE", f"/payments/{a.id}")
    print("Cobrança cancelada.")


# ---------- consultas ----------

_nomes = {}


def nome_cliente(cid):
    if cid not in _nomes:
        c = api("GET", f"/customers/{cid}", falhar=False) or {}
        _nomes[cid] = c.get("company") or c.get("name") or cid
    return _nomes[cid]


def linha(p):
    st = STATUS_PT.get(p["status"], p["status"])
    quando = data_br(p.get("paymentDate") or p.get("clientPaymentDate")) if p["status"] in PAGOS else data_br(p["dueDate"])
    return f"- {nome_cliente(p['customer'])}: {reais(p['value'])} {st} ({quando}) {p.get('description') or ''}".rstrip()


def cmd_pagamentos(a):
    params = {}
    if a.doc or a.cliente:
        params["customer"] = cliente_obrigatorio(a)["id"]
    if a.status:
        params["status"] = a.status
    ps = listar("/payments", params)
    if not ps:
        print("Nenhuma cobrança encontrada.")
    for p in ps[: a.limite]:
        print(linha(p) + f"  [id {p['id']}]")


def cmd_resumo(a):
    """Resumo para a tarefa diária: o que caiu, o que venceu e o que vence logo."""
    desde = hoje() - dt.timedelta(days=a.dias)
    pagos = listar("/payments", {"paymentDate[ge]": desde.isoformat()})
    pagos = [p for p in pagos if p["status"] in PAGOS]
    vencidos = listar("/payments", {"status": "OVERDUE"})
    proximos = listar("/payments", {
        "status": "PENDING",
        "dueDate[ge]": hoje().isoformat(),
        "dueDate[le]": (hoje() + dt.timedelta(days=2)).isoformat(),
    })
    if a.json:
        mostrar({
            "pagos": [dict(p, nomeCliente=nome_cliente(p["customer"])) for p in pagos],
            "vencidos": [dict(p, nomeCliente=nome_cliente(p["customer"])) for p in vencidos],
            "venceEmAte2Dias": [dict(p, nomeCliente=nome_cliente(p["customer"])) for p in proximos],
        })
        return
    if not (pagos or vencidos or proximos):
        print("Sem novidades nas cobranças.")
        return
    if pagos:
        print(f"Pagamentos recebidos desde {data_br(desde.isoformat())}:")
        for p in pagos:
            print(linha(p))
    if vencidos:
        print("Cobranças vencidas:")
        for p in vencidos:
            print(linha(p))
    if proximos:
        print("Vencem em até 2 dias:")
        for p in proximos:
            print(linha(p))


def cmd_status(a):
    s = api("GET", "/myAccount/status")
    print("Conta Asaas:", ", ".join(f"{k}={v}" for k, v in s.items() if k != "id"))


# ---------- linha de comando ----------

def main():
    ap = argparse.ArgumentParser(description="Cobranças no Asaas para a venda de sites")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def alvo(p):
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--doc", help="CPF ou CNPJ do cliente")
        g.add_argument("--cliente", help="id do cliente no Asaas (cus_...)")
        p.add_argument("--empresa", help="nome da empresa para a descrição")

    def criacao(p, dias=DIAS_VENCIMENTO):
        p.add_argument("--tipo", default="UNDEFINED", choices=["UNDEFINED", "PIX", "BOLETO", "CREDIT_CARD"],
                       help="forma de pagamento; UNDEFINED deixa o cliente escolher (padrão)")
        p.add_argument("--dias", type=int, default=dias, help="dias até o vencimento")
        p.add_argument("--confirmar", action="store_true", help="cria de verdade (sem isso é simulação)")

    p = sub.add_parser("status", help="situação da conta")
    p.set_defaults(f=cmd_status)

    p = sub.add_parser("cliente", help="cadastra cliente (ou mostra o existente)")
    p.add_argument("--nome", required=True)
    p.add_argument("--doc", required=True, help="CPF ou CNPJ")
    p.add_argument("--whatsapp")
    p.add_argument("--email")
    p.add_argument("--empresa")
    p.add_argument("--confirmar", action="store_true")
    p.set_defaults(f=cmd_cliente)

    for nome, f, ajuda in (("entrada", cmd_entrada, "cobra 50% de entrada"),
                           ("restante", cmd_restante, "cobra os outros 50%"),
                           ("integral", cmd_integral, "cobra o valor todo de uma vez")):
        p = sub.add_parser(nome, help=ajuda)
        alvo(p)
        criacao(p)
        p.add_argument("--valor-total", type=float, default=PRECO_SITE, help=f"padrão {PRECO_SITE}")
        p.add_argument("--abaixo-do-piso", action="store_true", help=f"permite menos de {PISO_SITE}")
        p.add_argument("--repetir", action="store_true", help="cria mesmo se já existir")
        p.set_defaults(f=f)

    p = sub.add_parser("mensalidade", help=f"assinatura de {MENSALIDADE}/mês")
    alvo(p)
    criacao(p)
    p.add_argument("--inicio", help="data da 1ª mensalidade AAAA-MM-DD (padrão: daqui a 30 dias)")
    p.add_argument("--repetir", action="store_true")
    p.set_defaults(f=cmd_mensalidade)

    p = sub.add_parser("cobrar", help="cobrança avulsa")
    alvo(p)
    criacao(p)
    p.add_argument("--valor", type=float, required=True)
    p.add_argument("--descricao", required=True)
    p.add_argument("--ref", help="referência interna")
    p.set_defaults(f=cmd_cobrar)

    p = sub.add_parser("cancelar", help="cancela cobrança não paga")
    p.add_argument("id")
    p.add_argument("--confirmar", action="store_true")
    p.set_defaults(f=cmd_cancelar)

    p = sub.add_parser("pagamentos", help="lista cobranças")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--doc")
    g.add_argument("--cliente")
    p.add_argument("--status", choices=list(STATUS_PT))
    p.add_argument("--limite", type=int, default=30)
    p.set_defaults(f=cmd_pagamentos)

    p = sub.add_parser("resumo", help="pagos, vencidos e a vencer")
    p.add_argument("--dias", type=int, default=1, help="pagamentos desde quantos dias atrás")
    p.add_argument("--json", action="store_true")
    p.set_defaults(f=cmd_resumo)

    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
