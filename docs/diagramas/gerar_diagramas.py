"""Gera os diagramas do modelo de dados Neo4j (docs/03-DADOS-ETL.md).

Feitos para a monografia, no tamanho dos exemplos do Manual de Textos
Técnicos (figura centralizada ocupando cerca de 1/4 a 1/3 da página):

- largura <= 16 cm (largura útil do A4 com margens ABNT de 3 cm e 2 cm);
- altura <= 9 cm nas figuras detalhadas (visão geral: <= 11,3 cm);
- todo o texto em Arial 12 pt, sem redução — insira a imagem a 100%
  (o PNG carrega 300 dpi, então o Word já o coloca no tamanho físico certo);
- nenhum título, fonte ou legenda dentro da imagem: isso fica no docx.

As entidades mostram só os atributos principais; "…" indica atributos
omitidos (a lista completa está nas seções de modelo do docs/03).

Saída, para cada figura: <nome>.png (300 dpi) e <nome>.svg (vetorial).

Uso (Windows, precisa só de Pillow e da Arial instalada):
    python docs/diagramas/gerar_diagramas.py

As propriedades e relacionamentos vêm de backend/etl/orcamento_loader.py,
backend/etl/saude_indicadores_loader.py e backend/db/neo4j_client.py —
se o modelo mudar, ajuste as definições abaixo e rode de novo.
"""

from __future__ import annotations

import math
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).resolve().parent
FONT_DIR = Path("C:/Windows/Fonts")
FONTS = {"r": "arial.ttf", "b": "arialbd.ttf", "i": "ariali.ttf"}

PT = 12                    # tamanho da fonte, em pontos
DPI = 300
SCALE = DPI / 72           # px por pt no PNG
MAX_W = 453                # 16 cm
MAX_H_DETALHE = 255        # 9 cm
MAX_H_GERAL = 320          # 11,3 cm (visão geral: 25 entidades)
ROW = 15                   # altura de linha de tabela (1,25 x fonte)
NAME_H = 16                # altura das caixas só com nome (visão geral)
PAD = 6                    # espaço interno horizontal das caixas
COL_GAP = 12               # espaço entre nome do atributo e tipo
CAP = 0.716 * PT           # altura das maiúsculas da Arial
EDGE_M = 16                # folga de cada lado do rótulo de um relacionamento
STROKE = 1.0
ELLIPSIS = ("…", "", False)

BLACK = "#000000"
GRAY_TXT = "#4D4D4D"
HEADER_FILL = "#E3E3E3"
GROUP_FILL = "#F4F4F4"
GROUP_STROKE = "#9A9A9A"

_measure = {k: ImageFont.truetype(str(FONT_DIR / f), 1000) for k, f in FONTS.items()}
_draw_fonts = {k: ImageFont.truetype(str(FONT_DIR / f), round(PT * SCALE)) for k, f in FONTS.items()}


def tw(text: str, weight: str = "r") -> float:
    """Largura do texto em pt, em Arial 12."""
    return _measure[weight].getlength(text) * PT / 1000


def baseline(top: float, height: float) -> float:
    """Linha de base que centraliza verticalmente as maiúsculas numa faixa."""
    return top + (height + CAP) / 2


# ---------------------------------------------------------------------------
# Canvas mínimo que grava o mesmo desenho em SVG e em PNG
# ---------------------------------------------------------------------------


