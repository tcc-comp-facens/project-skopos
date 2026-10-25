"""Gera os diagramas das topologias estrela e árvore (docs/arquitetura/).

Mesmas regras dos demais diagramas (../gerar_diagramas.py e
../agentes/gerar_diagramas_agentes.py), que seguem os exemplos do Manual de
Textos Técnicos: largura <= 16 cm, altura <= 9 cm, texto em Arial 12 pt a 100%,
sem título, fonte ou legenda dentro da imagem.

As figuras de topologia (estrela, árvore) e a sequência de chamadas da árvore
ficam em ../agentes/ (12-topologia-estrela, 13-topologia-arvore,
14-arvore-fluxo) e são reaproveitadas pelos documentos de arquitetura.

Na árvore, o nome no código continua `hierarchical` (agents/hierarchical/).

Uso (Windows, precisa só de Pillow e da Arial instalada):
    python docs/diagramas/arquitetura/gerar_diagramas_arquitetura.py

O conteúdo segue backend/agents/star/orchestrator.py e
backend/agents/hierarchical/{coordinator,supervisors}.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "agentes"))

from gerar_diagramas import (  # noqa: E402
    BLACK, CAP, GROUP_FILL, GROUP_STROKE, HEADER_FILL, Canvas, baseline, tw,
)
from gerar_diagramas_agentes import (  # noqa: E402
    FRAME_TITLE, GY, MAX_H, RIGHT, Node, arrow, frame, h_link, label, uniform, v_link,
)

GX = 18
BAR_FILL = "#9E9E9E"       # agentes (tempo somado em workers_time_ms)


# ---------------------------------------------------------------------------
# Elementos de linha do tempo
# ---------------------------------------------------------------------------


def bar(c, x, y, w, h, kind):
    """kind: agente (cinza escuro), coord (branco), excl (cinza claro tracejado)."""
    if kind == "agente":
        c.rect(x, y, w, h, fill=BAR_FILL, stroke=BLACK, sw=1.0)
    elif kind == "coord":
        c.rect(x, y, w, h, fill="#FFFFFF", stroke=BLACK, sw=1.0)
    else:
        c.rect(x, y, w, h, fill=HEADER_FILL, stroke=None)
        c.line([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], dash=True)


def bracket(c, x1, x2, y, text):
    """Chave horizontal com rótulo centralizado acima."""
    if tw(text) > (x2 - x1) + 4:
        raise ValueError(f"rótulo {text!r} maior que a chave")
    c.line([(x1, y + 5), (x1, y), (x2, y), (x2, y + 5)])
    c.text((x1 + x2) / 2, y - 4, text, anchor="m")


def legend_item(c, x, y, kind, text):
    bar(c, x, y - 9, 18, 10, kind)
    c.text(x + 24, y, text)
    return x + 24 + tw(text) + 18


# ---------------------------------------------------------------------------
# Estrela
# ---------------------------------------------------------------------------


def _snake(nodes, x0, y0, cols, w, h, gap_x, gap_y):
    """Posiciona em serpentina: linha 1 da esquerda p/ direita, linha 2 de volta."""
    pos = []
    for i, n in enumerate(nodes):
        row, k = divmod(i, cols)
        col = k if row % 2 == 0 else cols - 1 - k
        n.w, n.h = w, h
        n.at(x0 + col * (w + gap_x), y0 + row * (h + gap_y))
        pos.append((row, col))
    return pos


def _link_snake(c, nodes, pos):
    for (a, pa), (b, pb) in zip(zip(nodes, pos), zip(nodes[1:], pos[1:])):
        if pa[0] == pb[0]:
            h_link(c, a, b)
        else:
            v_link(c, a, b)


def fig_estrela_pipeline():
    c = Canvas()
    dentro = [Node(None, ["consultar_saude", "× N sistemas"]),
              Node(None, ["consultar_orcamento", "× M subfunções"]),
              Node(None, ["cruzar_dados"]), Node(None, ["detectar_gaps"]),
              Node(None, ["analisar_contexto"]), Node(None, ["analisar"])]
    fora = [Node(None, ["priorizar_achados"]), Node(None, ["sintetizar_texto"]),
            Node(None, ["verificar_afirmacoes"], "opt"), Node(None, ["persistir_metricas"])]
    w = max(n.need_w for n in dentro + fora)
    inner = 8.0
    fa_w = 3 * w + 2 * GX + 2 * inner
    # quadro "dentro do wall-clock": 2 linhas em serpentina
    h_a = max(n.h for n in dentro)
    pos_a = _snake(dentro, inner, FRAME_TITLE, 3, w, h_a, GX, GY - 4)
    fa_h = FRAME_TITLE + 2 * h_a + (GY - 4) + 8
    frame(c, 0, 0, fa_w, fa_h, "Dentro do wall-clock")
    # fronteira
    cap = Node(None, ["capturar_wallclock"], w=w)
    gap = 34.0
    cap.at(dentro[-1].x, fa_h + (gap - cap.h) / 2)
    # quadro "fora do wall-clock"
    fb_y = fa_h + gap
    h_b = max(n.h for n in fora)
    pos_b = _snake(fora, inner, fb_y + FRAME_TITLE, 3, w, h_b, GX, GY - 4)
    fb_h = FRAME_TITLE + 2 * h_b + (GY - 4) + 8
    # título à direita: a seta capturar_wallclock → priorizar_achados entra pela esquerda
    c.rect(0, fb_y, fa_w, fb_h, fill=GROUP_FILL, stroke=GROUP_STROKE, sw=0.75, r=5)
    c.text(fa_w - 8, baseline(fb_y + 2, FRAME_TITLE - 4), "Fora do wall-clock", weight="b", anchor="r")
    _link_snake(c, dentro, pos_a)
    v_link(c, dentro[-1], cap)
    v_link(c, cap, fora[0])
    _link_snake(c, fora, pos_b)
    label(c, cap.right + 8, cap.cy + CAP / 2, "fim da medição (_orch_end)", anchor="l", bg=False)
    for n in dentro + fora + [cap]:
        n.draw(c)
    return c


def _gantt(c, rows, x_lab, x0, unit, row_h, y0, bar_h=10):
    """rows: (rótulo, início, duração, tipo, recuo) — tempo em unidades arbitrárias."""
    ys = []
    for i, (lab, start, dur, kind, indent) in enumerate(rows):
        y = y0 + i * row_h
        c.text(x_lab + indent, baseline(y, row_h), lab)
        bar(c, x0 + start * unit, y + (row_h - bar_h) / 2, dur * unit, bar_h, kind)
        ys.append(y)
    return ys


def fig_estrela_medicao():
    c = Canvas()
    rows = [
        ("Saúde (× N)", 1, 26, "agente", 0),
        ("Orçamento (× M)", 28, 22, "agente", 0),
        ("Orquestrador", 51, 8, "coord", 0),
        ("Contexto", 60, 10, "agente", 0),
        ("Analítico", 71, 14, "agente", 0),
        ("Priorização", 86, 16, "excl", 0),
        ("Síntese", 103, 30, "excl", 0),
        ("Verificação", 134, 14, "excl", 0),
    ]
    x_lab = 0.0
    x0 = max(tw(r[0]) for r in rows) + 12
    end = max(r[1] + r[2] for r in rows)
    unit = (RIGHT - x0 - 2) / end
    row_h = 17.0
    y_leg = 12.0
    lx = legend_item(c, 0, y_leg, "agente", "agentes (somados)")
    lx = legend_item(c, lx, y_leg, "coord", "overhead")
    legend_item(c, lx, y_leg, "excl", "fora da medição")
    y_br = y_leg + 30
    t_cap = 85.5
    bracket(c, x0, x0 + t_cap * unit, y_br, "wall-clock = Σ agentes + overhead")
    bracket(c, x0 + t_cap * unit, x0 + end * unit, y_br, "fora do wall-clock")
    y0 = y_br + 10
    ys = _gantt(c, rows, x_lab, x0, unit, row_h, y0)
    xb = x0 + t_cap * unit
    y_end = ys[-1] + row_h + 4
    c.line([(xb, y_br), (xb, y_end)], dash=True)
    c.text(xb, y_end + 4 + CAP, "capturar_wallclock", anchor="m")
    return c


# ---------------------------------------------------------------------------
# Árvore
# ---------------------------------------------------------------------------


def fig_arvore_pipeline_coordenador():
    c = Canvas()
    nodes = [
        Node("delegar_orcamento", ["SupervisorOrcamento", "retorna despesas", "e tendências"]),
        Node("delegar_saude", ["SupervisorSaude", "retorna indicadores"]),
        Node("combinar_despesas", ["deduplica orçamento", "+ saúde"]),
        Node("delegar_contexto", ["SupervisorContexto", "recebe: despesas"]),
        Node("delegar_analitico", ["SupervisorAnalitico", "recebe: despesas,", "indicadores, contexto"]),
        Node("persistir_metricas", ["supervisores", "+ agentes-folha"]),
    ]
    w = max(n.need_w for n in nodes)
    h = max(n.h for n in nodes)
    pos = _snake(nodes, 0, 0, 3, w, h, GX, GY)
    _link_snake(c, nodes, pos)
    for n in nodes:
        n.draw(c)
    return c


def fig_arvore_supervisores():
    """Supervisores de orçamento, saúde e contexto (o analítico tem figura própria)."""
    c = Canvas()
    linhas = [
        ("SupervisorOrcamento", [["consultar_orcamento", "× M subfunções"], ["agregar_resultados"]]),
        ("SupervisorSaude", [["consultar_saude", "× N sistemas"], ["agregar_resultados"]]),
        ("SupervisorContexto", [["executar_contexto_orcamentario", "AgenteContextoOrcamentario"]]),
    ]
    x_chain = max(tw(nome, "b") for nome, _ in linhas) + 16
    h = max(Node(None, ls).h for _, acts in linhas for ls in acts)
    y = 0.0
    for nome, acts in linhas:
        ns = [Node(None, ls, h=h) for ls in acts]   # largura natural; altura igual
        x = x_chain
        for n in ns:
            n.at(x, y)
            x = n.right + GX
        c.text(0, baseline(y, h), nome, weight="b")
        for a, b in zip(ns, ns[1:]):
            h_link(c, a, b)
        for n in ns:
            n.draw(c)
        y += h + 12
    return c


def fig_arvore_supervisor_analitico():
    c = Canvas()
    dentro = [Node(None, ["cruzar_dados"]), Node(None, ["detectar_gaps"]), Node(None, ["analisar"])]
    fora = [Node(None, ["priorizar_achados"]), Node(None, ["sintetizar_texto"]),
            Node(None, ["verificar_afirmacoes"], "opt")]
    w = max(n.need_w for n in dentro + fora)
    inner = 8.0
    fw = 3 * w + 2 * GX + 2 * inner
    h = max(n.h for n in dentro + fora)
    for k, n in enumerate(dentro):
        n.w, n.h = w, h
        n.at(inner + k * (w + GX), FRAME_TITLE)
    fa_h = FRAME_TITLE + h + 8
    frame(c, 0, 0, fw, fa_h, "Medido (até _coala_leaf_end_time)")
    cap = Node(None, ["capturar_wallclock"], w=w)
    gap = 34.0
    cap.at(dentro[-1].x, fa_h + (gap - cap.h) / 2)
    fb_y = fa_h + gap
    for k, n in enumerate(reversed(fora)):
        n.w, n.h = w, h
        n.at(inner + k * (w + GX), fb_y + FRAME_TITLE)
    fb_h = FRAME_TITLE + h + 8
    frame(c, 0, fb_y, fw, fb_h, "Fora do wall-clock")
    h_link(c, dentro[0], dentro[1])
    h_link(c, dentro[1], dentro[2])
    v_link(c, dentro[2], cap)
    v_link(c, cap, fora[0])
    h_link(c, fora[0], fora[1])
    h_link(c, fora[1], fora[2])
    for n in dentro + fora + [cap]:
        n.draw(c)
    return c


def fig_arvore_medicao():
    c = Canvas()
    rows = [
        ("CoordenadorGeral", 0, 152, "coord", 0),
        ("SupervisorOrcamento", 2, 24, "coord", 0),
        ("Orçamento (× M)", 3, 22, "agente", 12),
        ("SupervisorSaude", 28, 28, "coord", 0),
        ("Saúde (× N)", 29, 26, "agente", 12),
        ("SupervisorContexto", 61, 12, "coord", 0),
        ("Contexto", 62, 10, "agente", 12),
        ("SupervisorAnalitico", 75, 17, "coord", 0),
        ("Analítico", 76, 15, "agente", 12),
        ("Priorização", 93, 15, "excl", 12),
        ("Síntese", 109, 28, "excl", 12),
        ("Verificação", 138, 13, "excl", 12),
    ]
    x_lab = 0.0
    x0 = max(tw(r[0]) + r[4] for r in rows) + 12
    end = max(r[1] + r[2] for r in rows)
    unit = (RIGHT - x0 - 2) / end
    row_h = 15.0
    y_leg = 12.0
    lx = legend_item(c, 0, y_leg, "agente", "agentes-folha (somados)")
    lx = legend_item(c, lx, y_leg, "coord", "só exibidos")
    legend_item(c, lx, y_leg, "excl", "fora da medição")
    y_br = y_leg + 30
    t_cap = 92.0
    # o relógio do coordenador fecha em _coala_leaf_end_time, o mesmo ponto da estrela
    bracket(c, x0, x0 + t_cap * unit, y_br, "wall-clock da árvore")
    bracket(c, x0 + t_cap * unit, x0 + end * unit, y_br, "fora do wall-clock")
    y0 = y_br + 10
    ys = _gantt(c, rows, x_lab, x0, unit, row_h, y0)
    xb = x0 + t_cap * unit
    y_end = ys[-1] + row_h + 4
    c.line([(xb, y_br), (xb, y_end)], dash=True)
    c.text(xb, y_end + 4 + CAP, "_coala_leaf_end_time", anchor="m")
    return c


def fig_arvore_degradacao():
    c = Canvas()
    d = Node("delegar_saude", ["CoordenadorGeral"])
    s = Node("SupervisorSaude.run()", ["lança exceção"], "opt")
    k = Node("Captura a exceção", ["CoordenadorGeral"])
    e = Node("Evento error", ["enviado pelo WebSocket"], "ext")
    r = Node("Retorno vazio", ["despesas: []", "indicadores: []"], "data")
    p = Node("Pipeline continua", ["combinar_despesas", "delegar_contexto", "delegar_analitico"])
    out = Node("Resultado parcial", ["sem correlações", "nem anomalias"], "data")
    left, right = [d, k, r], [s, e, p, out]
    gy = GY - 6   # 4 linhas de caixas: vãos menores para caber em 9 cm
    wl = max(n.need_w for n in left)
    wr = max(n.need_w for n in right)
    xr = wl + 60
    for n in left:
        n.w = wl
    for n in right:
        n.w = wr
    d.at(0, 0)
    s.at(xr, 0)
    s.h = d.h = max(d.h, s.h)
    k.at(0, d.bottom + gy)
    e.at(xr, k.y)
    k.h = e.h = max(k.h, e.h)
    r.at(0, k.bottom + gy)
    p.at(xr, r.y)
    r.y = p.cy - r.h / 2
    out.at(xr, p.bottom + gy)
    h_link(c, d, s)
    arrow(c, [(s.x, s.bottom - 8), (k.right, k.y + 8)], dash=True)
    label(c, (s.x + k.right) / 2, (s.bottom + k.y) / 2 + CAP / 2, "exceção")
    h_link(c, k, e)
    v_link(c, k, r)
    h_link(c, r, p)
    v_link(c, p, out)
    for n in (d, s, k, e, r, p, out):
        n.draw(c)
    return c


FIGURES = {
    "01-estrela-pipeline": fig_estrela_pipeline,
    "02-estrela-medicao": fig_estrela_medicao,
    "03-arvore-pipeline-coordenador": fig_arvore_pipeline_coordenador,
    "04a-arvore-supervisores": fig_arvore_supervisores,
    "04b-arvore-supervisor-analitico": fig_arvore_supervisor_analitico,
    "05-arvore-medicao": fig_arvore_medicao,
    "06-arvore-degradacao": fig_arvore_degradacao,
}

if __name__ == "__main__":
    only = sys.argv[1:]
    for name, fn in FIGURES.items():
        if only and not any(o in name for o in only):
            continue
        w, h = fn().save(name, MAX_H, out_dir=HERE)
        print(f"{name}: {w / 72 * 2.54:.1f} x {h / 72 * 2.54:.1f} cm")
