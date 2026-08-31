"""
Diagnóstico de acesso ao endpoint de embeddings do provedor do juiz RAGAS.

Existe porque um 403 `model_not_found` no endpoint de embeddings tem duas
causas indistinguíveis pela mensagem de erro:

  (A) O projeto da chave não tem o modelo na allowlist
      (platform.openai.com > Project > Limits, permissões de modelo).
  (B) O cabeçalho `OpenAI-Project`, que o SDK injeta a partir do projeto
      da chave, dispara uma checagem de acesso que falha em algumas
      configurações MESMO com o modelo liberado. Mandar o cabeçalho vazio
      pula essa checagem.

Este script testa cada modelo da cadeia de fallback nas duas condições e
imprime a matriz, para não sobrar adivinhação. Também confirma se o
`chat.completions` funciona com a mesma chave — se ele passa e embeddings
falha em tudo, o caso é (A).

Uso (na raiz do backend, com o .env carregado ou OPENAI_API_KEY exportada):

    python -m scripts.check_embeddings
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core import llm_client, ragas_metrics  # noqa: E402

TEXTO = "teste de acesso ao endpoint de embeddings"


def _rotulo(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    if ragas_metrics._is_model_access_error(exc):
        return f"SEM ACESSO AO MODELO ({status})"
    return f"{type(exc).__name__}: {str(exc)[:70]}"


def main() -> int:
    provider = ragas_metrics.get_judge_provider()
    print(f"Provedor do juiz : {provider.name}  (RAGAS_PROVIDER)")
    print(f"Chave            : {provider.api_key_env}", end=" ")
    if not llm_client.has_api_key(provider):
        print("— AUSENTE. Configure antes de rodar.")
        return 1
    print("— configurada")
    print(f"Modelo do juiz   : {ragas_metrics.get_judge_model(provider)}")
    print()

    print("1) chat.completions com a mesma chave")
    resposta = llm_client.generate(
        "Responda apenas: ok", caller="check_embeddings", provider=provider
    )
    print(f"   {'OK' if resposta else 'FALHOU'} — resposta: {(resposta or '')[:40]!r}")
    print()

    modelos = [
        ragas_metrics.get_embedding_model(),
        *ragas_metrics.get_embedding_fallbacks(),
    ]
    vistos: list[str] = []
    for m in modelos:
        if m and m not in vistos:
            vistos.append(m)

    client = llm_client.build_client(provider)

    print("2) embeddings, por modelo")
    print(
        f"   {'modelo':<26}{'na allowlist':<16}"
        f"{'create c/ cabeçalho':<34}{'create s/ cabeçalho'}"
    )
    print(f"   {'─' * 26}{'─' * 16}{'─' * 34}{'─' * 34}")

    algum_ok = False
    bypass_resolve = False
    visivel_mas_negado = False

    for modelo in vistos:
        # A allowlist do projeto e o caminho de inferência são planos
        # distintos e podem discordar: um modelo recém-liberado aparece
        # aqui antes de a chamada passar a funcionar. Sem esta coluna,
        # "fora da allowlist" e "liberado mas ainda negado" produzem
        # exatamente o mesmo relatório — e as duas causas têm soluções
        # diferentes.
        try:
            client.models.retrieve(modelo)
            visivel = "sim"
        except Exception as exc:  # noqa: BLE001
            visivel = f"não ({getattr(exc, 'status_code', '?')})"

        linha = {}
        for bypass in (False, True):
            emb = ragas_metrics.SkoposRagasEmbeddings(provider=provider, model=modelo)
            try:
                emb._create(modelo, TEXTO, bypass)
                linha[bypass] = "OK"
                algum_ok = True
                if bypass:
                    bypass_resolve = True
            except Exception as exc:  # noqa: BLE001 — é isso que queremos reportar
                linha[bypass] = _rotulo(exc)

        if visivel == "sim" and linha[False] != "OK" and linha[True] != "OK":
            visivel_mas_negado = True

        print(f"   {modelo:<26}{visivel:<16}{linha[False]:<34}{linha[True]}")

    print()
    print("Diagnóstico:")
    if algum_ok:
        if bypass_resolve and linha[False] != "OK":
            print("  Funciona SEM o cabeçalho OpenAI-Project — é o bug conhecido do")
            print("  header. O código já aplica esse contorno automaticamente.")
        else:
            print("  Acesso normal: o modelo responde com o cabeçalho padrão.")
        return 0

    if visivel_mas_negado:
        print("  Os modelos JÁ estão na allowlist do projeto, mas a chamada ainda")
        print("  é negada. A liberação foi registrada e o caminho de inferência")
        print("  ainda não a aplicou. Duas possibilidades:")
        print("    - propagação pendente: aguarde alguns minutos e repita;")
        print("    - a restrição é da CHAVE, não do projeto: em platform.openai.com")
        print("      > API keys, se a chave for Restricted, habilite 'Model")
        print("      capabilities' (que cobre /v1/embeddings).")
        return 3

    print("  Nenhum modelo de embeddings está na allowlist do projeto.")
    print("  Habilite em platform.openai.com > Project > Limits (permissões")
    print("  de modelo) ou configure RAGAS_EMBEDDING_MODEL com um permitido.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