class Canvas:
    def __init__(self) -> None:
        self.ops: list[tuple] = []
        self.max_x = 0.0
        self.max_y = 0.0

    def _grow(self, x: float, y: float) -> None:
        self.max_x = max(self.max_x, x)
        self.max_y = max(self.max_y, y)

    def rect(self, x, y, w, h, fill=None, stroke=BLACK, sw=0.75, r=0.0):
        self.ops.append(("rect", x, y, w, h, fill, stroke, sw, r))
        self._grow(x + w + sw, y + h + sw)

    def line(self, pts, stroke=BLACK, sw=STROKE, dash=False):
        self.ops.append(("line", list(pts), stroke, sw, dash))
        for x, y in pts:
            self._grow(x + sw, y + sw)

    def poly(self, pts, fill=BLACK):
        """Polígono preenchido (ponta de seta)."""
        self.ops.append(("poly", list(pts), fill))
        for x, y in pts:
            self._grow(x + 0.5, y + 0.5)

    def text(self, x, y, s, weight="r", fill=BLACK, anchor="l"):
        """(x, y) = ponto na linha de base; anchor l/m/r."""
        self.ops.append(("text", x, y, s, weight, fill, anchor))
        w = tw(s, weight)
        x0 = {"l": x, "m": x - w / 2, "r": x - w}[anchor]
        if x0 < -0.01:
            raise ValueError(f"texto sai pela esquerda: {s!r}")
        self._grow(x0 + w, y + 3)

    # -- saída ------------------------------------------------------------

    def size(self) -> tuple[float, float]:
        return math.ceil(self.max_x + 1), math.ceil(self.max_y + 1)

    def save(self, name: str, max_h: float, out_dir: Path | None = None) -> tuple[float, float]:
        w, h = self.size()
        if w > MAX_W or h > max_h:
            raise ValueError(f"{name}: {w}x{h} pt excede {MAX_W}x{max_h} pt")
        out_dir = out_dir or OUT_DIR
        self._save_svg(out_dir / f"{name}.svg", w, h)
        self._save_png(out_dir / f"{name}.png", w, h)
        return w, h

    def _save_svg(self, path: Path, w: float, h: float) -> None:
        out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}pt" height="{h}pt" '
            f'viewBox="0 0 {w} {h}" font-family="Arial, Helvetica, sans-serif" font-size="{PT}">',
            f'<rect x="0" y="0" width="{w}" height="{h}" fill="#FFFFFF"/>',
        ]
        for op in self.ops:
            kind = op[0]
            if kind == "rect":
                _, x, y, rw, rh, fill, stroke, sw, r = op
                out.append(
                    f'<rect x="{x:.2f}" y="{y:.2f}" width="{rw:.2f}" height="{rh:.2f}" rx="{r}" '
                    f'fill="{fill or "none"}" stroke="{stroke or "none"}" stroke-width="{sw}"/>'
                )
            elif kind == "line":
                _, pts, stroke, sw, dash = op
                d = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
                da = ' stroke-dasharray="4,3"' if dash else ""
                out.append(f'<polyline points="{d}" fill="none" stroke="{stroke}" stroke-width="{sw}"{da}/>')
            elif kind == "poly":
                _, pts, fill = op
                d = " ".join(f"{x:.2f},{y:.2f}" for x, y in pts)
                out.append(f'<polygon points="{d}" fill="{fill}" stroke="none"/>')
            else:
                _, x, y, s, weight, fill, anchor = op
                ta = {"l": "start", "m": "middle", "r": "end"}[anchor]
                fw = ' font-weight="bold"' if weight == "b" else ""
                fs = ' font-style="italic"' if weight == "i" else ""
                out.append(
                    f'<text x="{x:.2f}" y="{y:.2f}" text-anchor="{ta}" fill="{fill}"{fw}{fs}>{escape(s)}</text>'
                )
        out.append("</svg>")
        path.write_text("\n".join(out), encoding="utf-8")

    def _save_png(self, path: Path, w: float, h: float) -> None:
        img = Image.new("RGB", (math.ceil(w * SCALE), math.ceil(h * SCALE)), "white")
        d = ImageDraw.Draw(img)

        def p(v):
            return v * SCALE

        for op in self.ops:
            kind = op[0]
            if kind == "rect":
                _, x, y, rw, rh, fill, stroke, sw, r = op
                box = [p(x), p(y), p(x + rw), p(y + rh)]
                width = max(1, round(p(sw))) if stroke else 0
                if r:
                    d.rounded_rectangle(box, radius=p(r), fill=fill, outline=stroke, width=width)
                else:
                    d.rectangle(box, fill=fill, outline=stroke, width=width)
            elif kind == "line":
                _, pts, stroke, sw, dash = op
                width = max(1, round(p(sw)))
                if not dash:
                    d.line([(p(x), p(y)) for x, y in pts], fill=stroke, width=width, joint="curve")
                    continue
                on, off = 4.0, 3.0
                for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
                    seg = math.hypot(x2 - x1, y2 - y1)
                    pos = 0.0
                    while pos < seg:
                        a, b = pos, min(pos + on, seg)
                        ax, ay = x1 + (x2 - x1) * a / seg, y1 + (y2 - y1) * a / seg
                        bx, by = x1 + (x2 - x1) * b / seg, y1 + (y2 - y1) * b / seg
                        d.line([(p(ax), p(ay)), (p(bx), p(by))], fill=stroke, width=width)
                        pos += on + off
            elif kind == "poly":
                _, pts, fill = op
                d.polygon([(p(x), p(y)) for x, y in pts], fill=fill)
            else:
                _, x, y, s, weight, fill, anchor = op
                d.text((p(x), p(y)), s, font=_draw_fonts[weight], fill=fill, anchor=anchor + "s")
        img.save(path, dpi=(DPI, DPI))


