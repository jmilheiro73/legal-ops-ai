# =============================================================================
# EXCERPT — sanitized & trimmed from the court robot's analysis step.
# This is the piece that reads a lawsuit's actual PDFs with an LLM (native
# document blocks, no OCR) guided by a hand-written analyst manual.
# Comments are in Portuguese (the system's working language).
# =============================================================================

#!/usr/bin/env python3
"""
Analisa os andamentos de um processo trabalhista com um LLM, seguindo o
MANUAL_ANALISE_ANDAMENTOS.md (o "conhecimento do analista").

Fonte dos dados = o próprio tribunal (API autenticada do PJe), não um agregador.

Entrada:
  - CABECALHO + HISTORICO (texto) do processo
  - inteiro teor dos documentos recentes/relevantes (PDFs) -> o modelo lê nativo

Saida:
  - CARD (tradução, sempre) + FICHA (quando AGIR/decisão) em Markdown

A chave vem de .env (LLM_API_KEY). NÃO fica no código.

NOTA DE COLETA (aprendida em produção): a API do tribunal só libera a timeline
e o conteúdo dos documentos ENQUANTO o processo está "aberto" no visualizador.
O coletor precisa abrir o contexto de acesso primeiro (GET no detalhe do
processo) e só então buscar timeline/documentos.
"""
import os
import json
import glob
import base64
import urllib.request

# Tiering por env var: modelo pequeno pro volume; um maior quando a análise pesa mais.
MODELO = os.environ.get("LLM_MODEL_ANDAMENTOS", "small-model")


def carregar_manual():
    """O manual do analista É o system prompt — um documento vivo, atualizado
    quando o modelo erra, como se corrige o manual de um analista júnior."""
    # … trimmed: resolução de caminho …


def montar_conteudo(pasta, header_txt, max_pdfs):
    """Monta o conteúdo do usuário: cabeçalho/histórico + PDFs (document blocks)."""
    conteudo = []
    texto = header_txt or (
        "=== PROCESSO ===\n(cabecalho/historico nao fornecido; use o que os PDFs trazem)\n"
    )
    # O contrato de saída é rígido: o modelo raciocina pelo método das 7 perguntas
    # do manual, mas é PROIBIDO de mostrar o raciocínio — só a saída limpa.
    conteudo.append({"type": "text", "text":
        "Aplique o MÉTODO do manual (as 7 perguntas) como RACIOCÍNIO INTERNO — "
        "NÃO escreva as perguntas nem o raciocínio na resposta.\n\n"
        "Responda APENAS com a saída final LIMPA, exatamente neste formato (Markdown), "
        "começando pela etiqueta de gravidade. SÓ estas três seções, nada além:\n\n"
        "**{AGIR|OLHAR|REGISTRO|ROTINA}** · {prazo real, se houver}\n\n"
        "## Resumo\n{resumo de TODO o processo, em um parágrafo: classe, partes, vara, "
        "fase atual, o que já foi decidido ou acordado (com valores e datas literais) "
        "e onde o processo está hoje}\n\n"
        "## Última movimentação\n{o que aconteceu de mais recente nos autos e o que "
        "isso representa; cite a regra/artigo quando ajudar}\n\n"
        "## O que fazer\n{UMA única sugestão objetiva, em uma frase — a ação mais "
        "importante agora}\n\n"
        "REGRAS: números, datas e valores LITERAIS dos autos; se não achar algo, escreva "
        "'não localizado nos autos'; NÃO enumere as 7 perguntas; NÃO acrescente seções "
        "além das três acima; sem 'provavelmente/aparentemente'.\n\n"
        + texto})

    pdfs = sorted(glob.glob(os.path.join(pasta, "*.pdf")))
    escolhidos = pdfs[:max_pdfs]
    anexados = []
    for p in escolhidos:
        if os.path.getsize(p) < 200:  # stub/erro
            continue
        with open(p, "rb") as f:
            b64 = base64.standard_b64encode(f.read()).decode("ascii")
        conteudo.append({
            "type": "document",
            "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            "title": os.path.basename(p),
        })
        anexados.append(os.path.basename(p))
    return conteudo, anexados


def chamar_modelo(api_key, system, conteudo):
    corpo = {
        "model": MODELO,
        "max_tokens": 8000,
        "system": system,
        "messages": [{"role": "user", "content": conteudo}],
    }
    # O modelo pequeno não aceita thinking:{disabled} e não pensa por padrão -> omitir.
    # Os maiores aceitam disabled (evita gastar budget pensando numa tarefa
    # onde o "raciocínio" já está estruturado pelo manual).
    if MODELO != "small-model":
        corpo["thinking"] = {"type": "disabled"}
    req = urllib.request.Request(
        os.environ["LLM_API_URL"],
        data=json.dumps(corpo).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "api-version": os.environ.get("LLM_API_VERSION", ""),
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        resp = json.loads(r.read())
    partes = [b.get("text", "") for b in resp.get("content", []) if b.get("type") == "text"]
    usage = resp.get("usage", {})
    return "".join(partes), usage

# … trimmed: main() — CLI, salva a análise em disco ANTES de imprimir
# (console Windows cp1252 quebra em caracteres unicode; a análise nunca
# se perde por um erro de print), loga usage de tokens por processo …
