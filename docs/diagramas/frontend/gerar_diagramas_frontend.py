"""Gera os diagramas da interface (docs/05-FRONTEND.md).

Mesmas regras dos demais diagramas (../gerar_diagramas.py e vizinhos), que
seguem os exemplos do Manual de Textos Técnicos: largura <= 16 cm, altura
<= 9 cm, texto em Arial 12 pt a 100%, sem título, fonte ou legenda dentro da
imagem.

Uso (Windows, precisa só de Pillow e da Arial instalada):
    python docs/diagramas/frontend/gerar_diagramas_frontend.py [filtro...]

O conteúdo segue frontend/src/App.tsx, hooks/useWebSocket.ts,
hooks/useChatWebSocket.ts e components/ChatInterface.tsx.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
for sub in ("ciclo-metricas", "arquitetura", "agentes", ""):
    sys.path.insert(0, str(HERE.parent / sub))

from gerar_diagramas import CAP, ROW, Canvas, baseline, tw  # noqa: E402
from gerar_diagramas_agentes import GY, MAX_H, RIGHT, Node, arrow, columns, h_link, label, v_link  # noqa: E402
from gerar_diagramas_arquitetura import bar, bracket, legend_item  # noqa: E402


# ---------------------------------------------------------------------------
# Fluxo de dados
# ---------------------------------------------------------------------------


def fig_fluxo_dados():
    """Os dois WebSockets, o App e os dois consumidores do estado ao vivo."""
    c = Canvas()
    wschat = Node("/ws/chat", ["intenção"], "ext")
    wsres = Node("/ws/{analysisId}", ["resultados"], "ext")
    chat = Node("ChatInterface", ["useChatWebSocket"])
    app = Node("App", ["cria a rodada"])
    hook = Node("useWebSocket", ["(analysisId)"])
    estado = Node("Estado ao vivo", ["da rodada ativa"], "data")
    tech = Node("Aba Agentes", ["rounds[i].snapshot"])
    cols = [[wschat, chat], [app, tech], [wsres, hook, estado]]
    need = sum(max(n.need_w for n in col) for col in cols)
    columns(cols, gap=(RIGHT - need) / 2)
    gap_chat = 2 * ROW + 10        # duas linhas de rótulo entre /ws/chat e o chat
    r2 = wschat.h + gap_chat
    r3 = r2 + chat.h + GY
    wschat.y = wsres.y = 0
    chat.y = app.y = hook.y = r2
    tech.y = estado.y = r3
    # /ws/chat ⇄ ChatInterface: ida e volta em setas paralelas
    arrow(c, [(chat.cx - 8, chat.y), (chat.cx - 8, wschat.bottom)])
    arrow(c, [(chat.cx + 8, wschat.bottom), (chat.cx + 8, chat.y)])
    ym = (wschat.bottom + chat.y) / 2
    label(c, chat.cx + 14, ym - ROW / 2 + CAP / 2, "↑ user_message", anchor="l", bg=False)
    label(c, chat.cx + 14, ym + ROW / 2 + CAP / 2, "↓ analysis_started", anchor="l", bg=False)
    h_link(c, chat, app, text="analysisId")
    h_link(c, app, hook)
    v_link(c, wsres, hook, text="eventos", side="l")
    v_link(c, hook, estado)
    h_link(c, estado, tech, text="espelha")
    # estado ao vivo → bolha do chat (activeRoundWs), por baixo da aba Agentes
    y_r = estado.bottom + 18
    c.line([(estado.cx, estado.bottom), (estado.cx, y_r), (chat.cx, y_r)])
    arrow(c, [(chat.cx, y_r), (chat.cx, chat.bottom)])
    label(c, (chat.cx + estado.cx) / 2, y_r - 4, "activeRoundWs", bg=False)
    for n in (wschat, wsres, chat, app, hook, estado, tech):
        n.draw(c)
    return c


def fig_eventos_estado():
    """Que evento preenche que campo do estado, e quem exibe cada campo."""
    c = Canvas()
    eventos = [Node("star · hierarchical", ["chunk, done, error", "metric, agent_data"], "ext"),
               Node("both", ["quality_metrics", "ragas, ragas_done", "chunk, done"], "ext")]
    campos = [Node(None, ["starText · hierText", "…Loading · …Error", "…Benchmarks",
                          "…AgentData"], "data"),
              Node(None, ["qualityMetrics", "ragasText · ragasLoading", "comparativeReport",
                          "comparativeLoading"], "data")]
    comps = [Node(None, ["ArchitecturePanel", "AgentDataCard", "RoundCard (tempos)", "bolha do chat"]),
             Node(None, ["QualityMetricsSection", "ComparativeSection", "RoundCard (vencedor)",
                         "bolha (badge)"])]
    cols = [eventos, campos, comps]
    need = sum(max(n.need_w for n in col) for col in cols)
    gap = (RIGHT - need) / 2
    if gap < 16:
        raise ValueError("colunas de eventos/estado não cabem")
    columns(cols, gap=gap)
    head = 18.0
    y = head
    for row in zip(eventos, campos, comps):
        h = max(n.h for n in row)
        for n in row:
            n.h = h
            n.y = y
        y += h + 16
    for title, col in zip(("Evento", "Campo do estado", "Componente"), cols):
        c.text(col[0].cx, baseline(0, head - 4), title, weight="b", anchor="m")
    for e, f, k in zip(eventos, campos, comps):
        h_link(c, e, f)
        h_link(c, f, k)
    for n in eventos + campos + comps:
        n.draw(c)
    return c


# ---------------------------------------------------------------------------
# Rodadas
# ---------------------------------------------------------------------------


def fig_rodadas():
    """Duas rodadas: o estado ao vivo, o espelho no snapshot e o congelamento."""
    c = Canvas()
    t_fim, t_troca, folga = 100.0, 50.0, 1.5
    linhas = [
        ("/ws/{analysisId}", [(0, t_troca - folga, "agente", "conexão 1"),
                              (t_troca + folga, t_fim, "agente", "conexão 2")]),
        ("useWebSocket", [(0, t_troca - folga, "agente", "estado da rodada 1"),
                          (t_troca + folga, t_fim, "agente", "estado da rodada 2")]),
        ("rounds[0].snapshot", [(0, t_troca - folga, "coord", "espelhado"),
                                (t_troca + folga, t_fim, "excl", "congelado")]),
        ("rounds[1].snapshot", [(t_troca + folga, t_fim, "coord", "espelhado")]),
    ]
    x0 = max(tw(nome) for nome, _ in linhas) + 12
    unit = (RIGHT - x0 - 2) / t_fim
    row_h, bar_h = 22.0, 16.0
    y_leg = 12.0
    lx = legend_item(c, 0, y_leg, "agente", "ao vivo")
    lx = legend_item(c, lx, y_leg, "coord", "espelhado no snapshot")
    legend_item(c, lx, y_leg, "excl", "congelado")
    y_br = y_leg + 30
    bracket(c, x0, x0 + (t_troca - folga) * unit, y_br, "Rodada 1")
    bracket(c, x0 + (t_troca + folga) * unit, x0 + t_fim * unit, y_br, "Rodada 2")
    y0 = y_br + 10
    for i, (nome, barras) in enumerate(linhas):
        y = y0 + i * row_h
        c.text(0, baseline(y, row_h), nome)
        for a, b, kind, texto in barras:
            bx, bw = x0 + a * unit, (b - a) * unit
            by = y + (row_h - bar_h) / 2
            if tw(texto) > bw - 8:
                raise ValueError(f"rótulo {texto!r} não cabe na barra")
            bar(c, bx, by, bw, bar_h, kind)
            c.text(bx + bw / 2, baseline(by, bar_h), texto, anchor="m")
    xm = x0 + t_troca * unit
    y_end = y0 + len(linhas) * row_h + 4
    c.line([(xm, y_br), (xm, y_end)], dash=True)
    c.text(xm, y_end + 4 + CAP, "nova pergunta: analysisId muda", anchor="m")
    c.text(xm, y_end + 4 + CAP + ROW, "reset síncrono · close(1000)", anchor="m")
    return c


# ---------------------------------------------------------------------------
# Bolha de resposta do chat
# ---------------------------------------------------------------------------


def fig_bolha_resposta():
    """Como o ChatInterface preenche e finaliza a bolha de resposta de uma rodada."""
    c = Canvas()
    inicio = Node(None, ["Bolha \"Analisando", "os dados…\""], "data")
    d_texto = Node(None, ["Alguma arquitetura", "já tem texto?"])
    d_rodando = Node(None, ["Alguma ainda", "está rodando?"])
    falha = Node(None, ["formatRoundSummary", "(nenhum texto)"], "data")
    acompanha = Node(None, ["Acompanha a", "primeira com texto"])
    sem_badge = Node(None, ["Finaliza", "sem badge"], "data")
    atualiza = Node(None, ["Atualiza a bolha", "chunk a chunk"])
    d_transmite = Node(None, ["Ela ainda", "transmite?"])
    d_relatorio = Node(None, ["Relatório já aponta", "a mesma?"])
    com_badge = Node(None, ["Finaliza", "com badge"], "data")
    cols = [[inicio, d_texto, d_rodando, falha], [acompanha, sem_badge],
            [atualiza, d_transmite, d_relatorio, com_badge]]
    need = sum(max(n.need_w for n in col) for col in cols)
    columns(cols, gap=min(60.0, (RIGHT - need) / 2))
    h = max(n.h for col in cols for n in col)
    ys = [k * (h + GY) for k in range(4)]
    for col in (cols[0], cols[2]):
        for n, y in zip(col, ys):
            n.y = y
    acompanha.y, sem_badge.y = ys[1], ys[2]
    v_link(c, inicio, d_texto)
    h_link(c, d_texto, acompanha, y=d_texto.cy - 6, text="sim")
    v_link(c, d_texto, d_rodando, text="não", side="l")
    v_link(c, d_rodando, falha, text="não", side="l")
    # "sim, ainda rodando": espera o próximo chunk e reavalia
    xl = d_rodando.right + 15
    c.line([(d_rodando.right, d_rodando.cy), (xl, d_rodando.cy), (xl, d_texto.cy + 8)])
    arrow(c, [(xl, d_texto.cy + 8), (d_texto.right, d_texto.cy + 8)])
    label(c, xl + 4, (d_texto.bottom + d_rodando.y) / 2 + CAP / 2, "sim", anchor="l", bg=False)
    h_link(c, acompanha, d_transmite)
    v_link(c, d_transmite, atualiza, text="sim", side="l")
    v_link(c, d_transmite, d_relatorio, text="não", side="l")
    h_link(c, d_relatorio, sem_badge, text="não")
    v_link(c, d_relatorio, com_badge, text="sim", side="l")
    for col in cols:
        for n in col:
            n.draw(c)
    return c


FIGURES = {
    "01-frontend-fluxo-dados": fig_fluxo_dados,
    "02-frontend-eventos-estado": fig_eventos_estado,
    "03-frontend-rodadas": fig_rodadas,
    "04-frontend-bolha-resposta": fig_bolha_resposta,
}

if __name__ == "__main__":
    only = sys.argv[1:]
    for name, fn in FIGURES.items():
        if only and not any(o in name for o in only):
            continue
        w, h = fn().save(name, MAX_H, out_dir=HERE)
        print(f"{name}: {w / 72 * 2.54:.1f} x {h / 72 * 2.54:.1f} cm")
