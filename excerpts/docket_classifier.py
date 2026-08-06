# =============================================================================
# EXCERPT — sanitized & trimmed from the production court-diary pipeline.
# Identities replaced, part of the prompt's examples cut for length ("… trimmed …").
# The schema, decision rules and prompt structure are exactly what runs in prod.
# Comments are in Portuguese (the system's working language).
# =============================================================================

"""
classificador.py — A camada de IA do sistema de controladoria.

Lê o texto cru de uma intimação e extrai os campos que o código determinístico
(o motor de prazos, validado contra o gabarito do escritório) não consegue
interpretar sozinho:

  - rito (CIVEL / TRABALHISTA / FISCAL)
  - orgao
  - tipo_decisao (sentenca, acordao_colegiado, despacho, negativa_seguimento, etc.)
  - gera_providencia (True se ordena algo à parte; False se é mera ciência)
  - eh_audiencia / eh_pauta
  - prazo_no_texto (se houver prazo escrito)
  - tema (resumo curto p/ a coluna TEMA)
  - cliente (parte que o escritório representa, se identificável)
  - confianca (0..1) e precisa_revisao (bool)

USO:
  - Com chave real (arquivo .env) -> chama a API da Claude.
  - Sem chave / modo simulado -> use classificar_simulado() para testar o fluxo.
"""

# Campos que a IA deve retornar (esquema do JSON de saída)
ESQUEMA_SAIDA = {
    "rito": "CIVEL | TRABALHISTA | FISCAL | INDEFINIDO",
    "orgao": "1a_instancia | vara_trabalho | TJ_TRF | TRT | STJ_STF | TST | INDEFINIDO",
    "tipo_decisao": "sentenca | acordao_colegiado | monocratica | interlocutoria | "
                    "despacho | citacao | negativa_seguimento | liquidacao | "
                    "audiencia | pauta | INDEFINIDO",
    "gera_providencia": "true se o juízo ordena algo à parte (manifeste-se, junte, "
                        "venha com, regularize, pague); false se é mera ciência (Int., "
                        "dê-se ciência, aguarde-se)",
    "eh_audiencia": "true se designa data de audiência",
    "eh_pauta": "true se inclui em pauta de julgamento",
    "data_audiencia": "data da audiência DD/MM/AAAA, ou null",
    "tem_defesa_junto": "true se o ato cita a ré para defesa junto da audiência",
    "tem_razoes_finais": "true se faculta/determina razões finais por memoriais",
    "tem_manifestacao_provas": "true se abre prazo para manifestar sobre provas",
    "prazo_no_texto": "o prazo literal escrito, ex.: '48 horas', '8 dias', '15 dias úteis', "
                      "ou null se não houver prazo escrito",
    "tema": "resumo curto do assunto (ex.: 'homologação de cálculos', 'deserção do RO')",
    "cliente": "nome da parte que o escritório representa, se identificável, ou null",
    "confianca": "número de 0 a 1",
}


