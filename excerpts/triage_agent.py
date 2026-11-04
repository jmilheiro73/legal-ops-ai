# =============================================================================
# EXCERPT — sanitized & trimmed from the production triage agent.
# One court-diary publication in, one typed proposal out: a manual tool-use
# loop over Claude Sonnet, five read-only tools, a JSON Schema generated from
# Pydantic as the output format, and the deadline left to deterministic code.
# Identities replaced: one client-specific dismissal reason renamed to
# "cliente_fora_do_fluxo", and the firm-branded name of its internal deadline
# replaced by "interno". Prompt, 4 of the 5 tool bodies and the bookkeeping
# around the loop cut ("… trimmed …").
# Comments are in Portuguese (the system's working language).
# =============================================================================

"""
agente_triagem.py — Agente de Triagem da Controladoria.

Para UMA intimação capturada do DJEN, o agente lê o texto, consulta por
ferramentas SÓ DE LEITURA o contexto do processo, pede ao código o cálculo dos
prazos (o prazo NUNCA é decidido pela IA) e entrega uma proposta tipada:
cadastrar / descartar / revisar_humano. Não escreve em tabela nenhuma — quem
grava é o chamador (o cron auxiliar).
"""

import json
import os
import time
from datetime import datetime
from typing import List, Literal

from pydantic import BaseModel, Field, ValidationError

# … trimmed: imports internos, Rito/Orgao/Tipo (Literal), helpers de banco e de
# número CNJ, montar_prompt_sistema (instruções de triagem + as regras do
# classificador, reaproveitadas) …

MODELO_PADRAO = "claude-sonnet-5"
MAX_PASSOS = 8            # teto de rodadas de ferramenta por intimação
MAX_TOKENS = 4096
VERSAO_AGENTE = "2026-09-28.1"   # gravada em cada proposta: permite comparar versões


# ─────────────────────────────────────────────────────────────────────
# Saída tipada (a ordem dos campos importa: raciocínio ANTES da decisão,
# confiança POR ÚLTIMO — o modelo preenche na ordem do schema).
# ─────────────────────────────────────────────────────────────────────
Acao = Literal["cadastrar", "descartar", "revisar_humano"]
MotivoDescarte = Literal["nao_se_aplica", "duplicado", "cliente_fora_do_fluxo",
                         "sem_prazo_aberto", "prazo_da_parte_contraria",
                         "certidao_distribuicao", "processo_encerrado_ou_arquivado",
                         "outro"]


class Classificacao(BaseModel):
    """Mesmos campos que o classificador antigo entregava ao processar.py."""
    rito: Rito
    orgao: Orgao
    tipo: Tipo
    gera_providencia: bool
    # … trimmed: audiência/pauta, gatilhos de defesa/razões/provas, ED já julgado,
    # decisão recorrível, prazo literal, tema, cliente …


class Proposta(BaseModel):
    raciocinio: str = Field(description="Raciocínio curto: o que o texto pede, o que as "
                                        "consultas mostraram e por que a ação abaixo.")
    classificacao: Classificacao
    acao: Acao
    motivo_descarte: MotivoDescarte
    justificativa: str = Field(description="Texto pronto para o campo de justificativa "
                                           "(descarte) ou para o resumo do cadastro.")
    evidencias: List[str] = Field(description="Fatos verificados nas ferramentas que "
                                              "sustentam a decisão. Vazio se não consultou.")
    confianca: float = Field(ge=0, le=1)


