"""Gera os diagramas do ciclo completo e das métricas de avaliação
(docs/arquitetura/ciclo-completo.md e docs/arquitetura/metricas-de-avaliacao.md).

Mesmas regras dos demais diagramas (../gerar_diagramas.py,
../agentes/gerar_diagramas_agentes.py e ../arquitetura/gerar_diagramas_arquitetura.py),
que seguem os exemplos do Manual de Textos Técnicos: largura <= 16 cm,
altura <= 9 cm, texto em Arial 12 pt a 100%, sem título, fonte ou legenda
dentro da imagem.

As linhas do tempo da medição (wall-clock e overhead) ficam em ../arquitetura/
(02-estrela-medicao, 05-arvore-medicao) e são reaproveitadas pelo documento
de métricas.

Uso (Windows, precisa só de Pillow e da Arial instalada):
    python docs/diagramas/ciclo-metricas/gerar_diagramas_ciclo_metricas.py

O conteúdo segue backend/api/{chat_websocket,dispatch,runners,websocket}.py,
backend/core/quality_metrics.py, backend/core/ragas_metrics.py e
backend/core/llm_client.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "agentes"))

from gerar_diagramas import BLACK, CAP, GROUP_STROKE, Canvas, baseline, tw  # noqa: E402
from gerar_diagramas_agentes import (  # noqa: E402
    FRAME_TITLE, GX, GY, MAX_H, RIGHT, Node, arrow, bus_down, bus_in, columns, frame,
    h_link, label, uniform, v_link,
)


# ---------------------------------------------------------------------------
# Elementos
# ---------------------------------------------------------------------------


class Pt:
    """Ponto de ancoragem na borda de um quadro, para bus_down/bus_in."""

    def __init__(self, cx, y, bottom=None):
        self.cx, self.y = cx, y
        self.bottom = y if bottom is None else bottom


def fill_row(nodes, x0, x1, y, gap):
    """Distribui as caixas entre x0 e x1, repartindo a sobra igualmente."""
    need = sum(n.need_w for n in nodes) + gap * (len(nodes) - 1)
    extra = (x1 - x0 - need) / len(nodes)
    if extra < 0:
        raise ValueError(f"linha {nodes[0].texts[0][0]!r}… não cabe ({need:.0f} pt)")
    x = x0
    for n in nodes:
        n.w = n.need_w + extra
        n.at(x, y)
        x = n.right + gap


def sequence(c, heads, xs, rows, step=18, self_step=24):
    """Diagrama de sequência no padrão de 14-arvore-fluxo.

    rows: ("msg", de, para, texto, tracejada) | ("self", quem, texto) |
    ("gap", altura) — espaço livre para guardas de um quadro alt.
    Devolve a y de cada linha (para "gap", a y em que o espaço começa).
    """
    hh = max(h.h for h in heads)
    for h, x in zip(heads, xs):
        h.h = hh
        h.at(x - h.w / 2, 0)
    for a, b in zip(heads, heads[1:]):
        if b.x < a.right + 8:
            raise ValueError("cabeçalhos do diagrama de sequência se sobrepõem")
    y = hh + 18
    ys = []
    for row in rows:
        ys.append(y)
        y += {"msg": step, "self": self_step, "gap": row[-1]}[row[0]]
    y_end = ys[-1] + 12
    for x in xs:
        c.line([(x, hh), (x, y_end)], dash=True, sw=0.75)
    for row, yy in zip(rows, ys):
        if row[0] == "self":
            x = xs[row[1]]
            arrow(c, [(x, yy - 6), (x + 18, yy - 6), (x + 18, yy + 6), (x, yy + 6)])
            label(c, x + 24, yy + CAP / 2, row[2], anchor="l")
        elif row[0] == "msg":
            _, a, b, text, dash = row
            xa, xb = xs[a], xs[b]
            if tw(text) > abs(xb - xa) - 8:
                raise ValueError(f"rótulo {text!r} não cabe")
            arrow(c, [(xa, yy), (xb, yy)], dash=dash)
            label(c, (xa + xb) / 2, yy - 4, text)
    for h in heads:
        h.draw(c)
    return ys


# ---------------------------------------------------------------------------
# Ciclo completo
# ---------------------------------------------------------------------------


def fig_ciclo_fluxo():
    c = Canvas()
    u = Node("Usuário", ["/ws/chat"], "ext")
    i = Node("Intenção", ["parse(texto)"])
    d = Node("dispatch_analysis", ["Analise + ws_queue"])
    s = Node("run_star", ["thread A"])
    h = Node("run_hierarchical", ["thread B"])
    q = Node("ws_queue", ["2 topologias"], "data")
    w = Node("/ws/{analysisId}", ["streaming"])
    m = Node("Métricas", ["determinísticas"])
    r = Node("RAGAS", ["2 arquiteturas"], "ext")
    rel = Node("Relatório", ["comparativo"])
    rows = [0.0]
    for _ in range(3):
        rows.append(rows[-1] + u.h + GY)
    # linha 1: usuário ⇄ intenção → dispatch (alinhado à direita, sobre as threads)
    h.at(RIGHT - h.w, rows[1])
    s.at(h.x - GX - s.w, rows[1])
    d.at(RIGHT - d.w, rows[0])
    u.at(0, rows[0])
    i.at(u.right + tw("recusa / conversa") + 14, rows[0])
    if d.x - i.right < tw("dados") + 10:
        raise ValueError("intenção encosta no dispatch")
    # linha 3: fila → WebSocket de resultados → métricas (da direita p/ a esquerda)
    q.at((s.cx + h.cx) / 2 - q.w / 2, rows[2])
    w.at(q.x - GX - w.w, rows[2])
    m.at(0, rows[2])
    # linha 4: RAGAS → relatório
    r.at(0, rows[3])
    rel.at(r.right + 3 * GX, rows[3])
    h_link(c, u, i, y=u.cy - 5, text="mensagem")
    h_link(c, i, u, y=u.cy + 5, text="recusa / conversa", dash=True, below=True)
    h_link(c, i, d, text="dados")
    bus_down(c, d, [s, h], rows[1] - GY / 2)
    bus_in(c, [s, h], q, rows[2] - GY / 2)
    h_link(c, q, w)
    h_link(c, w, m, text="done × 2")
    v_link(c, m, r)
    h_link(c, r, rel)
    for n in (u, i, d, s, h, q, w, m, r, rel):
        n.draw(c)
    return c


def fig_ciclo_ws_chat():
    c = Canvas()
    heads = [Node("Frontend"), Node("chat_websocket"), Node("Intenção"), Node("dispatch")]
    x0 = heads[0].w / 2
    x1 = x0 + tw("system_chunk … system_done") + 14
    x3 = RIGHT - heads[3].w / 2
    xs = [x0, x1, (x1 + x3) / 2, x3]
    rows = [
        ("msg", 0, 1, "user_message", False),
        ("msg", 1, 0, "user_ack", True),
        ("msg", 1, 2, "parse(texto)", False),
        ("msg", 2, 1, "resultado", True),
        ("gap", 27),
        ("msg", 1, 0, "system_chunk … system_done", True),
        ("gap", 24),
        ("msg", 1, 3, "dispatch_analysis", False),
        ("msg", 3, 1, "analysisId", True),
        ("msg", 1, 0, "analysis_started", True),
        ("msg", 1, 0, "system_chunk … system_done", True),
    ]
    ys = sequence(c, heads, xs, rows)
    # quadro alt: [sem análise] | [com análise]
    fx, fy = 1.0, ys[4] - 6
    fw, fb = RIGHT - 2, ys[-1] + 8
    c.rect(fx, fy, fw, fb - fy, fill=None, stroke=BLACK, sw=0.75)
    tag_w, tag_h = tw("alt", "b") + 10, 15.0
    c.rect(fx, fy, tag_w, tag_h, fill="#FFFFFF", stroke=BLACK, sw=0.75)
    c.text(fx + 5, baseline(fy, tag_h), "alt", weight="b")
    label(c, fx + tag_w + 6, baseline(fy, tag_h), "[fora do escopo ou conversa]", anchor="l")
    y_div = ys[6] - 6
    c.line([(fx, y_div), (fx + fw, y_div)], dash=True, sw=0.75)
    label(c, fx + 6, y_div + 4 + CAP, "[pergunta de dados]", anchor="l")
    return c


def fig_ciclo_ws_resultados():
    c = Canvas()
    heads = [Node("Topologias"), Node("ws_queue"), Node("/ws/{analysisId}"), Node("Frontend")]
    x3 = RIGHT - heads[3].w / 2
    x2 = x3 - tw("chunk, metric, agent_data, error") - 14
    x0 = heads[0].w / 2
    xs = [x0, x2 - heads[2].w / 2 - 10 - heads[1].w / 2, x2, x3]
    rows = [
        ("msg", 0, 1, "eventos", False),
        ("msg", 1, 2, "evento", False),
        ("msg", 2, 3, "chunk, metric, agent_data, error", False),
        ("msg", 0, 1, "done × 2", False),
        ("msg", 2, 3, "done × 2", False),
        ("self", 2, "métricas determinísticas"),
        ("msg", 2, 3, "quality_metrics", False),
        ("self", 2, "avaliação RAGAS × 2"),
        ("msg", 2, 3, "ragas … ragas_done", False),
        ("self", 2, "relatório comparativo"),
        ("msg", 2, 3, "chunk … done (both)", False),
    ]
    sequence(c, heads, xs, rows, step=17, self_step=26)   # folga entre ação interna e evento
    return c


def fig_ciclo_llm():
    c = Canvas()
    n1 = Node(None, ["1. Intenção — 1× por mensagem"])
    n6 = Node(None, ["6. Juiz RAGAS — 1× por arquitetura"])
    inner, vgap = 8.0, 12.0
    lanes = []
    for _ in ("Estrela", "Árvore"):
        lanes.append([Node(None, ["2. Dimensão"]), Node(None, ["3. Priorização"]),
                      Node(None, ["4. Síntese"]), Node(None, ["5. Verificação"], "opt")])
    bw, bh = uniform([n for lane in lanes for n in lane])
    fw = bw + 2 * inner
    fh = FRAME_TITLE + 4 * bh + 3 * vgap + inner
    mid_gap = tw("em paralelo") + 30
    fx = [(RIGHT - 2 * fw - mid_gap) / 2]
    fx.append(fx[0] + fw + mid_gap)
    fy = n1.h + GY
    n1.at((RIGHT - n1.w) / 2, 0)
    for x, title, lane in zip(fx, ("Estrela", "Árvore"), lanes):
        frame(c, x, fy, fw, fh, title)
        for k, n in enumerate(lane):
            n.at(x + inner, fy + FRAME_TITLE + k * (bh + vgap))
        for a, b in zip(lane, lane[1:]):
            v_link(c, a, b)
    n6.at((RIGHT - n6.w) / 2, fy + fh + GY)
    tops = [Pt(x + fw / 2, fy) for x in fx]
    bottoms = [Pt(x + fw / 2, fy + fh) for x in fx]
    bus_down(c, n1, tops, fy - GY / 2)
    bus_in(c, bottoms, n6, fy + fh + GY / 2)
    c.text(RIGHT / 2, fy + fh / 2 + CAP / 2, "em paralelo", anchor="m")
    for n in [n1, n6] + [n for lane in lanes for n in lane]:
        n.draw(c)
    return c


# ---------------------------------------------------------------------------
# Métricas de avaliação
# ---------------------------------------------------------------------------


def fig_metricas_eixos():
    c = Canvas()
    inner, gap = 8.0, 12.0
    row1 = [Node("A. Eficiência", ["E1 overhead", "E2 latência por fase"]),
            Node("B. Qualidade", ["Q1 consistência", "Q3 completude"]),
            Node("B. RAGAS (LLM)", ["faithfulness", "answer_relevancy", "context_relevance"], "ext")]
    row2 = [Node("C. Resiliência", ["R1 cobertura"]), Node("D. Custo", ["tokens"]),
            Node("D. Comunicação", ["mensagens"]), Node("E. Outcome", ["sucesso"])]
    procs = [Node("Guardrail", ["taxa de rejeição"]),
             Node("Cache de planejamento", ["inativo"], "opt")]
    h1 = max(n.h for n in row1)
    h2 = max(n.h for n in row2 + procs)
    for n in row1:
        n.h = h1
    for n in row2 + procs:
        n.h = h2
    x0, x1 = inner, RIGHT - inner
    fill_row(row1, x0, x1, FRAME_TITLE, gap)
    fill_row(row2, x0, x1, FRAME_TITLE + h1 + 10, gap)
    fa_h = FRAME_TITLE + h1 + 10 + h2 + inner
    frame(c, 0, 0, RIGHT, fa_h, "Por análise")
    fb_y = fa_h + 12
    fill_row(procs, x0, x1, fb_y + FRAME_TITLE, gap)
    frame(c, 0, fb_y, RIGHT, FRAME_TITLE + h2 + inner, "Process-wide")
    for n in row1 + row2 + procs:
        n.draw(c)
    return c


def fig_metricas_veredito():
    c = Canvas()
    d1 = Node(None, ["Fidelidade medida", "nas duas?"])
    d2 = Node(None, ["|Δ fidelidade|", "≥ 0,05?"])
    o1 = Node(None, ["Vence a maior", "fidelidade"], "data")
    d3 = Node(None, ["Completude (Q3)", "diferente?"])
    o2 = Node(None, ["Vence a maior", "completude"], "data")
    d4 = Node(None, ["Tempo total", "diferente?"])
    o3 = Node(None, ["Vence o menor", "tempo"], "data")
    o4 = Node(None, ["Empate"], "data")
    cols = [[d1, d3, d4, o4], [d2], [o1, o2, o3]]
    need = sum(max(n.need_w for n in col) for col in cols)
    columns(cols, gap=min(60.0, (RIGHT - need) / 2))
    y = 0.0
    for row in ([d1, d2, o1], [d3, o2], [d4, o3], [o4]):
        for n in row:
            n.y = y
        y += max(n.h for n in row) + GY
    o4.x = d4.cx - o4.w / 2
    h_link(c, d1, d2, text="sim")
    h_link(c, d2, o1, text="sim")
    v_link(c, d1, d3, text="não", side="l")
    y_join = d1.bottom + GY / 2
    c.line([(d2.cx, d2.bottom), (d2.cx, y_join), (d1.cx, y_join)])
    label(c, d2.cx + 6, (d2.bottom + y_join) / 2 + CAP / 2, "não", anchor="l", bg=False)
    h_link(c, d3, o2, text="sim")
    v_link(c, d3, d4, text="não", side="l")
    h_link(c, d4, o3, text="sim")
    v_link(c, d4, o4, text="não", side="l")
    for n in (d1, d2, o1, d3, o2, d4, o3, o4):
        n.draw(c)
    return c


def fig_metricas_ragas():
    c = Canvas()
    origens = [Node("Pergunta do chat", ["source_question"], "data"),
               Node("Texto da análise", ["texto_analise"], "data"),
               Node("build_contexts", ["achados, cobertura", "e enquadramento"], "data")]
    campos = [Node(None, ["user_input"]), Node(None, ["response"]),
              Node(None, ["retrieved_contexts"])]
    metricas = [Node("faithfulness", ["response", "× retrieved_contexts"], "ext"),
                Node("answer_relevancy", ["user_input × response", "+ embeddings"], "ext"),
                Node("context_relevance", ["user_input", "× retrieved_contexts"], "ext")]
    uniform(origens)
    uniform(metricas)
    fill_row(origens, 0, RIGHT, 0, GX)
    y_b = origens[0].bottom + GY
    fill_row(campos, 0, RIGHT, y_b, GX)
    inner = 8.0
    fy = campos[0].bottom + GY
    fh = FRAME_TITLE + metricas[0].h + inner
    frame(c, 0, fy, RIGHT, fh, "Juiz RAGAS (RAGAS_PROVIDER)")
    fill_row(metricas, inner, RIGHT - inner, fy + FRAME_TITLE, 12.0)
    for a, b in zip(origens, campos):
        v_link(c, a, b)
    bus_in(c, campos, Pt(RIGHT / 2, fy), campos[0].bottom + GY / 2)
    for n in origens + campos + metricas:
        n.draw(c)
    return c


def fig_metricas_custo():
    c = Canvas()
    escopos = [Node("Intenção", ["chat_websocket", "1× por mensagem"]),
               Node("Estrela", ["run_star", "thread A"]),
               Node("Árvore", ["run_hierarchical", "thread B"]),
               Node("RAGAS", ["2 Tasks (gather)", "+ embeddings"])]
    chaves = [Node(None, ["intent_interpretation"], "data"), Node(None, ["star"], "data"),
              Node(None, ["hierarchical"], "data"),
              Node(None, ["ragas.star", "ragas.hierarchical"], "data")]
    # embeddings não passam por generate(): ragas_metrics chama record_token_usage()
    g = Node("generate(), generate_stream(), record_token_usage()",
             ["somam os tokens no TokenBucket ativo do escopo (ContextVar)"], "ext")
    inner = 8.0
    widths = [max(a.need_w, b.need_w) for a, b in zip(escopos, chaves)]
    gap = (RIGHT - 2 * inner - sum(widths)) / 3
    if gap < 14:
        raise ValueError("colunas de custo não cabem")
    x = inner
    for wdt, a, b in zip(widths, escopos, chaves):
        a.w = b.w = wdt
        a.x = b.x = x
        x += wdt + gap
    uniform_h = max(n.h for n in escopos)
    for n in escopos:
        n.h = uniform_h
        n.y = 0
    g.w = RIGHT - 2 * inner
    g.at(inner, uniform_h + GY)
    fy = g.bottom + GY
    kh = max(n.h for n in chaves)
    for n in chaves:
        n.h = kh
        n.y = fy + FRAME_TITLE
    frame(c, 0, fy, RIGHT, FRAME_TITLE + kh + inner, "cost")
    for a, b in zip(escopos, chaves):
        v_link(c, a, g, x=a.cx)
        arrow(c, [(b.cx, g.bottom), (b.cx, b.y)])
    for n in escopos + chaves + [g]:
        n.draw(c)
    return c


FIGURES = {
    "01-ciclo-fluxo": fig_ciclo_fluxo,
    "02a-ciclo-ws-chat": fig_ciclo_ws_chat,
    "02b-ciclo-ws-resultados": fig_ciclo_ws_resultados,
    "03-ciclo-llm": fig_ciclo_llm,
    "04-metricas-eixos": fig_metricas_eixos,
    "05-metricas-veredito": fig_metricas_veredito,
    "06-metricas-ragas": fig_metricas_ragas,
    "07-metricas-custo": fig_metricas_custo,
}

if __name__ == "__main__":
    only = sys.argv[1:]
    for name, fn in FIGURES.items():
        if only and not any(o in name for o in only):
            continue
        w, h = fn().save(name, MAX_H, out_dir=HERE)
        print(f"{name}: {w / 72 * 2.54:.1f} x {h / 72 * 2.54:.1f} cm")
