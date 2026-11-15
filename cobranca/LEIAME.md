# Cobranças no Asaas

`asaas.py` gera e acompanha as cobranças da venda de sites. Usa só Python 3, sem instalar nada.

## Chave da API

- **Sessão do Claude na nuvem (ambiente `sites`):** a chave já está salva no ambiente, e a rede a injeta sozinha no cabeçalho `access_token`. Não precisa fazer nada.
- **No computador:** crie a variável de ambiente `ASAAS_API_KEY` com a chave (no Windows: menu Iniciar → "variáveis de ambiente" → "Editar as variáveis de ambiente para sua conta" → Novo). Depois reinicie o app do Claude.
- A chave **nunca** vai para este repositório, para o chat ou para arquivos.

## Regras de preço (iguais ao roteiro)

| Item | Valor |
|---|---|
| Site | R$ 289 (desconto só com resistência: R$ 259, depois R$ 231) |
| Piso | R$ 231. Abaixo disso, só com o Josué (`--abaixo-do-piso`) |
| Pagamento | 50% de entrada para começar os ajustes, 50% antes de publicar no domínio. Pode pagar tudo de uma vez (`integral`) |
| Mensalidade | R$ 45/mês, sem desconto. A 1ª vence 30 dias depois do site no ar |
| Domínio | incluso, custo do Josué |

O cliente escolhe Pix, boleto ou cartão no link do Asaas. A cobrança vence em 2 dias.

## Fluxo

1. **Cliente fechou:** peça nome completo, **CPF ou CNPJ** (o Asaas exige), WhatsApp e e-mail (opcional).
2. `cliente --nome ... --doc ... --whatsapp ... --empresa ... --confirmar`
3. `entrada --doc ... [--valor-total 259] --confirmar` → mande ao cliente o link e o Pix copia e cola, junto com o termo de serviço.
4. Entrada paga → começar os ajustes.
5. Ajustes aprovados → `restante --doc ... [--valor-total 259] --confirmar`.
6. Restante pago → publicar no domínio.
7. Site no ar → `mensalidade --doc ... --confirmar`.

Sem `--confirmar`, todo comando só **simula** e mostra o que criaria. Os comandos `entrada`, `restante` e `mensalidade` não criam uma segunda cobrança igual para o mesmo cliente (para isso, use `--repetir`).

## Consultas

```
python3 asaas.py status                  # situação da conta
python3 asaas.py pagamentos --doc ...    # cobranças de um cliente
python3 asaas.py pagamentos --status OVERDUE
python3 asaas.py resumo                  # pagos desde ontem, vencidos e a vencer em 2 dias
python3 asaas.py cancelar pay_... --confirmar   # só cobrança ainda não paga
```

Não guarde CPF, CNPJ ou dados de clientes neste repositório: eles ficam só no Asaas.