# ---------------------------------------------------------------------------
# Elementos de diagrama
# ---------------------------------------------------------------------------


class Entity:
    """Tabela de entidade: cabeçalho com o label do nó + linhas atributo/tipo.

    attrs: lista de (nome, tipo, chave). A chave aparece sublinhada (notação ER).
    Sem attrs, desenha só o cabeçalho (usado na visão geral, com altura NAME_H).
    """

    def __init__(self, name, attrs=(), width=None):
        self.name = name
        self.attrs = list(attrs)
        need = tw(name, "b")
        for a, t, _ in self.attrs:
            need = max(need, tw(a) + (COL_GAP + tw(t) if t else 0))
        self.w = width if width is not None else need + 2 * PAD
        if self.w < need + 2 * PAD - 0.01:
            raise ValueError(f"caixa estreita demais para {name}")
        self.header_h = ROW if self.attrs else NAME_H
        self.h = self.header_h + ROW * len(self.attrs)
        self.x = self.y = 0.0

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
    def cy(self):
        return self.y + self.h / 2

    @property
    def cx(self):
        return self.x + self.w / 2

    def draw(self, c: Canvas):
        c.rect(self.x, self.y, self.w, self.header_h, fill=HEADER_FILL, stroke=None)
        c.text(self.cx, baseline(self.y, self.header_h), self.name, weight="b", anchor="m")
        for i, (a, t, key) in enumerate(self.attrs):
            yb = baseline(self.y + self.header_h + i * ROW, ROW)
            c.text(self.x + PAD, yb, a)
            if key:
                c.line([(self.x + PAD, yb + 1.6), (self.x + PAD + tw(a), yb + 1.6)], sw=0.75)
            if t:
                c.text(self.right - PAD, yb, t, fill=GRAY_TXT, anchor="r")
        if self.attrs:
            c.line([(self.x, self.y + self.header_h), (self.right, self.y + self.header_h)], sw=0.75)
        c.rect(self.x, self.y, self.w, self.h, fill=None, stroke=BLACK, sw=STROKE)


def h_edge(c, x1, x2, y, label=None, card1=None, card2=None, dash=False):
    """Relacionamento horizontal: nome acima da linha, cardinalidades abaixo das pontas."""
    if x2 < x1:
        raise ValueError("h_edge espera x1 < x2")
    c.line([(x1, y), (x2, y)], dash=dash)
    if label:
        if tw(label) > (x2 - x1) - 8:
            raise ValueError(f"rótulo {label!r} não cabe em {x2 - x1:.0f} pt")
        c.text((x1 + x2) / 2, y - 4, label, anchor="m")
    if card1:
        c.text(x1 + 4, y + 12, card1)
    if card2:
        c.text(x2 - 4, y + 12, card2, anchor="r")


def seg_len(labels):
    """Comprimento de aresta que acomoda o maior rótulo com folga para as cardinalidades."""
    return max(tw(lb) for lb in labels) + 2 * EDGE_M


def straight(c, a: Entity, b: Entity, dash=False):
    """Linha reta entre as bordas de `a` e `b` (visão geral)."""
    if b.x >= a.right:
        p1, p2 = (a.right, a.cy), (b.x, b.cy)
    elif a.x >= b.right:
        p1, p2 = (a.x, a.cy), (b.right, b.cy)
    elif b.y >= a.bottom:
        p1, p2 = (a.cx, a.bottom), (b.cx, b.y)
    else:
        p1, p2 = (a.cx, a.y), (b.cx, b.bottom)
    c.line([p1, p2], dash=dash)


