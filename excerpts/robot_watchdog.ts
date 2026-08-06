// =============================================================================
// EXCERPT — sanitized from the production watchdog for the resident court robot.
// A serverless cron that watches a robot running on office hardware — because a
// watchdog running on the watched machine is useless when that machine dies.
// Comments are in Portuguese (the system's working language).
// =============================================================================

// Vigia do Robô PJe — roda de hora em hora na Vercel (schedule em vercel.json).
//
// Por que NA NUVEM e não no mini PC: um vigia que roda na própria máquina vigiada
// não serve pra nada quando ela desliga, perde internet ou trava. Aqui ele detecta
// os três casos, porque só olha o heartbeat que o robô grava no Postgres
// (tabela robo_pje_rodadas).
//
// Manda push via ntfy (mesmo tópico dos alertas de login/coleta do robô).
// Anti-spam: só um alerta a cada JANELA_SILENCIO_MIN, e avisa quando VOLTA ao
// normal — senão um fim de semana com o PC desligado viraria 48 notificações.
//
// Acionamento:
//   Vercel Cron → GET com header Authorization: Bearer ${CRON_SECRET}
//   Teste manual: GET /api/cron/robo-pje-vigia?debug=1 com o header.

import type { VercelRequest, VercelResponse } from "@vercel/node";

const SUPABASE_URL = process.env.SUPABASE_URL || "";
const SUPABASE_SERVICE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY || "";
const CRON_SECRET = process.env.CRON_SECRET || "";
const NTFY_TOPIC = process.env.NTFY_TOPIC || "";

// A caixa roda de hora em hora; 2 rodadas perdidas = algo errado de verdade.
const LIMITE_CAIXA_MIN = 150;
// As gotas rodam de hora em hora também, mas atrasam mais (rodada longa + retry).
const LIMITE_GOTAS_MIN = 240;
// Não repetir o mesmo alerta antes disso (o robô pode ficar horas fora).
const JANELA_SILENCIO_MIN = 360;

type Rodada = {
  tipo: string;
  status: string;
  itens: number | null;
  detalhe: string | null;
  fim: string;
};

async function sb(path: string, init?: RequestInit) {
  const r = await fetch(`${SUPABASE_URL}/rest/v1/${path}`, {
    ...init,
    headers: {
      apikey: SUPABASE_SERVICE_KEY,
      Authorization: `Bearer ${SUPABASE_SERVICE_KEY}`,
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!r.ok) throw new Error(`supabase ${r.status}: ${(await r.text()).slice(0, 200)}`);
  const txt = await r.text();
  return txt ? JSON.parse(txt) : null;
}

async function push(titulo: string, msg: string, prioridade: "high" | "default") {
  // Header HTTP é latin-1: acento/travessão no Title estoura. Corpo vai UTF-8.
  const tituloAscii = titulo
    .replace(/—/g, "-")
    .replace(/·/g, "-")
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/[^\x20-\x7E]/g, "");
  await fetch(`https://ntfy.sh/${NTFY_TOPIC}`, {
    method: "POST",
    headers: {
      Title: tituloAscii,
      Priority: prioridade,
      Tags: prioridade === "high" ? "warning,robot" : "white_check_mark,robot",
    },
    body: msg,
  });
}

function minutosDesde(iso: string): number {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return Number.POSITIVE_INFINITY;
  return Math.round((Date.now() - t) / 60000);
}

// … trimmed: fmtAtraso() e horaBRT() — formatação de atraso e timezone …

export default async function handler(req: VercelRequest, res: VercelResponse) {
  const auth = req.headers.authorization || "";
  if (CRON_SECRET && auth !== `Bearer ${CRON_SECRET}`) {
    return res.status(401).json({ error: "unauthorized" });
  }

  try {
    const rows: Rodada[] = (await sb(
      "robo_pje_rodadas?select=tipo,status,itens,detalhe,fim&order=fim.desc&limit=60",
    )) || [];

    // última de cada tipo (rows já vem do mais recente pro mais antigo)
    const ultima: Record<string, Rodada> = {};
    for (const r of rows) if (r.tipo && !ultima[r.tipo]) ultima[r.tipo] = r;

    const problemas: string[] = [];
    const caixa = ultima.caixa;
    const gotas = ultima.gotas;

    if (!caixa) {
      problemas.push("CAIXA: nenhuma rodada registrada");
    } else {
      const atraso = minutosDesde(caixa.fim);
      if (atraso > LIMITE_CAIXA_MIN) {
        problemas.push(`CAIXA parada ha ${atraso} min`);
      } else if (caixa.status === "erro") {
        problemas.push(`CAIXA com erro: ${(caixa.detalhe || "").slice(0, 90)}`);
      }
    }

    // … trimmed: mesma checagem pro laço de gotas …

    // Estado do próprio vigia fica na mesma tabela (tipo='vigia'): sem tabela nova,
    // e o histórico de alertas fica auditável junto com as rodadas.
    const ultimoVigia = rows.find((r) => r.tipo === "vigia");
    const emAlerta = ultimoVigia?.status === "alerta";
    const minDesdeAviso = ultimoVigia ? minutosDesde(ultimoVigia.fim) : Number.POSITIVE_INFINITY;

    let acao = "nada";
    if (problemas.length) {
      if (!emAlerta || minDesdeAviso >= JANELA_SILENCIO_MIN) {
        await push(
          "Robo PAROU",
          `${problemas.join("\n")}\n\n` +
            `Checar na maquina residente:\n` +
            `- Windows logado e tela NAO bloqueada\n` +
            `- assinador na bandeja + token do certificado plugado`,
          "high",
        );
        await sb("robo_pje_rodadas", {
          method: "POST",
          body: JSON.stringify([
            { tipo: "vigia", status: "alerta", itens: problemas.length, detalhe: problemas.join(" | ").slice(0, 500) },
          ]),
          headers: { Prefer: "return=minimal" },
        });
        acao = "alerta_enviado";
      } else {
        acao = "silenciado";
      }
    } else if (emAlerta) {
      // voltou ao normal — avisa uma vez e limpa o estado de alerta
      await push("Robo voltou ao normal", "Heartbeats dentro da janela.", "default");
      await sb("robo_pje_rodadas", {
        method: "POST",
        body: JSON.stringify([{ tipo: "vigia", status: "ok", itens: 0, detalhe: "recuperado" }]),
        headers: { Prefer: "return=minimal" },
      });
      acao = "recuperado";
    }

    return res.status(200).json({ ok: true, acao, problemas });
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e);
    console.error("[robo-pje-vigia]", msg);
    return res.status(500).json({ error: msg });
  }
}