# ─────────────────────────────────────────────────────────────────────
# Ferramentas (só leitura). Cada uma devolve um dict pequeno, já resumido:
# o modelo nunca vê a tabela inteira, vê o que a função escolheu mostrar.
# ─────────────────────────────────────────────────────────────────────
def tool_historico_intimacoes(numero_processo: str, limite: int = 8, id_djen_atual: str = "",
                              data_atual: str = "", corte_temporal: str = ""):
    """Intimações já capturadas do mesmo processo (com o que o time decidiu em cada uma)."""
    pat = _cnj_pattern(numero_processo)   # ILIKE tolerante a máscara; conferência exata depois
    if not pat:
        return {"total": 0, "intimacoes": []}
    limite = max(1, min(int(limite or 8), 20))
    params = dict(
        select="id_djen,data_consulta,created_at,tipo,resumo,tema,status,prazo_fatal,"
               "prazo_interno,cliente,revisado_por,revisado_em,precisa_revisao",
        numero_processo=f"ilike.{pat}", order="data_consulta.desc,created_at.desc",
        limit=str(limite + 6))
    if corte_temporal:  # avaliação offline: só o que existia ANTES desta intimação
        params["created_at"] = f"lt.{corte_temporal}"
    _, rows = supabase_writer._rest(_q("controladoria_intimacoes", **params))
    rows = [r for r in (rows or [])
            if str(r.get("id_djen") or "") != str(id_djen_atual or "")
            # satélites (defesa/razões/provas) nascem sem id_djen na MESMA captura:
            # são desdobramentos desta intimação, não um ato anterior
            and not (not r.get("id_djen") and data_atual and r.get("data_consulta") == data_atual)]
    # … trimmed: junta a justificativa de cada descarte e devolve, por intimação:
    # data, tipo, resumo, decisão do time, prazo fatal, justificativa do descarte …


def _schema_sem_titulos(modelo: type[BaseModel]):
    """JSON Schema do Pydantic, com additionalProperties=false em todo objeto
    (exigência do modo estrito) e sem os 'title' que só fazem volume.
    Aprendido em produção: o modo estrito recusa minimum/maximum (erro 400)."""
    def limpa(node):
        if isinstance(node, dict):
            node.pop("title", None)
            node.pop("minimum", None)
            node.pop("maximum", None)
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            for v in node.values():
                limpa(v)
        elif isinstance(node, list):
            for v in node:
                limpa(v)
        return node
    return limpa(modelo.model_json_schema())


def _definicoes_de_tools():
    # A descrição é o único lugar em que o modelo aprende QUANDO usar cada
    # ferramenta — por isso é escrita como instrução.
    return [
        # … trimmed: consultar_cadastro_processo, historico_intimacoes,
        # atividades_abertas, planilha_controladoria — todas strict, só leitura …
        {
            "name": "calcular_prazos",
            "description": "Pede ao CÓDIGO do escritório os prazos (verificação, interno, "
                           "fatal interno) e o tipo da planilha para a classificação que você "
                           "propõe. Chame UMA vez, ao final, sempre que a ação for 'cadastrar' "
                           "— a justificativa do cadastro deve citar o tipo e o prazo "
                           "devolvidos. O prazo nunca é calculado por você.",
            "strict": True,
            "input_schema": {"type": "object", "additionalProperties": False,
                             # o argumento é o MESMO schema da classificação final
                             "properties": {"classificacao": _schema_sem_titulos(Classificacao)},
                             "required": ["classificacao"]},
        },
    ]