def rel_figure(source: Entity, column: list, card_src: str, card_dst: str, top: bool = False):
    """Entidade de origem ligada a uma coluna de entidades por um tronco vertical.

    column: itens (Entity, [rótulos]) ligados ao tronco — dois rótulos desenham
    dois relacionamentos para a mesma entidade —, ou (Entity, ("sub", rótulo,
    card_pai, card_filho)) para uma entidade pendurada embaixo da anterior por
    um relacionamento vertical (ex.: Acao -[:PARTE_DE]-> ProgramaGoverno).

    top=False: origem à esquerda, tronco logo à direita dela.
    top=True: origem no topo e tronco descendo da base dela — usado quando os
    rótulos são longos demais para caber ao lado da origem.
    """
    c = Canvas()
    labels = [lb for _, rels in column if isinstance(rels, list) for lb in rels]
    wt = max(e.w for e, _ in column)
    if top:
        source.at(0, 0)
        xt = 24.0
        y = source.bottom + 22
    else:
        xt = None
        y = 0.0
    x_col = (xt if top else 0) + seg_len(labels)   # provisório se side; ajustado abaixo
    placed = []
    for e, rels in column:
        e = Entity(e.name, e.attrs, width=wt)
        if isinstance(rels, tuple):              # pendurada embaixo da anterior
            e.at(0, placed[-1][0].bottom + 30)
        else:
            extra = 6 if len(rels) == 2 else 0   # folga para o rótulo do 1º de dois relacionamentos
            e.at(0, y if not placed else placed[-1][0].bottom + 12 + extra)
        placed.append((e, rels))
    if not top:
        connected = [e for e, r in placed if isinstance(r, list)]
        span_top, span_bot = connected[0].y, connected[-1].bottom
        source.at(0, max(0.0, (span_top + span_bot) / 2 - source.h / 2))
        xt = source.right + 16
        x_col = xt + seg_len(labels)
    for e, _ in placed:
        e.x = x_col

    ys = []
    for e, rels in placed:
        if isinstance(rels, tuple):
            continue
        if len(rels) == 1:
            ys_e = [e.cy]
        else:
            ys_e = [e.y + 4, e.bottom - 4]
        for yy, rel in zip(ys_e, rels):
            h_edge(c, xt, e.x, yy, rel, None, card_dst)
            ys.append(yy)
    if top:
        c.line([(xt, source.bottom), (xt, max(ys))])
        c.text(xt + 5, source.bottom + 12, card_src)
    else:
        c.line([(source.right, source.cy), (xt, source.cy)])
        c.line([(xt, min(ys + [source.cy])), (xt, max(ys + [source.cy]))])
        c.text(source.right + 3, source.cy + 12, card_src)

    for i, (e, rels) in enumerate(placed):
        if not isinstance(rels, tuple):
            continue
        _, rel, card_pai, card_filho = rels
        pai = placed[i - 1][0]
        vx = pai.x + 20
        c.line([(vx, pai.bottom), (vx, e.y)])
        c.text(vx + 6, (pai.bottom + e.y) / 2 + CAP / 2, rel)
        c.text(vx - 5, pai.bottom + 12, card_pai, anchor="r")
        c.text(vx - 5, e.y - 3, card_filho, anchor="r")

    source.draw(c)
    for e, _ in placed:
        e.draw(c)
    return c


# ---------------------------------------------------------------------------
# Definição do modelo — só atributos principais ("…" = há omitidos).
# Fonte: loaders do ETL e neo4j_client.
# ---------------------------------------------------------------------------

S, I, F, D = "string", "int", "float", "date"