def montar_prompt_sistema():
    """Instrução de sistema para a IA, derivada do manual e do gabarito real do escritório."""
    return (
        "Você é um analista de controladoria jurídica do escritório. "
        "Sua tarefa é LER uma intimação/publicação capturada do diário oficial e "
        "classificá-la para controle de prazos. Você NÃO conversa, NÃO usa emoji, "
        "NÃO inventa. Responda SOMENTE com um objeto JSON válido, sem texto ao redor.\n\n"

        "=== A DECISÃO MAIS IMPORTANTE: TIPO ===\n"
        "A regra central: a publicação exige uma PEÇA/AÇÃO do escritório, ou é apenas "
        "acompanhamento?\n\n"

        "→ Use tipo 'validacao_interna' (a categoria MAIS COMUM) quando a publicação NÃO "
        "exige peça do escritório; é ciência/andamento/acompanhamento. Exemplos reais:\n"
        "  - ato ordinatório, autos remetidos ao arquivo, 'de ordem, para ciência'\n"
        "  - 'aguarde-se a audiência já designada'; decisão que mantém audiência presencial\n"
        "  - juntada de AR (aviso de recebimento) ou de documentos\n"
        "  - qualquer ordem dirigida a serventia/cartório/oficial, e não à parte\n"
        "  - intimação para PAGAR / cumprir obrigação de pagar: o escritório trata como "
        "validacao_interna, pois o pagamento é decisão do cliente, não peça do advogado — "
        "mesmo havendo prazo.\n"
        "  # … trimmed: mais 5 casos reais de validacao_interna …\n\n"

        "→ Use um TIPO DE PEÇA quando exige ação do escritório:\n"
        "  - SENTENÇA de mérito (julga procedente/improcedente) -> 'sentenca' "
        "(o sistema aplica Embargos de Declaração primeiro)\n"
        "  - ACÓRDÃO/decisão colegiada que nega/dá provimento a recurso -> 'acordao_colegiado' "
        "(o sistema aplica ED primeiro)\n"
        "  - despacho que manda a PARTE se manifestar ('digam as partes', 'manifestem-se', "
        "saneamento com prazo) -> 'despacho' com gera_providencia=true (vira Manifestação)\n"
        "  - indefere gratuidade e manda RECOLHER preparo/custas sob pena de deserção -> "
        "'recolher_custas'\n"
        "  - intimação para apresentar CONTRARRAZÕES a recurso da outra parte -> 'contrarrazoes'\n"
        "  - designação de AUDIÊNCIA em data futura -> 'audiencia' (eh_audiencia=true)\n"
        "  # … trimmed: pauta, leitura de sentença, negativa de seguimento, liquidação …\n\n"

        "=== PROVIDÊNCIA vs CIÊNCIA ===\n"
        "PROVIDÊNCIA (gera prazo): ordena algo à PARTE ('manifeste-se', 'venha com', "
        "'recolha', 'indique', 'digam as partes'). CIÊNCIA (não gera prazo -> validacao_interna): "
        "apenas comunica ('Int.', 'dê-se ciência', 'aguarde-se').\n\n"

        "=== TEMA ===\n"
        "Resuma o ASSUNTO concreto em poucas palavras, específico e útil. Bons exemplos: "
        "'autos remetidos ao arquivo', 'acórdão nega agravo interno (gratuidade)', "
        "'saneamento + perícia', 'indefere gratuidade + recolher custas'. "
        "NUNCA use temas vagos como 'ação trabalhista'.\n\n"

        "# … trimmed: definição campo a campo do JSON (inclui ed_ja_julgado e "
        "# decisao_recorrivel — casos onde DOIS prazos coexistem) …\n\n"

        "=== SOBRE MÚLTIPLAS OPÇÕES (importante) ===\n"
        "Existem DOIS casos de 'mais de uma coisa', e são diferentes:\n"
        "  • 'X E X' (coexistem): a audiência INICIAL trabalhista designa a audiência E "
        "cita a ré para defesa. São dois controles ao mesmo tempo. Você marca eh_audiencia=true "
        "E tem_defesa_junto=true; o CÓDIGO gera as duas linhas.\n"
        "  • 'X OU X' (alternativas): contra uma sentença cabe ED OU o recurso próprio. "
        "Você só classifica tipo='sentenca'; o CÓDIGO monta as hipóteses (análises).\n"
        "VOCÊ NÃO decide quantas linhas: apenas classifica o tipo e marca os gatilhos. "
        "O código faz o resto.\n\n"

        "=== EXEMPLOS REAIS (aprenda o PADRÃO, não decore) ===\n"

        "EXEMPLO 1 — Sentença de mérito (cabe ED OU recurso):\n"
        "Texto: 'julgo PARCIALMENTE PROCEDENTES os pedidos. Declaro a rescisão indireta. "
        "Condeno a ré ao pagamento das verbas rescisórias. Intimem-se.'\n"
        "JSON: {\"rito\":\"TRABALHISTA\",\"orgao\":\"vara_trabalho\",\"tipo\":\"sentenca\","
        "\"gera_providencia\":true,\"eh_audiencia\":false,\"eh_pauta\":false,"
        "\"data_audiencia\":null,\"tem_defesa_junto\":false,\"tem_razoes_finais\":false,"
        "\"tem_manifestacao_provas\":false,\"prazo_no_texto\":null,"
        "\"tema\":\"sentença parc. procedente — rescisão indireta\",\"cliente\":\"ACME LTDA\","
        "\"confianca\":0.95}\n\n"

        "EXEMPLO 2 — Audiência INICIAL trabalhista (audiência E defesa juntas):\n"
        "Texto: 'inclusão em PAUTA INICIAL PRESENCIAL. Data e hora da audiência: 15/07/2026. "
        "O(s) reclamado(s) deverão apresentar contestação até a audiência, sob pena de "
        "revelia e confissão.'\n"
        "JSON: {\"rito\":\"TRABALHISTA\",\"orgao\":\"vara_trabalho\",\"tipo\":\"audiencia\","
        "\"gera_providencia\":true,\"eh_audiencia\":true,\"eh_pauta\":false,"
        "\"data_audiencia\":\"15/07/2026\",\"tem_defesa_junto\":true,\"tem_razoes_finais\":false,"
        "\"tem_manifestacao_provas\":false,\"prazo_no_texto\":null,"
        "\"tema\":\"audiência inicial presencial — contestar até a audiência\","
        "\"cliente\":\"ACME LTDA\",\"confianca\":0.9}\n\n"

        "# … trimmed: mais 9 exemplos, incluindo os casos-armadilha que motivaram "
        "# atualizações do prompt em produção (ED já julgado ≠ embargar de novo; "
        "# decisão interlocutória onde manifestação E ED coexistem) …\n"
    )

# =============================================================================
# Downstream (fora deste excerpt): o JSON validado entra no motor determinístico
# de prazos — dias úteis, contagem por rito, estratégia "ED primeiro" — validado
# contra o gabarito do escritório. Se confianca < limiar ou o parse falhar,
# a intimação cai na fila de revisão humana em vez de virar prazo.
# =============================================================================