# ─────────────────────────────────────────────────────────────────────
# O loop
# ─────────────────────────────────────────────────────────────────────
def triar(texto, *, numero_processo, data_publicacao, contexto_api=None, id_djen=None,
          modelo=None, api_key=None, effort=None, corte_temporal=None):
    """corte_temporal (ISO, só na avaliação offline): as ferramentas enxergam apenas
    o que existia no banco ANTES desse instante — senão a intimação já decidida
    "vê" as atividades e satélites que ela mesma gerou e se acha duplicada."""
    modelo = modelo or os.environ.get("ANTHROPIC_MODEL_TRIAGEM", MODELO_PADRAO)
    effort = effort or os.environ.get("ANTHROPIC_EFFORT_TRIAGEM", "medium")
    # … trimmed: cliente da API, mapa nome -> função (as lambdas injetam o que o
    # modelo não sabe: id atual, data, corte_temporal), contadores de usage …
    tools = _definicoes_de_tools()
    # system prompt idêntico em toda intimação -> cache
    system = [{"type": "text", "text": montar_prompt_sistema(),
               "cache_control": {"type": "ephemeral"}}]

    try:
        while True:
            passos += 1
            resposta = client.messages.create(
                model=modelo,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": effort,
                    # a resposta final TEM que ser a Proposta
                    "format": {"type": "json_schema", "schema": _schema_sem_titulos(Proposta)},
                },
            )
            _acumula(resposta.usage)

            if resposta.stop_reason == "tool_use":
                if passos > MAX_PASSOS:
                    erro = f"teto de {MAX_PASSOS} passos atingido"
                    break
                # só append: recortar o histórico invalida os blocos de raciocínio
                messages.append({"role": "assistant", "content": resposta.content})
                resultados = []
                for bloco in resposta.content:
                    if bloco.type != "tool_use":
                        continue
                    args = bloco.input if isinstance(bloco.input, dict) else json.loads(bloco.input)
                    fn = ferramentas.get(bloco.name)
                    try:
                        if fn is None:
                            raise KeyError(f"ferramenta desconhecida: {bloco.name}")
                        out = fn(args)
                        resultados.append({"type": "tool_result", "tool_use_id": bloco.id,
                                           "content": json.dumps(out, ensure_ascii=False, default=str)})
                        # … trimmed: guarda o último calcular_prazos e o registro da chamada …
                    except Exception as e:  # a falha vira resultado, não derruba a rodada
                        resultados.append({"type": "tool_result", "tool_use_id": bloco.id,
                                           "content": f"Erro ao executar {bloco.name}: {type(e).__name__}: {e}",
                                           "is_error": True})
                # todos os resultados numa mensagem só (mantém as chamadas em paralelo)
                messages.append({"role": "user", "content": resultados})
                continue

            if resposta.stop_reason == "pause_turn":
                messages.append({"role": "assistant", "content": resposta.content})
                continue

            if resposta.stop_reason == "max_tokens":
                erro = "resposta cortada por max_tokens"
            elif resposta.stop_reason == "refusal":
                det = getattr(resposta, "stop_details", None)
                erro = "modelo recusou" + (f" ({det.category})" if det and getattr(det, "category", None) else "")
            break
    except Exception as e:
        erro = f"{type(e).__name__}: {e}"

    proposta = None
    if erro is None and resposta is not None:
        texto_final = "".join(b.text for b in resposta.content if b.type == "text")
        try:
            proposta = Proposta.model_validate_json(texto_final)
        except ValidationError as e:
            erro = f"saída fora do schema: {e.errors()[0].get('msg')} em {e.errors()[0].get('loc')}"

    # … trimmed: se a ação for cadastrar, garante que a linha da planilha corresponde
    # à classificação FINAL (o modelo pode ter ajustado depois de calcular_prazos,
    # ou nem ter chamado) — reaproveita o último cálculo ou recalcula pelo processar.py …

    return {
        "versao": VERSAO_AGENTE,
        "proposta": proposta.model_dump() if proposta else None,
        "linha_planilha": linha_planilha,
        "linha_completa": _json_safe(linha_completa) if linha_completa else None,
        "erro": erro,
        "execucao": {
            "modelo": modelo, "effort": effort, "passos": passos,
            "tools": chamadas, "usage": usage,
            "custo_estimado_usd": _custo_usd(modelo, usage),
            "latencia_ms": int((time.time() - inicio) * 1000),
            "em": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        },
    }

# =============================================================================
# Fora deste excerpt:
#   - o cron auxiliar que chama triar() 20 min depois de cada captura e, conforme
#     TRIAGEM_MODO, só grava a proposta (paralelo) ou a aplica na fila (substituir)
#     — descarte vira pedido em nome do agente, aguardando aprovação do líder;
#   - o script de avaliação: intimações já decididas (amostra equilibrada
#     cadastrado/descartado), texto rebuscado no DJEN, triar(..., corte_temporal=
#     created_at da intimação), matriz de concordância proposta × decisão humana
#     e custo/latência por caso. Não grava nada no banco.
# =============================================================================