EMPENHO = [
    ("id", S, True), ("dataEmpenho", D, False), ("subfuncaoCodigo", I, False),
    ("natureza", S, False), ("valorProcessado", F, False), ("valorPago", F, False), ELLIPSIS,
]
EMPENHO_COMPACTO = [("id", S, True), ELLIPSIS]
DESPESA_ANUAL = [
    ("subfuncaoCodigo", I, True), ("ano", I, True), ("valorProcessado", F, False),
    ("valorProprio", F, False), ("valorFederal", F, False), ("valorEstadual", F, False), ELLIPSIS,
]
ORCAMENTO_PROGRAMA = [
    ("programa", S, True), ("acao", S, True), ("orgao", S, True), ("periodoOrigem", S, True),
    ("valorPrevisto", F, False), ("valorRealizado", F, False), ELLIPSIS,
]
INDICADOR = [
    ("chave", S, True), ("sistema", S, False), ("subtipo", S, False), ("ano", I, False),
    ("valorTotal", F, False), ELLIPSIS,
]
INDICADOR_COMPACTO = [("chave", S, True), ("sistema", S, False), ELLIPSIS]
ANALISE = [
    ("id", S, True), ("dateFrom", I, False), ("dateTo", I, False), ("healthParams", S, False),
    ("sourceQuestion", S, False), ("starStatus", S, False), ("hierStatus", S, False), ELLIPSIS,
]
METRICA = [
    ("id", S, True), ("architecture", S, False), ("agentType", S, False),
    ("executionTimeMs", F, False), ("cpuPercent", F, False), ELLIPSIS,
]


def key(name, attr="chave", typ=S):
    """Dimensão representada só pela chave."""
    return Entity(name, [(attr, typ, True)])


# ---------------------------------------------------------------------------
# Figura — visão geral (só nomes)
# ---------------------------------------------------------------------------


def fig_visao_geral():
    c = Canvas()
    gx, gw = 0.0, MAX_W - 2.5
    inner = 8.0
    title_h = 19.0      # faixa do título do grupo
    BH = NAME_H

    def col_width(names):
        return max(tw(n, "b") for n in names) + 2 * PAD

    def column(names, x, y0, w, gap):
        return [Entity(n, width=w).at(x, y0 + i * (BH + gap)) for i, n in enumerate(names)]

    def group(y, h, title):
        c.rect(gx, y, gw, h, fill=GROUP_FILL, stroke=GROUP_STROKE, sw=0.75, r=5)
        c.text(gx + inner, baseline(y + 2, title_h - 4), title, weight="b")

    # --- Orçamento ---
    y0 = 0.0
    col1 = ["Fornecedor", "FonteRecurso", "Subfuncao", "UnidadeOrcamentaria"]
    col3 = ["Acao", "ProgramaGoverno", "Aplicacao", "Natureza"]
    w1 = col_width(col1 + ["OrcamentoPrograma"])
    w3 = col_width(col3)
    w2 = col_width(["Empenho", "DespesaAnual"])
    x1, x3 = gx + inner, gx + gw - inner - w3
    x2 = (x1 + w1 + x3) / 2 - w2 / 2
    top = y0 + title_h
    b1 = column(col1, x1, top, w1, gap=4)
    empenho = Entity("Empenho", width=w2).at(x2, (b1[0].y + b1[-1].bottom) / 2 - BH / 2)
    acao = Entity("Acao", width=w3).at(x3, top)
    programa = Entity("ProgramaGoverno", width=w3).at(x3, acao.bottom + 5)
    aplicacao = Entity("Aplicacao", width=w3).at(x3, programa.bottom + 10)
    natureza = Entity("Natureza", width=w3).at(x3, aplicacao.bottom + 10)
    despesa = Entity("DespesaAnual", width=w2).at(x2, (aplicacao.y + natureza.bottom) / 2 - BH / 2)
    orc_prog = Entity("OrcamentoPrograma", width=w1).at(x1, max(natureza.y, b1[-1].bottom + 4))
    g1_h = max(natureza.bottom, orc_prog.bottom) + 5 - y0
    group(y0, g1_h, "Orçamento (LAI)")
    for b in b1:
        straight(c, empenho, b)
    straight(c, empenho, acao)
    straight(c, empenho, aplicacao)
    c.line([(acao.x + 18, acao.bottom), (acao.x + 18, programa.y)])
    c.line([(empenho.cx, empenho.bottom), (despesa.cx, despesa.y)], dash=True)
    straight(c, despesa, aplicacao)
    straight(c, despesa, natureza)
    lx = despesa.x - 11    # laço VARIACAO_ANUAL
    c.line([(despesa.x, despesa.y + 4), (lx, despesa.y + 4), (lx, despesa.bottom - 4),
            (despesa.x, despesa.bottom - 4)])
    for e in b1 + [empenho, acao, programa, aplicacao, natureza, despesa, orc_prog]:
        e.draw(c)

    # --- Saúde ---
    gap_dom = 16.0
    y1 = y0 + g1_h + gap_dom
    left = ["FaixaEtaria", "CapituloCID10", "Sexo", "FaixaPeso", "Vacina"]
    right = ["TipoEstabelecimento", "OcupacaoProfissional", "TipoEquipe",
             "TipoAtendimento", "TipoLeitoConsultorio", "TipoEquipamento"]
    wl, wr = col_width(left), col_width(right)
    wi = col_width(["IndicadorSaude"])
    xl, xr = gx + inner, gx + gw - inner - wr
    xi = (xl + wl + xr) / 2 - wi / 2
    top = y1 + title_h
    br = column(right, xr, top, wr, gap=3)
    span = br[-1].bottom - br[0].y
    bl = column(left, xl, top, wl, gap=(span - len(left) * BH) / (len(left) - 1))
    ind = Entity("IndicadorSaude", width=wi).at(xi, (br[0].y + br[-1].bottom) / 2 - BH / 2)
    g2_h = br[-1].bottom + 5 - y1
    group(y1, g2_h, "Saúde (DATASUS/TabNet)")
    for b in bl + br:
        straight(c, ind, b)
    for e in bl + br + [ind]:
        e.draw(c)

    # cruzamento lógico entre os domínios (sem relacionamento no grafo)
    c.line([(despesa.cx, despesa.bottom), (despesa.cx, ind.y)], dash=True)
    c.text(despesa.cx + 6, baseline(y0 + g1_h, gap_dom), "cruzamento em memória")

    # --- Execução ---
    y2 = y1 + g2_h + 6
    analise = Entity("Analise").at(gx + inner, y2 + title_h)
    metrica = Entity("MetricaExecucao")
    metrica.at(gx + gw - inner - metrica.w, y2 + title_h)
    g3_h = analise.bottom + 5 - y2
    group(y2, g3_h, "Execução das análises")
    straight(c, analise, metrica)
    analise.draw(c)
    metrica.draw(c)
    return c


