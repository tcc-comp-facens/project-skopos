"""Gera os diagramas dos agentes CoALA (docs/02-AGENTES.md).

Mesmas regras dos diagramas do modelo de dados (../gerar_diagramas.py), que
seguem os exemplos do Manual de Textos Técnicos:

- largura <= 16 cm e altura <= 9 cm;
- todo o texto em Arial 12 pt — insira a imagem a 100% (o PNG carrega 300 dpi);
- nenhum título, fonte ou legenda dentro da imagem: isso fica no docx.

Convenções visuais:
- retângulo branco = processo/ação; retângulo cinza-claro arredondado = dado;
- retângulo cinza = componente externo (Neo4j, LLM, WebSocket, outro módulo);
- borda tracejada = etapa opcional ou de falha; seta tracejada = alternativa/retorno.

Uso (Windows, precisa só de Pillow e da Arial instalada):
    python docs/diagramas/agentes/gerar_diagramas_agentes.py

O conteúdo segue o código em backend/agents/ (base.py, domain/, analytical/,
context/, intent/, star/, hierarchical/ — a topologia em árvore) e backend/core/ — se o comportamento
de algum agente mudar, ajuste a figura correspondente e rode de novo.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from gerar_diagramas import (  # noqa: E402  (import depois de ajustar o sys.path)
    BLACK, CAP, GROUP_FILL, GROUP_STROKE, HEADER_FILL, MAX_W, PAD, ROW, Canvas, baseline, tw,
)

MAX_H = 255            # 9 cm
VPAD = 4               # espaço vertical interno dos nós
GX = 20                # espaço horizontal entre colunas (setas)
GY = 22                # espaço vertical entre linhas (setas)
HEAD_L, HEAD_W = 6.0, 3.2
FRAME_TITLE = 20.0
RIGHT = MAX_W - 2.5    # borda direita útil (folga para o traço)


# ---------------------------------------------------------------------------
# Elementos
# ---------------------------------------------------------------------------


class Node:
    """Caixa com título (negrito) e linhas de texto centralizadas.

    style: proc (processo), data (dado), ext (componente externo), opt (opcional).
    """

    def __init__(self, title, lines=(), style="proc", w=None, h=None):
        self.style = style
        self.texts = ([(title, "b")] if title else []) + [(ln, "r") for ln in lines]
        need = max(tw(t, wt) for t, wt in self.texts)
        self.w = w if w is not None else need + 2 * PAD
        if self.w < need + 2 * PAD - 0.01:
            raise ValueError(f"nó estreito demais: {self.texts[0][0]!r}")
        natural = len(self.texts) * ROW + 2 * VPAD
        self.h = max(h or 0, natural)
        self.x = self.y = 0.0

    @property
    def need_w(self):
        return max(tw(t, wt) for t, wt in self.texts) + 2 * PAD

    def at(self, x, y):
        self.x, self.y = x, y
        return self

    @property
    def right(self):
        return self.x + self.w

    @property
    def bottom(self):
        return self.y + self.h

    @property
    def cx(self):
        return self.x + self.w / 2

    @property
    def cy(self):
        return self.y + self.h / 2

    def draw(self, c: Canvas):
        x, y, w, h = self.x, self.y, self.w, self.h
        if self.style == "data":
            c.rect(x, y, w, h, fill=GROUP_FILL, stroke=BLACK, sw=1.0, r=6)
        elif self.style == "ext":
            c.rect(x, y, w, h, fill=HEADER_FILL, stroke=BLACK, sw=1.0)
        elif self.style == "opt":
            c.rect(x, y, w, h, fill="#FFFFFF", stroke=None)
            c.line([(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)], dash=True)
        else:
            c.rect(x, y, w, h, fill="#FFFFFF", stroke=BLACK, sw=1.0)
        top = y + (h - len(self.texts) * ROW) / 2
        for i, (t, wt) in enumerate(self.texts):
            c.text(self.cx, baseline(top + i * ROW, ROW), t, weight=wt, anchor="m")


def arrow(c, pts, dash=False, start=False, end=True):
    """Polilinha com ponta(s) de seta."""
    pts = [tuple(p) for p in pts]

    def head(tip, frm):
        dx, dy = tip[0] - frm[0], tip[1] - frm[1]
        ln = math.hypot(dx, dy)
        ux, uy = dx / ln, dy / ln
        bx, by = tip[0] - ux * HEAD_L, tip[1] - uy * HEAD_L
        px, py = -uy * HEAD_W, ux * HEAD_W
        c.poly([tip, (bx + px, by + py), (bx - px, by - py)])
        return (bx, by)

    line_pts = list(pts)
    if end:
        line_pts[-1] = head(pts[-1], pts[-2])
    if start:
        line_pts[0] = head(pts[0], pts[1])
    c.line(line_pts, dash=dash)


def label(c, x, yb, s, anchor="m", bg=True, weight="r"):
    """Texto com fundo branco (legível por cima de linhas)."""
    w = tw(s, weight)
    x0 = {"l": x, "m": x - w / 2, "r": x - w}[anchor]
    if bg:
        c.rect(x0 - 2, yb - CAP - 2.5, w + 4, CAP + 5.5, fill="#FFFFFF", stroke=None)
    c.text(x, yb, s, weight=weight, anchor=anchor)


def frame(c, x, y, w, h, title):
    c.rect(x, y, w, h, fill=GROUP_FILL, stroke=GROUP_STROKE, sw=0.75, r=5)
    c.text(x + 8, baseline(y + 2, FRAME_TITLE - 4), title, weight="b")


def h_link(c, a: Node, b: Node, y=None, text=None, dash=False, below=False):
    """Seta horizontal entre as bordas de a e b (na direção de b)."""
    y = a.cy if y is None else y
    if b.x >= a.right:
        x1, x2 = a.right, b.x
    else:
        x1, x2 = a.x, b.right
    arrow(c, [(x1, y), (x2, y)], dash=dash)
    if text:
        if tw(text) > abs(x2 - x1) - 6:
            raise ValueError(f"rótulo {text!r} não cabe em {abs(x2 - x1):.0f} pt")
        yb = y + 4 + CAP if below else y - 4
        c.text((x1 + x2) / 2, yb, text, anchor="m")


def v_link(c, a: Node, b: Node, x=None, text=None, side="r", dash=False, head=True):
    """Seta vertical de a para b; rótulo ao lado, no meio do vão."""
    x = a.cx if x is None else x
    if b.y >= a.bottom:
        y1, y2 = a.bottom, b.y
    else:
        y1, y2 = a.y, b.bottom
    if head:
        arrow(c, [(x, y1), (x, y2)], dash=dash)
    else:
        c.line([(x, y1), (x, y2)], dash=dash)
    if text:
        ym = (y1 + y2) / 2 + CAP / 2
        if side == "r":
            label(c, x + 6, ym, text, anchor="l", bg=False)
        else:
            label(c, x - 6, ym, text, anchor="r", bg=False)


def bus_down(c, src: Node, dsts: list[Node], y_bus: float):
    """Uma origem distribuindo para várias caixas abaixo (barramento ortogonal)."""
    c.line([(src.cx, src.bottom), (src.cx, y_bus)])
    xs = [d.cx for d in dsts] + [src.cx]
    c.line([(min(xs), y_bus), (max(xs), y_bus)])
    for d in dsts:
        arrow(c, [(d.cx, y_bus), (d.cx, d.y)])


def bus_in(c, srcs: list[Node], dst: Node, y_bus: float):
    """Várias caixas convergindo para uma abaixo."""
    for s in srcs:
        c.line([(s.cx, s.bottom), (s.cx, y_bus)])
    xs = [s.cx for s in srcs] + [dst.cx]
    c.line([(min(xs), y_bus), (max(xs), y_bus)])
    arrow(c, [(dst.cx, y_bus), (dst.cx, dst.y)])


def columns(cols: list[list[Node]], gap=GX, x0=0.0):
    """Ajusta a largura de cada coluna à maior caixa dela e devolve os x."""
    xs = []
    x = x0
    for col in cols:
        w = max(n.need_w for n in col)
        for n in col:
            n.w = w
            n.x = x
        xs.append(x)
        x += w + gap
    return xs


def uniform(nodes: list[Node]):
    w = max(n.need_w for n in nodes)
    h = max(n.h for n in nodes)
    for n in nodes:
        n.w, n.h = w, h
    return w, h


# ---------------------------------------------------------------------------
# Base CoALA
# ---------------------------------------------------------------------------


def fig_coala_estrutura():
    c = Canvas()
    mems = [
        Node("Memória de trabalho", ["working_memory", "volátil"], "data"),
        Node("Memória semântica", ["semantic_memory", "só leitura"], "data"),
        Node("Memória episódica", ["episodic_memory", "volátil"], "data"),
        Node("Memória procedural", ["procedural_memory", "só leitura"], "data"),
    ]
    uniform(mems)
    cyc = Node("Ciclo de decisão", ["perceive", "propose_actions", "evaluate_and_select",
                                     "execute", "_observe_and_learn"])
    inner = 8.0
    gx0, gy0 = inner, FRAME_TITLE
    mems[0].at(gx0, gy0)
    mems[1].at(mems[0].right + 6, gy0)
    mems[2].at(gx0, mems[0].bottom + 6)
    mems[3].at(mems[1].x, mems[2].y)
    grid_h = mems[2].bottom - gy0
    cyc.at(RIGHT - inner - cyc.w, gy0 + (grid_h - cyc.h) / 2)
    frame_h = FRAME_TITLE + grid_h + 8
    frame(c, 0, 0, RIGHT, frame_h, "AgenteCoALA (agents/base.py)")
    grid_right = mems[1].right
    ya, yb = mems[1].bottom - 10, mems[3].y + 10
    arrow(c, [(grid_right, ya), (cyc.x, ya)])
    c.text((grid_right + cyc.x) / 2, ya - 4, "lê", anchor="m")
    arrow(c, [(cyc.x, yb), (grid_right, yb)])
    c.text((grid_right + cyc.x) / 2, yb + 4 + CAP, "grava", anchor="m")
    for n in mems + [cyc]:
        n.draw(c)

    ay = frame_h + 34
    ext = [Node("Neo4j", style="ext"), Node("WebSocket", style="ext"),
           Node("Outros agentes", style="ext")]
    frame2_h = FRAME_TITLE + ext[0].h + 8
    frame(c, 0, ay, RIGHT, frame2_h, "Ambiente")
    total = sum(n.w for n in ext) + 2 * 24
    x = (RIGHT - total) / 2
    for n in ext:
        n.at(x, ay + FRAME_TITLE)
        x = n.right + 24
        n.draw(c)
    arrow(c, [(cyc.cx, cyc.bottom), (cyc.cx, ay)], start=True)
    label(c, cyc.cx - 6, (frame_h + ay) / 2 + CAP / 2, "ações externas (grounding)",
          anchor="r", bg=False)
    return c


def fig_coala_ciclo():
    c = Canvas()
    steps = [
        Node("Perceber", ["perceive()", "lê o estado"]),
        Node("Atualizar memória", ["update_working_memory", "incorpora a percepção"]),
        Node("Propor ações", ["propose_actions()", "candidatos (goals)"]),
        Node("Avaliar e selecionar", ["evaluate_and_select()", "escolhe o que executar"]),
        Node("Executar", ["execute()", "memória procedural"]),
        Node("Aprender", ["_observe_and_learn()", "memória episódica"]),
    ]
    w, h = uniform(steps)
    # serpentina em 2 colunas: 1 → 2 ↓ 3 ← … (cabe em 16 cm com update_working_memory)
    cx = [0, w + 2 * GX]
    pos = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 2), (1, 2)]
    for n, (col, row) in zip(steps, pos):
        n.at(cx[col], row * (h + GY))
    h_link(c, steps[0], steps[1])
    v_link(c, steps[1], steps[2])
    h_link(c, steps[2], steps[3])
    v_link(c, steps[3], steps[4])
    h_link(c, steps[4], steps[5])
    for n in steps:
        n.draw(c)
    return c


def fig_coala_recuperacao_falha():
    c = Canvas()
    a = Node("Ação selecionada", ["ex.: consultar_indicadores"])
    s1 = Node("Estratégia 1 (primária)", ["_act_consultar_indicadores"])
    s2 = Node("Estratégia 2 (fallback)", ["_act_fallback_indicadores", "grava lista vazia"])
    f = Node("Ação com falha", ["status = failed", "registra o episódio"], "data")
    for n in (a, s1, s2, f):
        n.w = max(m.need_w for m in (a, s1, s2, f))
    a.at(0, 0)
    s1.at(0, a.bottom + GY)
    s2.at(0, s1.bottom + GY)
    f.at(0, s2.bottom + GY)
    ok = Node("Ação concluída", ["status = completed", "registra o episódio"], "data",
              h=s2.bottom - s1.y)
    ok.at(s1.right + tw("sucesso") + 40, s1.y)
    v_link(c, a, s1)
    h_link(c, s1, ok, text="sucesso")
    v_link(c, s1, s2, text="ActionFailure")
    h_link(c, s2, ok, text="sucesso")
    v_link(c, s2, f, text="ActionFailure")
    for n in (a, s1, s2, f, ok):
        n.draw(c)
    return c


# ---------------------------------------------------------------------------
# Agente de interpretação de intenção
# ---------------------------------------------------------------------------


def fig_agente_intencao():
    c = Canvas()
    m = Node("Mensagem do chat", ["texto do usuário"], "data")
    e = Node("Memória episódica", ["buscar_memoria_episodica", "análises anteriores"])
    k = Node("Classificar escopo", ["classificar_escopo", "1 chamada ao LLM"])
    x = Node("Extrair parâmetros", ["extrair_parametros"])
    fb = Node("LLM indisponível", ["escopo = indisponivel", "pede nova tentativa"], "opt")
    outs = [
        Node("Fora do escopo", ["recusa escrita", "pelo LLM"], "data"),
        Node("Conversa", ["resposta direta,", "sem análise"], "data"),
        Node("Pergunta de dados", ["AnalysisIntent →", "dispatch_analysis()"], "data"),
    ]
    columns([[m], [e, fb], [k, x]])
    for n in (m, e, k):
        n.y = 0
    row1_h = max(n.h for n in (m, e, k))
    for n in (m, e, k):
        n.y = (row1_h - n.h) / 2
    fb.y = x.y = row1_h + GY
    x.h = fb.h
    h_link(c, m, e)
    h_link(c, e, k)
    v_link(c, k, x)
    sx, ex = k.x + 14, fb.right - 40
    arrow(c, [(sx, k.bottom), (ex, fb.y)], dash=True)
    # rótulo acima da diagonal, à esquerda do meio (longe da seta vertical k → x)
    label(c, (sx + ex) / 2 - 6, (k.bottom + fb.y) / 2 - 1, "falha", anchor="r", bg=False)
    uniform(outs)
    total = sum(o.w for o in outs) + 2 * GX
    ox = max(0.0, (k.right - total) / 2)
    y3 = x.bottom + 2 * GY
    for o in outs:
        o.at(ox, y3)
        ox = o.right + GX
    bus_down(c, x, outs, x.bottom + GY)
    for n in (m, e, k, x, fb, *outs):
        n.draw(c)
    return c


# ---------------------------------------------------------------------------
# Agentes de domínio
# ---------------------------------------------------------------------------


def fig_agente_saude():
    c = Canvas()
    p1 = Node("Perceber", ["analysis_id e período"])
    p2 = Node("Propor ações", ["candidatos por dimensão"])
    p3 = Node("Avaliar e selecionar", ["arbitrar_dimensao()"])
    c2 = Node("Candidatos (SINAN)", ["sem quebra", "POR_FAIXA_ETARIA", "POR_SEXO"], "data")
    c3 = Node("Critério", ["score da intenção", "+ LLM (se use_llm)", "falha → maior score"], "data")
    r1 = Node("Neo4j", ["IndicadorSaude"], "ext")
    r2 = Node("query_builder", ["Cypher parametrizado"])
    r3 = Node("Executar", ["consultar_indicadores", "falha → lista vazia"])
    columns([[p1, r1], [p2, c2, r2], [p3, c3, r3]])
    for n in (p1, p2, p3):
        n.y = 0
    row1_h = max(n.h for n in (p1, p2, p3))
    c2.y = c3.y = row1_h + 16
    c2.h = c3.h = max(c2.h, c3.h)
    row3 = c2.bottom + GY
    for n in (r1, r2, r3):
        n.y = row3
    r1.h = r2.h = r3.h = max(n.h for n in (r1, r2, r3))
    h_link(c, p1, p2)
    h_link(c, p2, p3)
    v_link(c, p2, c2, dash=True, head=False)
    v_link(c, p3, c3, dash=True, head=False)
    v_link(c, c3, r3)
    h_link(c, r3, r2)
    h_link(c, r2, r1)
    for n in (p1, p2, p3, c2, c3, r1, r2, r3):
        n.draw(c)
    return c


def fig_agentes_saude_sistemas():
    c = Canvas()
    data = [
        ("AgenteSINAN · sinan", ["POR_FAIXA_ETARIA", "POR_SEXO"]),
        ("AgenteSIM · sim", ["POR_FAIXA_ETARIA", "POR_CAPITULO_CID"]),
        ("AgenteSIH · sih", ["POR_CAPITULO_CID"]),
        ("AgenteSIPNI · sipni", ["COBERTURA_VACINAL", "DOSES_APLICADAS"]),
        ("AgenteSINASC · sinasc", ["POR_FAIXA_ETARIA", "POR_FAIXA_PESO"]),
        ("AgenteCNES · cnes", ["6 dimensões do CNES"]),
        ("AgenteSIA · sia", ["sem dimensão:", "consulta única"]),
        ("AgenteCOVID · covid", ["sem dimensão:", "consulta única"]),
    ]
    nodes = [Node(t, ls) for t, ls in data]
    w, h = uniform(nodes)
    w = (RIGHT - 12) / 2
    for i, n in enumerate(nodes):
        col, row = divmod(i, 4)
        n.w = w
        n.at(col * (w + 12), row * (h + 8))
        n.draw(c)
    return c


def fig_agente_orcamento():
    c = Canvas()
    p1 = Node("Perceber", ["subfunção e período"])
    p2 = Node("Propor ações", ["consultar_despesas (×3)", "analisar_tendencia",
                               "consultar_variacao_anual"])
    p3 = Node("Avaliar e selecionar", ["arbitra só", "consultar_despesas"])
    e1 = Node("Executar", ["consultar_despesas", "DespesaAnual (Neo4j)"])
    e3 = Node("Executar", ["consultar_variacao_anual", "falha → lista vazia"])
    e2 = Node("Executar", ["analisar_tendencia", "tendência e CAGR"])
    out = Node("Resultado", ["despesas, tendencia, variacao_anual"], "data")
    columns([[p1, e1], [p2, e3], [p3, e2]])
    for n in (p1, p2, p3):
        n.y = 0
    yr = 19.0                                   # setas da 1ª linha (caixas alinhadas no topo)
    arrow(c, [(p1.right, yr), (p2.x, yr)])
    arrow(c, [(p2.right, yr), (p3.x, yr)])
    row2 = p2.bottom + 2 * GY
    for n in (e1, e3, e2):
        n.y = row2
    bus_down(c, p3, [e1, e3, e2], p2.bottom + GY)
    out.at((e2.right - out.w) / 2, e1.bottom + 2 * GY)
    bus_in(c, [e1, e3, e2], out, e1.bottom + GY)
    for n in (p1, p2, p3, e1, e3, e2, out):
        n.draw(c)
    return c


# ---------------------------------------------------------------------------
# Agentes analíticos, contexto e síntese
# ---------------------------------------------------------------------------


def fig_agente_contexto():
    c = Canvas()
    d = Node("Despesas", ["por subfunção", "e ano"], "data")
    v = Node("Variação ano a ano", ["compute_yoy_variation"])
    cg = Node("Resumo do período", ["compute_cagr (CAGR)"])
    ct = Node("Classificação", ["classify_trend:", "crescimento, corte,", "estagnação ou",
                                "insuficiente"])
    out = Node("Resultado", ["tendencia", "variacao_media_percentual"], "data")
    columns([[d], [v, cg], [ct, out]])
    ct.y = 0
    v.y = ct.cy - v.h / 2
    out.y = ct.bottom + GY
    cg.y = out.cy - cg.h / 2
    d.y = (v.cy + cg.cy) / 2 - d.h / 2
    xm = d.right + GX / 2
    arrow(c, [(d.right, d.cy), (xm, d.cy), (xm, v.cy), (v.x, v.cy)])
    arrow(c, [(xm, d.cy), (xm, cg.cy), (cg.x, cg.cy)])
    h_link(c, v, ct)
    v_link(c, ct, out)
    h_link(c, cg, out)
    for n in (d, v, cg, ct, out):
        n.draw(c)
    return c


def fig_agente_analitico():
    c = Canvas()
    dc = Node("Dados cruzados", ["subfunção ×", "indicador × ano"], "data")
    cc = Node("Calcular correlação", ["calcular_correlacao", "Spearman por par"])
    sm = Node("Memória semântica", ["limiares 0,7 e 0,4", "polaridade"], "data")
    da = Node("Detectar anomalia", ["detectar_anomalia", "mediana por par"])
    oc = Node("Correlações", ["alta, média ou baixa", "+ leitura de polaridade"], "data")
    oa = Node("Anomalias", ["alto_gasto_baixo_resultado", "baixo_gasto_alto_resultado"], "data")
    columns([[dc], [cc, sm, da], [oc, oa]])
    cc.y = 0
    sm.y = cc.bottom + 16
    da.y = sm.bottom + 16
    oc.y = cc.cy - oc.h / 2
    oa.y = da.cy - oa.h / 2
    dc.y = sm.cy - dc.h / 2
    xm = dc.right + GX / 2
    arrow(c, [(dc.right, dc.cy), (xm, dc.cy), (xm, cc.cy), (cc.x, cc.cy)])
    arrow(c, [(xm, dc.cy), (xm, da.cy), (da.x, da.cy)])
    v_link(c, sm, cc, dash=True)
    v_link(c, sm, da, dash=True)
    h_link(c, cc, oc)
    h_link(c, da, oa)
    for n in (dc, cc, sm, da, oc, oa):
        n.draw(c)
    return c


def fig_agente_priorizacao():
    c = Canvas()
    i = Node("Achados", ["correlações,", "anomalias, contexto"], "data")
    pp = Node("Propor ações", ["1 candidato por ângulo"])
    ev = Node("Avaliar e selecionar", ["score + LLM", "(intent_summary)"])
    ang = Node("Ângulos", ["ineficiencias", "eficiencia", "correlacoes_fortes",
                           "tendencias_orcamentarias", "intencao_usuario"], "data")
    ex = Node("Executar", ["reordena as listas", "(nunca filtra)"])
    ts = Node("TextSynthesizer", ["recebe a ênfase"], "ext")
    columns([[i], [pp, ang], [ev, ex, ts]])
    for n in (i, pp, ev):
        n.y = 0
    row1_h = max(n.h for n in (i, pp, ev))
    for n in (i, pp, ev):
        n.y = (row1_h - n.h) / 2
    ang.y = row1_h + 16
    ex.y = row1_h + GY
    ts.y = ex.bottom + GY
    h_link(c, i, pp)
    h_link(c, pp, ev)
    v_link(c, pp, ang, dash=True, head=False)
    v_link(c, ev, ex)
    v_link(c, ex, ts)
    for n in (i, pp, ev, ang, ex, ts):
        n.draw(c)
    return c


def fig_sintese_verificacao():
    c = Canvas()
    i = Node("Entrada", ["achados priorizados,", "contexto e lacunas"], "data")
    ts = Node("TextSynthesizer", ["monta o prompt"])
    llm = Node("LLM", ["generate_stream"], "ext")
    fb = Node("Texto estruturado", ["fallback sem LLM"])
    sa = Node("StreamingAdapter", ["chunks → WebSocket"])
    columns([[i], [ts, fb], [llm, sa]])
    row1_h = max(n.h for n in (i, ts, llm))
    for n in (i, ts, llm):
        n.y = (row1_h - n.h) / 2
    fb.y = sa.y = row1_h + GY + 8
    h_link(c, i, ts)
    h_link(c, ts, llm)
    v_link(c, llm, sa)
    v_link(c, ts, fb, dash=True, text="LLM indisponível", side="l")
    h_link(c, fb, sa)

    vs = [Node("extract_claims", ["extrai afirmações"], "opt"),
          Node("verify_claims", ["confere com os dados"], "opt"),
          Node("revise_text", ["revisa uma vez"], "opt")]
    uniform(vs)
    fy = sa.bottom + 30
    frame_h = FRAME_TITLE + vs[0].h + 8
    frame(c, 0, fy, RIGHT, frame_h, "Verificação pós-síntese (opcional)")
    total = sum(v.w for v in vs) + 2 * GX
    vx = (RIGHT - total) / 2
    for v in vs:
        v.at(vx, fy + FRAME_TITLE)
        vx = v.right + GX
    h_link(c, vs[0], vs[1])
    h_link(c, vs[1], vs[2])
    arrow(c, [(sa.cx, sa.bottom), (sa.cx, fy)])
    label(c, sa.cx - 6, (sa.bottom + fy) / 2 + CAP / 2, "texto final", anchor="r", bg=False)
    for n in (i, ts, llm, fb, sa, *vs):
        n.draw(c)
    return c


# ---------------------------------------------------------------------------
# Topologias
# ---------------------------------------------------------------------------


def fig_topologia_estrela():
    c = Canvas()
    hub = Node("OrquestradorEstrela", ["cruzar_dados · detectar_gaps", "capturar_wallclock",
                                        "persistir_metricas"])
    top = [Node("1. Saúde", ["até 8 agentes"]), Node("2. Orçamento", ["até 7 agentes"]),
           Node("3. Contexto", ["tendências"])]
    bot = [Node("4. Analítico", ["estatística"]), Node("5. Priorização", ["ênfase (LLM)"]),
           Node("6. Sintetizador", ["texto (LLM)"]), Node("7. Verificação", ["opcional"], "opt")]
    w, h = uniform(top + bot)
    gap = (RIGHT - 4 * w) / 3
    for k, n in enumerate(bot):
        n.at(k * (w + gap), 0)
    span = 3 * w + 2 * gap
    x0 = (RIGHT - span) / 2
    for k, n in enumerate(top):
        n.at(x0 + k * (w + gap), 0)
    hub.at((RIGHT - hub.w) / 2, h + 30)
    for n in bot:
        n.y = hub.bottom + 30
    for n in top:
        xa = min(max(n.cx, hub.x + 12), hub.right - 12)
        arrow(c, [(xa, hub.y), (n.cx, n.bottom)], start=True)
    for n in bot:
        xa = min(max(n.cx, hub.x + 12), hub.right - 12)
        arrow(c, [(xa, hub.bottom), (n.cx, n.y)], start=True)
    for n in (hub, *top, *bot):
        n.draw(c)
    return c


def fig_topologia_arvore():
    c = Canvas()
    co = Node("CoordenadorGeral", ["3. combinar_despesas", "persistir_metricas"])
    sups = [Node("1. Supervisor", ["Orçamento"]), Node("2. Supervisor", ["Saúde"]),
            Node("4. Supervisor", ["Contexto"]), Node("5. Supervisor", ["Analítico"])]
    leaves = [Node("Orçamento", ["até 7 agentes"]), Node("Saúde", ["até 8 agentes"]),
              Node("Contexto", ["orçamentário"]),
              Node("Analítico", ["Priorização", "Sintetizador", "Verificação"])]
    w = max(n.need_w for n in sups + leaves)
    gap = (RIGHT - 4 * w) / 3
    co.at((RIGHT - co.w) / 2, 0)
    for k, (s, lf) in enumerate(zip(sups, leaves)):
        s.w = lf.w = w
        s.at(k * (w + gap), co.bottom + 2 * GY)
        lf.at(s.x, 0)
    for s, lf in zip(sups, leaves):
        lf.y = s.bottom + GY
    bus_down(c, co, sups, co.bottom + GY)
    for s, lf in zip(sups, leaves):
        v_link(c, s, lf)
    for n in (co, *sups, *leaves):
        n.draw(c)
    return c


def fig_arvore_fluxo():
    """Sequência de chamadas do CoordenadorGeral: dados só passam pelo pai."""
    c = Canvas()
    parts = [Node("Coordenador", ["Geral"]), Node("Supervisor", ["Saúde"]),
             Node("Supervisor", ["Contexto"]), Node("Supervisor", ["Orçamento"]),
             Node("Supervisor", ["Analítico"])]
    hh = max(p.h for p in parts)
    # cabeçalhos com espaço interno menor (4 pt) para caberem 5 participantes em 16 cm
    wc = tw("Coordenador", "b") + 8
    ws = tw("Supervisor", "b") + 8
    parts[0].w = wc
    for p in parts[1:]:
        p.w = ws
    for p in parts:
        p.h = hh
    x0 = wc / 2
    x_last = RIGHT - ws / 2
    x1 = max(x0 + tw("2. delegar_saude") + 10, x0 + wc / 2 + ws / 2 + 10)
    step = (x_last - x1) / 3
    if step < ws + 8:
        raise ValueError("cabeçalhos do diagrama de sequência se sobrepõem")
    xs = [x0, x1, x1 + step, x1 + 2 * step, x_last]
    for p, x in zip(parts, xs):
        p.at(x - p.w / 2, 0)
    msgs = [
        (0, 3, "1. delegar_orcamento", False), (3, 0, "despesas, tendências", True),
        (0, 1, "2. delegar_saude", False), (1, 0, "indicadores", True),
        ("self", "3. combinar_despesas"),
        (0, 2, "4. delegar_contexto(despesas)", False), (2, 0, "contexto orçamentário", True),
        (0, 4, "5. delegar_analitico(despesas, indicadores, contexto)", False),
        (4, 0, "correlações, anomalias, texto", True),
    ]
    y = hh + 18
    ys = []
    for msg in msgs:
        ys.append(y)
        y += 26 if msg[0] == "self" else 19
    y_end = y - 6
    for x in xs:
        c.line([(x, hh), (x, y_end)], dash=True, sw=0.75)
    for msg, yy in zip(msgs, ys):
        if msg[0] == "self":
            arrow(c, [(x0, yy - 6), (x0 + 18, yy - 6), (x0 + 18, yy + 6), (x0, yy + 6)])
            label(c, x0 + 24, yy + CAP / 2, msg[1], anchor="l")
            continue
        a, b, text, dash = msg
        xa, xb = xs[a], xs[b]
        if tw(text) > abs(xb - xa) - 8:
            raise ValueError(f"rótulo {text!r} não cabe")
        arrow(c, [(xa, yy), (xb, yy)], dash=dash)
        label(c, (xa + xb) / 2, yy - 4, text)
    for p in parts:
        p.draw(c)
    return c


FIGURES = {
    "01-coala-estrutura": fig_coala_estrutura,
    "02-coala-ciclo": fig_coala_ciclo,
    "03-coala-recuperacao-falha": fig_coala_recuperacao_falha,
    "04-agente-intencao": fig_agente_intencao,
    "05-agente-saude": fig_agente_saude,
    "06-agentes-saude-sistemas": fig_agentes_saude_sistemas,
    "07-agente-orcamento": fig_agente_orcamento,
    "08-agente-contexto": fig_agente_contexto,
    "09-agente-analitico": fig_agente_analitico,
    "10-agente-priorizacao": fig_agente_priorizacao,
    "11-sintese-verificacao": fig_sintese_verificacao,
    "12-topologia-estrela": fig_topologia_estrela,
    "13-topologia-arvore": fig_topologia_arvore,
    "14-arvore-fluxo": fig_arvore_fluxo,
}

if __name__ == "__main__":
    only = sys.argv[1:]
    for name, fn in FIGURES.items():
        if only and not any(o in name for o in only):
            continue
        w, h = fn().save(name, MAX_H, out_dir=HERE)
        print(f"{name}: {w / 72 * 2.54:.1f} x {h / 72 * 2.54:.1f} cm")
