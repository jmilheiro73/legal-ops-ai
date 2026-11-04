# Legal Ops AI — Case Study de Produção

> **[English version →](./README.en.md)**

Plataforma de operações jurídicas que eu projetei, construí e opero sozinho para um escritório de advocacia multi-equipes no Brasil. Em produção desde **2025**, usada diariamente pelo escritório inteiro — advogados, controladoria, sucesso do cliente e os próprios clientes do escritório.

Este é um **repositório de case study**: o produto é proprietário e o código completo permanece privado. O que você encontra aqui é a arquitetura, as decisões de engenharia, trechos de código sanitizados dos pipelines de IA e as lições de operar LLMs em produção. Todos os nomes, partes e números de processo nos exemplos são fictícios.

---

## Os números

| | |
|---|---|
| Em produção | desde 2025, sem time de ops — só eu |
| Processos acompanhados | ~1.060 em toda a carteira do escritório |
| Pipelines de IA entregues em produção | 4 — classificação do diário oficial, Agente de Triagem e resumo em lote seguem rodando; o robô de autos foi aposentado deliberadamente (história abaixo) |
| Funções serverless | ~95 (TypeScript + Python, mesmo repo, mesmo deploy) |
| Jobs agendados | 25 crons na Vercel (capturas, triagem, relatórios, pushes, syncs) |
| Clientes | PWA mobile-first + app iOS nativo na App Store |

## O que a plataforma faz

O escritório vive e morre por **prazos judiciais**. A plataforma roda essa operação inteira:

- **Mesas de prazos** — quadros de atividades contenciosas e consultivas com sistema de faróis guiado por duas camadas de prazo (o prazo interno do escritório vs. o prazo fatal legal)
- **Controladoria do diário oficial (IA)** — captura automaticamente toda publicação endereçada ao escritório no DJEN, o **Claude Opus** lê e classifica cada uma, o prazo legal é calculado deterministicamente, e vira atividade rastreada em 1 clique
- **Robô judicial (IA)** — robô residente que logava no PJe com certificado A3 (smartcard), lia os autos direto da fonte primária, e o **Claude Haiku** produzia um briefing de analista por movimentação — rotulado por gravidade: *agir / olhar / registro / rotina*. Entregue, operado e depois aposentado deliberadamente ([a história completa](#3-análise-de-autos--claude-haiku-lendo-os-pdfs-do-processo))
- **Portal do cliente** — cada empresa cliente tem seu próprio dashboard (white-label, incluindo o app iOS)
- E mais: filas de aprovação em duas etapas, CRM de leads, relatórios mensais `.docx` por cliente com upload no Google Drive, relatórios diários por equipe via e-mail, web push self-hosted, trilha de auditoria completa com soft delete, sync 1-way com Trello.

## Arquitetura

```mermaid
flowchart LR
    subgraph Cliente
        SPA["SPA React 19 (PWA)\nService Worker: push + auto-update em 3 camadas"]
        iOS["App iOS (React Native/Expo)\nárea do cliente nativa + WebView da equipe"]
    end
    subgraph Vercel
        FN["Funções serverless\n~75 TypeScript + 21 Python"]
        CRON["25 cron jobs\ncapturas · triagem · relatórios"]
    end
    subgraph Dados
        PG[("Supabase Postgres\nRLS travada · autorização só no servidor")]
    end
    subgraph Externos
        DJEN["DJEN (diário oficial)"]
        ANT["API Anthropic\nOpus · Sonnet · Haiku"]
        MISC["Trello · Google Drive\nSMTP · Web Push (VAPID)"]
    end
    ROBOT["Robô residente (mini PC)\nPlaywright + certificado A3\nlia os autos do PJe na fonte\n(hoje aposentado)"]

    SPA -- "token de sessão (Bearer)" --> FN
    iOS --> FN
    CRON --> FN
    FN -- service_role --> PG
    FN --> DJEN
    FN --> ANT
    FN --> MISC
    ROBOT --> ANT
    ROBOT -- grava --> PG
    CRON -. "vigia na nuvem\npings + alertas push" .-> ROBOT
```

Regra de projeto nº 1: **o navegador nunca fala com o banco.** Auth é um esquema próprio de token de sessão (senhas em Argon2id); toda leitura/escrita passa por uma função serverless que re-checa papel e equipe no servidor. Esconder botão é cosmético — a função é o portão.

Regra de projeto nº 2: **um repo, um deploy.** Funções TypeScript e Python convivem lado a lado; um `git push` na main sobe frontend, backend, crons e headers atomicamente.

## A engenharia de IA (a parte interessante)

### 1. Classificação do diário oficial — Claude Opus + motor determinístico de prazos

Duas vezes ao dia, uma função Python captura toda publicação endereçada ao escritório no diário oficial. Cada uma passa pelo Opus com um **prompt de extração estruturada** que retorna JSON estrito:

```
rito · instância · tipo de decisão · exige ação do escritório?
é audiência? · prazo literal escrito no texto · resumo do tema
qual parte é nosso cliente · confiança 0..1 · precisa_revisao
```

A decisão-chave: **o LLM nunca calcula prazo.** Prazo processual brasileiro é matemática de calendário com regras (dias úteis, contagens por rito, estratégia de ED primeiro) — isso fica num motor Python determinístico **validado contra o gabarito real do escritório**. O modelo só extrai o que código não consegue interpretar da prosa jurídica livre. Ver [`excerpts/docket_classifier.py`](./excerpts/docket_classifier.py).

O prompt não é genérico: ele codifica a doutrina operacional real do escritório (ex.: *"intimação para pagar é decisão do cliente, não peça do advogado — classifique como validação interna mesmo havendo prazo"*). Quando o modelo erra, a correção entra no prompt como entraria no manual de um analista júnior.

**Human-in-the-loop por design:** nada vira prazo rastreado sem alguém clicar em *Cadastrar* — e nada é descartado sem aprovação do líder da equipe. A IA propõe; o fluxo dispõe.

### 2. Agente de Triagem — Claude Sonnet com ferramentas, medido antes de construído

Em produção desde setembro de 2026, por cima do pipeline anterior. A classificação lê cada intimação **isolada**; o agente lê a intimação **no contexto do processo**. Vinte minutos depois de cada captura, ele consulta o cadastro do processo, as intimações anteriores do mesmo processo, as atividades já abertas na Mesa e a planilha da controladoria, e entrega à equipe uma **proposta pronta**: cadastrar (com o prazo já calculado pelo motor), descartar (com a justificativa que o time usaria) ou mandar para revisão humana.

**Medir antes de desenhar.** Antes da primeira linha de código, medi a produção: cerca de **24 intimações por dia**, **38% descartadas à mão**, e **um terço dos descartes era duplicado** — o mesmo ato capturado de novo, já tratado pelo time. Um classificador que só vê o texto não tem como saber disso. O agente precisava enxergar o que o time já tinha decidido, e cada ferramenta saiu de uma pergunta que a triagem manual fazia:

| Pergunta da triagem | Ferramenta (só leitura) |
|---|---|
| Esse ato já passou pela fila? (o terço duplicado) | `historico_intimacoes` — intimações anteriores do mesmo processo, com a decisão do time e a justificativa de cada descarte |
| O prazo já está controlado na Mesa? | `atividades_abertas` |
| Quem é o cliente? O processo está ativo ou arquivado? | `consultar_cadastro_processo` |
| É um processo de acompanhamento estratégico? | `planilha_controladoria` — fase, estratégia, risco, próxima ação |
| Qual é o prazo? | `calcular_prazos` — roda o motor determinístico com a classificação proposta. **O prazo continua determinístico** |

A lista de motivos de descarte também saiu da medição: é a lista real de justificativas do time, da mais comum para a menos comum, fechada no schema.

```mermaid
flowchart TD
    CAP["Captura do DJEN + classificação (Opus)\n07:00 · 23:00"] --> FILA[("Fila da controladoria\nintimação pendente")]
    FILA -- "cron auxiliar\n07:20 · 23:20" --> LLM
    subgraph LOOP["Agente de Triagem — loop manual, teto de 8 passos"]
        LLM["Claude Sonnet\nsystem prompt em cache"]
        TOOLS["5 ferramentas só de leitura (Postgres)\ncadastro · histórico · atividades abertas\nplanilha · calcular_prazos"]
        LLM -- "tool_use" --> TOOLS
        TOOLS -- "tool_result" --> LLM
    end
    TOOLS -. "calcular_prazos" .-> MOTOR["Motor de prazos\ndeterminístico"]
    LLM -- "end_turn" --> PROP["Proposta tipada\nJSON Schema + Pydantic"]
    PROP --> MODO{"TRIAGEM_MODO"}
    MODO -- "paralelo" --> SOMBRA["só grava a proposta\n(modo sombra, para eval)"]
    MODO -- "substituir" --> APLICA["aplica na fila\ncadastrar · descartar · revisar_humano"]
    APLICA -- "descartar" --> LIDER["pedido em nome do agente\naguarda aprovação do líder"]
```

**Desenho.**

- **Claude Sonnet, loop manual de tool use.** Chamar o modelo → `tool_use` → executar → devolver `tool_result` → repetir, com teto de **8 passos**. Sem o tool runner do SDK e sem framework: mais linhas, mas cada decisão fica à vista. Se uma ferramenta falha, o erro volta para o modelo como `tool_result` com `is_error` e a rodada não cai.
- **Ferramentas `strict` e só de leitura.** A API garante que os argumentos chegam no formato do schema. O agente não grava nada: quem grava é o cron que o chama.
- **Saída estruturada imposta pela API, validada de novo na chegada.** O JSON Schema é gerado do modelo Pydantic e vai como formato de saída; a resposta passa por `Proposta.model_validate_json`. Todo campo de decisão é `Literal` fechado: se o modelo escrever `"cadastro"` em vez de `"cadastrar"`, a validação falha e o erro fica registrado, em vez de um valor inesperado entrar no banco. A ordem dos campos é proposital — raciocínio primeiro, confiança por último.
- **O prazo continua no código.** `calcular_prazos` recebe a classificação no mesmo schema da proposta final e roda o motor que já existia. Se o modelo muda de ideia depois de calcular, o código recalcula pela classificação final.
- **Prompt caching.** O system prompt — instruções de triagem mais as regras do classificador, reaproveitadas — é idêntico para toda intimação e vai com `cache_control`.
- **Versão do agente gravada em toda proposta**, junto com modelo, esforço, passos, ferramentas chamadas, tokens, custo e latência. Mudou o prompt, sobe a versão; comparar versões é trabalho do eval.

O contrato de saída (ver [`excerpts/triage_agent.py`](./excerpts/triage_agent.py)):

```python
Acao = Literal["cadastrar", "descartar", "revisar_humano"]
MotivoDescarte = Literal["nao_se_aplica", "duplicado", "cliente_fora_do_fluxo",
                         "sem_prazo_aberto", "prazo_da_parte_contraria",
                         "certidao_distribuicao", "processo_encerrado_ou_arquivado",
                         "outro"]

class Proposta(BaseModel):
    raciocinio: str               # o que o ato pede, o que as consultas mostraram, por quê
    classificacao: Classificacao  # os mesmos campos que o motor de prazos já consumia
    acao: Acao
    motivo_descarte: MotivoDescarte
    justificativa: str            # pronta para o campo de descarte ou o resumo do cadastro
    evidencias: List[str]         # fatos verificados nas ferramentas
    confianca: float = Field(ge=0, le=1)
```

**Fora, de propósito.** Sem LangChain, sem RAG, sem fila externa. A decisão ficou registrada antes do código, com o gatilho que traria cada um:

| Fora | Gatilho que traria |
|---|---|
| LangChain | um 2º provedor ou um 2º agente |
| LangGraph | ramificação, ou um humano **no meio** da execução — aqui o humano está no fim |
| RAG / pgvector | um corpus de texto livre entrando na decisão — o contexto que decide é estruturado e vem das ferramentas; as regras viajam no prompt |
| Fila externa / Redis | execuções de dezenas de passos, com estado durável no meio — o lote cabe num cron auxiliar, e o que não coube na rodada fica para a próxima |
| Fine-tuning | erro sistemático provado por eval |

**Segurança: o agente propõe, um líder aprova.**

| Modo (`TRIAGEM_MODO`) | Efeito |
|---|---|
| `paralelo` (default) | **Modo sombra**: só grava a proposta ao lado da intimação; a fila não muda. É o modo de avaliação |
| `substituir` (o que roda em produção) | Aplica a proposta na fila — se a intimação ainda estiver pendente. O cron relê a linha antes: se alguém já tratou, não mexe |

- Em `substituir`, **cadastrar** reescreve a intimação com a classificação do agente e os prazos do motor, e a equipe confirma como sempre. **Descartar** abre um pedido de descarte **em nome do agente**, que cai na mesma aprovação do líder que vale para um colaborador: **nada sai da fila sem um líder aprovar**. **Revisar_humano** marca a intimação para revisão com o motivo.
- **Confiança baixa vai para revisão humana.** O prompt manda `revisar_humano` abaixo de 0,6 — e também quando o cliente não está no cadastro, falta o número do processo, o texto está truncado ou contradiz o cadastro. Um cadastro proposto com confiança abaixo de 0,7 chega marcado para revisão.
- **Kill switch:** `TRIAGEM_AGENTE_ATIVA=0` desliga o agente; `TRIAGEM_MODO=paralelo` volta ao modo sombra. São variáveis de ambiente, sem mudança de código.

**Avaliação: o método.** Um script offline compara a proposta do agente com a decisão humana, sem gravar nada no banco:

1. Pega intimações **já decididas** pelo time, em amostra equilibrada entre cadastradas e descartadas.
2. Rebusca o texto de cada uma no DJEN.
3. Roda o agente com **corte temporal**: as ferramentas só enxergam registros criados **antes** daquela intimação. Sem o corte, o agente veria as atividades, as linhas derivadas e o descarte que a própria intimação gerou depois — ou seja, a decisão do time — e o gabarito vazaria para a prova (o caso típico: a intimação "vê" o próprio rastro e se acha duplicada). Em produção o corte fica vazio, porque o agente roda sobre intimação pendente, que ainda não deixou rastro.
4. Imprime a **matriz de concordância** (proposta × decisão humana), mais custo e latência por intimação.

Modelo, esforço e versão do agente são parâmetros do script: a mesma amostra roda contra outro modelo ou outra versão do prompt, e os números ficam comparáveis.

### 3. Análise de autos — Claude Haiku lendo os PDFs do processo

O robô residente loga no PJe com certificado físico (Playwright + automação de UI do Windows + TOTP), puxa a **timeline e os PDFs reais** de cada processo pela API autenticada, e envia ao Haiku como document blocks nativos — sem pipeline de OCR, sem etapa de extração de texto.

O system prompt é um **manual de analista** escrito à mão — um método de 7 perguntas que o modelo aplica como raciocínio interno, proibido de aparecer na saída. O contrato de saída é fixo: etiqueta de gravidade (**AGIR / OLHAR / REGISTRO / ROTINA**), resumo do processo em um parágrafo e — só quando exige ação — uma ficha estruturada. Ver [`excerpts/docket_analyst.py`](./excerpts/docket_analyst.py).

**Tiering de modelo pelo custo do erro:** Opus para a classificação que cria prazos legais (volume baixo, custo de erro catastrófico), Haiku para leitura de autos em volume (um resumo ruim é chato, não perigoso). Mesma plataforma, dois preços, escolhidos de propósito.

**Epílogo — entregue, medido, aposentado.** Essa foi a engenharia mais difícil do projeto (login com certificado físico, throttling imposto pelo WAF, estado à prova de queda numa máquina que é desligada da tomada) — e funcionou. Mas o relatório semanal de cobertura repetia a verdade inconveniente: o robô só alcançava ~39% da carteira — o resto vivia em outros tribunais ou atrás de habilitações pendentes na OAB — então o fornecedor legado de dados nunca poderia ser desligado. Quando a necessidade que ele atendia passou, eu o desativei em vez de pagar indefinidamente o custo operacional de duas fontes sobrepostas. Aposentar um pipeline que funciona porque os dados mandam é tão engenharia de IA quanto entregá-lo.

### 4. Operar LLM em produção — o que quebrou de verdade

As partes que só se aprendem rodando isso por meses:

- **Drift no formato da resposta.** Uma integração de resumo em lote mudou o shape do payload em silêncio; o parser passou a devolver vazio. Correção: parse defensivo + marcador de log pesquisável para "resposta não bateu" — o próximo drift é uma busca no log, não um mistério.
- **Inanição por ordenação.** Um job horário de captura ordenava ascendente com limite de resultados — quando o backlog cresceu além do limite, *o dia atual nunca chegava*. Bug clássico de paginação, mas com um pipeline de IA na jusante parece que "a IA parou de funcionar". Correção: ordem descendente + janela maior.
- **Ancorar no tempo do evento, não da captura.** Resumos eram datados por quando o robô os *viu*, não por quando *aconteceram* — balde de dia errado em todo relatório. Backfill de 35 linhas.
- **A máquina desliga.** O robô roda num mini PC de escritório. A janela de captura de alertas é **adaptativa** — dimensionada pelo intervalo desde a última execução bem-sucedida — então queda de energia não perde nada. Um cron vigia na nuvem checa o heartbeat de hora em hora e dispara push se ele silenciar — ver [`excerpts/robot_watchdog.ts`](./excerpts/robot_watchdog.ts).
- **A fonte te bane.** O WAF do tribunal bane rajadas; as capturas são throttled e distribuídas em três laços defasados (`:00` caixa / `:30` gotas / `:45` gatilho por alerta).
- **Conheça sua cobertura.** O robô cobria 39% da carteira (o resto era outros tribunais ou habilitação pendente na OAB) — medido por relatório semanal de cobertura, que é por que o fornecedor legado nunca pôde ser desligado, e no fim o que embasou a decisão de aposentar o robô. Se você não mede cobertura, não tem um pipeline — tem uma demo.

## Destaques de engenharia além da IA

- **Auto-update do PWA em 3 camadas** — depois de cada deploy, toda aba aberta se atualiza educadamente (reload em idle/aba oculta, rota preservada). Ninguém no escritório jamais ouviu "limpa o cache".
- **Web push self-hosted** (VAPID, sem Firebase): alertas de prazo, resumos da controladoria, alertas do vigia do robô.
- **Auditoria por soft delete** — exclusões movem linhas para tabelas espelho `exc_*`; nada se perde, exclusão em massa restrita a um admin.
- **Python serverless com restrições reais** — o gerador de relatório mensal estourava o limite de 300 s reconstruindo credenciais do Drive por cliente; cache resolveu, e `maxDuration: 800` cobre a cauda.
- **Postura de segurança** — RLS travada de forma que a chave pública anon não consegue nada; verificado com pentest de verdade na minha própria API. Senhas só em Argon2id (coluna SHA-256 legada dropada após a migração).

## Screenshots

*Todos os dados nas capturas são fictícios (censurados a partir de telas de produção).*

**Controladoria — visão Controle do dia**: toda publicação capturada do diário oficial, classificada pelo Claude Opus e acompanhada no fluxo human-in-the-loop (cadastrada / descarte aguardando aprovação do líder):

![Controladoria visão Controle](./docs/img/controladoria-controle.png)

**Mesa de Prazos** — o quadro contencioso: faróis em duas camadas (prazo interno vs. prazo fatal legal), filtros salvos, ações em massa, visões por equipe:

![Mesa de Prazos](./docs/img/mesa-prazos.png)

**Portal do Cliente** — cada empresa cliente tem seu dashboard white-label (também entregue como app iOS nativo):

![Portal do Cliente](./docs/img/portal-cliente.png)

**Início** — os números fechados do mês anterior por tipo de atividade (concluídas × fora do prazo), mais o que vence hoje e nesta semana:

![Início](./docs/img/home-cards.png)

## Stack

React 19 · TypeScript · Vite 7 · Tailwind · PWA (Service Worker custom) · Vercel Functions (Node + Python) · Supabase Postgres · API Anthropic (Claude Opus / Sonnet / Haiku) · Playwright · React Native/Expo · web-push · nodemailer · Google APIs

---

*O código completo é privado (ele opera um escritório de advocacia real). Os trechos aqui são sanitizados: identidades removidas, exemplos fictícios, lógica real. Fico feliz em detalhar qualquer parte — [abra uma issue](../../issues) ou me chame.*