# ---------------------------------------------------------------------------
# Figuras detalhadas
# ---------------------------------------------------------------------------


def fig_orcamento_empenho_classificacao():
    return rel_figure(
        Entity("Empenho", EMPENHO),
        [
            (key("Fornecedor", "documento"), ["DO_FORNECEDOR"]),
            (Entity("FonteRecurso", [("nome", S, True), ("tipoRecurso", S, False)]), ["FINANCIADO_POR"]),
            (key("Subfuncao", "codigo", I), ["CLASSIFICADO_EM"]),
            (key("UnidadeOrcamentaria", "nome"), ["DA_UNIDADE"]),
        ],
        card_src="N", card_dst="1",
    )


def fig_orcamento_empenho_acao():
    return rel_figure(
        Entity("Empenho", EMPENHO_COMPACTO),
        [
            (key("Aplicacao", "nome"), ["APLICADO_EM"]),
            (key("Acao", "codigo"), ["EXECUTA_ACAO"]),
            (key("ProgramaGoverno", "nome"), ("sub", "PARTE_DE", "N", "1")),
        ],
        card_src="N", card_dst="1",
    )


def fig_orcamento_agregados():
    c = Canvas()
    desp = Entity("DespesaAnual", DESPESA_ANUAL)
    right = [Entity("Empenho", EMPENHO_COMPACTO), key("Aplicacao", "nome"), key("Natureza", "nome")]
    wr = max(e.w for e in right)
    emp, apl, nat = (Entity(e.name, e.attrs, width=wr) for e in right)
    rels = ["agregação", "POR_APLICACAO", "POR_NATUREZA"]
    xr = desp.w + seg_len(rels)
    emp.at(xr, 0)
    apl.at(xr, emp.bottom + 12)
    nat.at(xr, apl.bottom + 12)
    desp.at(0, max(0.0, (emp.y + nat.bottom) / 2 - desp.h / 2))
    for e in (emp, apl, nat):
        if not (desp.y + 4 <= e.cy <= desp.bottom - 4):
            raise ValueError(f"{e.name} fora da altura de DespesaAnual")
    h_edge(c, desp.right, emp.x, emp.cy, "agregação", "1", "N", dash=True)
    h_edge(c, desp.right, apl.x, apl.cy, "POR_APLICACAO", "N", "N")
    h_edge(c, desp.right, nat.x, nat.cy, "POR_NATUREZA", "N", "N")
    # auto-relacionamento VARIACAO_ANUAL (laço abaixo da tabela)
    x1, x2 = desp.x + 24, desp.right - 24
    yl = desp.bottom + 18
    c.line([(x1, desp.bottom), (x1, yl), (x2, yl), (x2, desp.bottom)])
    c.text((x1 + x2) / 2, yl + 4 + CAP, "VARIACAO_ANUAL", anchor="m")
    c.text(x1 + 4, desp.bottom + 12, "0..1")
    c.text(x2 - 4, desp.bottom + 12, "0..1", anchor="r")
    for e in (desp, emp, apl, nat):
        e.draw(c)
    return c


def fig_orcamento_previsto_realizado():
    c = Canvas()
    Entity("OrcamentoPrograma", ORCAMENTO_PROGRAMA).at(0, 0).draw(c)
    return c


def fig_saude_dimensoes():
    return rel_figure(
        Entity("IndicadorSaude", INDICADOR),
        [
            (key("FaixaEtaria"), ["POR_FAIXA_ETARIA"]),
            (key("CapituloCID10", "codigo"), ["POR_CAPITULO_CID"]),
            (key("Sexo"), ["POR_SEXO"]),
            (key("FaixaPeso"), ["POR_FAIXA_PESO"]),
            (key("Vacina"), ["COBERTURA_VACINAL", "DOSES_APLICADAS"]),
        ],
        card_src="N", card_dst="N",
    )


def _cnes(dims):
    return rel_figure(
        Entity("IndicadorSaude", INDICADOR_COMPACTO),
        [(key(n), [r]) for n, r in dims],
        card_src="N", card_dst="N", top=True,
    )


def fig_saude_cnes_a():
    return _cnes([
        ("TipoEstabelecimento", "POR_TIPO_ESTABELECIMENTO"),
        ("OcupacaoProfissional", "POR_OCUPACAO"),
        ("TipoEquipe", "POR_TIPO_EQUIPE"),
    ])


def fig_saude_cnes_b():
    return _cnes([
        ("TipoAtendimento", "POR_TIPO_ATENDIMENTO"),
        ("TipoLeitoConsultorio", "POR_TIPO_LEITO_CONSULTORIO"),
        ("TipoEquipamento", "POR_TIPO_EQUIPAMENTO"),
    ])


def fig_execucao():
    c = Canvas()
    an = Entity("Analise", ANALISE).at(0, 0)
    me = Entity("MetricaExecucao", METRICA)
    me.at(an.w + seg_len(["GEROU_METRICA"]), (an.h - me.h) / 2)
    h_edge(c, an.right, me.x, me.cy, "GEROU_METRICA", "1", "N")
    an.draw(c)
    me.draw(c)
    return c


FIGURES = {
    "01-visao-geral": (fig_visao_geral, MAX_H_GERAL),
    "02-orcamento-empenho-classificacao": (fig_orcamento_empenho_classificacao, MAX_H_DETALHE),
    "03-orcamento-empenho-acao": (fig_orcamento_empenho_acao, MAX_H_DETALHE),
    "04-orcamento-agregados": (fig_orcamento_agregados, MAX_H_DETALHE),
    "05-orcamento-previsto-realizado": (fig_orcamento_previsto_realizado, MAX_H_DETALHE),
    "06-saude-dimensoes": (fig_saude_dimensoes, MAX_H_DETALHE),
    "07a-saude-cnes": (fig_saude_cnes_a, MAX_H_DETALHE),
    "07b-saude-cnes": (fig_saude_cnes_b, MAX_H_DETALHE),
    "08-execucao": (fig_execucao, MAX_H_DETALHE),
}

if __name__ == "__main__":
    for name, (fn, max_h) in FIGURES.items():
        w, h = fn().save(name, max_h)
        print(f"{name}: {w / 72 * 2.54:.1f} x {h / 72 * 2.54:.1f} cm")
