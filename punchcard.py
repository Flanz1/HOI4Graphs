"""
"Punch card" ledger tables: a tabulating-report styled table of per-nation values at a
handful of checkpoint dates, grouped into blocs, with combined rows, bloc totals and growth.

Drawn with matplotlib only (no browser needed). Layout is done in pixels on a 100-dpi
canvas and saved at 2x for crisp text.
"""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import Polygon, Rectangle, FancyBboxPatch

# ---- palette (sampled from the reference cards) --------------------------------------
BG = '#ded5b4'
CARD = '#e9e3cc'
CARD_EDGE = '#d4cba5'
BAND = '#d8cfa8'          # header + combined rows
GREEN = '#c9d9be'         # green-bar rows
GREEN_RULE = '#a2b992'
INK = '#1c1b15'
GREY = '#7a7560'
RULE = '#3f3e34'
HOLE = '#ded5b4'
HOLE_EDGE = '#cfc59c'
DASH = '#8f8970'

DEFAULT_NAMES = {
    'GER': 'Germany', 'ITA': 'Italy', 'JAP': 'Japan', 'HUN': 'Hungary', 'ROM': 'Romania',
    'SPR': 'Spain', 'BUL': 'Bulgaria', 'FIN': 'Finland', 'MAN': 'Manchukuo', 'SIA': 'Siam',
    'ENG': 'United Kingdom', 'USA': 'United States', 'FRA': 'France', 'CAN': 'Canada',
    'RAJ': 'British Raj', 'SAF': 'South Africa', 'AST': 'Australia', 'SOV': 'Soviet Union',
    'UKR': 'Ukraine', 'MON': 'Mongolia', 'CHI': 'China', 'PRC': 'Communist China',
    'POL': 'Poland', 'CZE': 'Czechoslovakia', 'YUG': 'Yugoslavia', 'GRE': 'Greece',
    'TUR': 'Turkey', 'NZL': 'New Zealand', 'HOL': 'Netherlands', 'BEL': 'Belgium',
    'NOR': 'Norway', 'SWE': 'Sweden', 'POR': 'Portugal', 'IRQ': 'Iraq', 'PER': 'Persia',
    'MEX': 'Mexico', 'BRA': 'Brazil', 'ARG': 'Argentina', 'SLO': 'Slovakia', 'CRO': 'Croatia',
}

FOOTER = ('Every field reproduced at exact 2-decimal source precision.  '
          'Alternating row bands follow standard green-bar accounting paper convention.')


def _mono_family():
    have = {f.name for f in font_manager.fontManager.ttflist}
    for name in ('Consolas', 'Cascadia Mono', 'DejaVu Sans Mono', 'Liberation Mono', 'Courier New'):
        if name in have:
            return name
    return 'monospace'


class _Canvas:
    """Pixel-coordinate drawing helper (y grows downwards)."""

    def __init__(self, w, h, family):
        self.fig = plt.figure(figsize=(w / 100, h / 100), dpi=100)
        self.fig.patch.set_facecolor(BG)
        self.ax = self.fig.add_axes([0, 0, 1, 1])
        self.ax.set_xlim(0, w)
        self.ax.set_ylim(h, 0)
        self.ax.axis('off')
        self.family = family

    def rect(self, x, y, w, h, fc, ec='none', lw=0, ls='-', z=1):
        self.ax.add_patch(Rectangle((x, y), w, h, facecolor=fc, edgecolor=ec, linewidth=lw,
                                    linestyle=ls, zorder=z))

    def line(self, x0, x1, y, color, lw):
        self.ax.plot([x0, x1], [y, y], color=color, linewidth=lw, solid_capstyle='butt', zorder=3)

    def text(self, x, y, s, px, color=INK, weight='normal', ha='left', spacing=0.0):
        size = px * 72 / 100  # px -> pt on the 100 dpi canvas
        if not spacing:
            self.ax.text(x, y, s, fontsize=size, color=color, fontweight=weight, ha=ha,
                         va='center', family=self.family, zorder=4)
            return
        # letter-spaced: place characters one by one (monospace advance ~0.6 em)
        step = px * 0.6 + spacing
        if ha == 'right':
            x -= step * len(s)
        for i, ch in enumerate(s):
            self.ax.text(x + i * step, y, ch, fontsize=size, color=color, fontweight=weight,
                         ha='left', va='center', family=self.family, zorder=4)

    def save(self, path):
        self.fig.savefig(path, dpi=200, facecolor=BG)
        plt.close(self.fig)


def _fmt(v):
    return f'{v:,.2f}'


def _growth(first, last):
    if first > 0:
        return f'{last / first:.2f}×'
    return '—'


def render(path, title, checkpoint_labels, values, blocs, names=None, footer=FOOTER,
           kicker='TABULATING REPORT · BATCH RUN'):
    """
    values: {tag: [value at each checkpoint]}
    blocs:  [{'name': 'Axis Powers', 'faction': 'Axis', 'members': [...]}, ...]
    """
    names = {**DEFAULT_NAMES, **(names or {})}
    family = _mono_family()
    k = len(checkpoint_labels)
    # fields are "punched" at 2 decimals; combined rows add up the punched values
    values = {t: [round(v, 2) for v in vals] for t, vals in values.items()}
    # Axis blocs first, then Allies, then anything else (config order within each)
    rank = {'Axis': 0, 'Allies': 1}
    blocs = sorted(blocs, key=lambda b: rank.get(b.get('faction'), 2))

    # rows per bloc, biggest final value first; combined rows
    sections = []
    for b in blocs:
        members = [t for t in b['members'] if t in values]
        if not members:
            continue
        members.sort(key=lambda t: values[t][-1], reverse=True)
        combined = [round(sum(values[t][i] for t in members), 2) for i in range(k)]
        sections.append((b, members, combined))
    if not sections:
        return

    # ---- column widths (monospace) -------------------------------------------------
    PX = 11            # table text size
    CW = PX * 0.6      # char advance
    nation_texts = [f'{names.get(t, t)} ({t})' for _, m, _ in sections for t in m]
    nation_texts += [f"{b['name']} — Combined" for b, _, _ in sections]
    w_nation = max(len(s) for s in nation_texts + ['Nation']) * CW + 24
    num_cols = []
    for i in range(k):
        texts = [_fmt(values[t][i]) for _, m, _ in sections for t in m]
        texts += [_fmt(c[i]) for _, _, c in sections] + [checkpoint_labels[i]]
        num_cols.append(max(len(s) for s in texts) * CW + 13)
    growth_texts = [_growth(values[t][0], values[t][-1]) for _, m, _ in sections for t in m]
    w_growth = max(len(s) for s in growth_texts + ['Growth ×']) * CW + 22
    table_w = w_nation + sum(num_cols) + w_growth

    # ---- vertical layout ------------------------------------------------------------
    ROW = 21
    SECTION = 30
    X0 = 68                                  # table left
    CARD_X = 24
    card_w = (X0 - CARD_X) + table_w + 26
    W = CARD_X + card_w + 24
    y_card = 106
    y_sum = 127
    y_head = 198
    y = y_head + 24
    layout = []
    for sec in sections:
        layout.append((y, sec))
        y += SECTION + ROW * (len(sec[1]) + 1)
    y_footer = y + 20
    card_bottom = y_footer + 37
    H = card_bottom + 32

    cv = _Canvas(W, H, family)

    # ---- header ---------------------------------------------------------------------
    first_last = f'{checkpoint_labels[0]} – {checkpoint_labels[-1]}'
    n_nations = sum(len(m) for _, m, _ in sections)
    cv.text(24, 34, kicker, 8, GREY, spacing=2.6)
    cv.text(24, 59, title, 26, INK, weight='bold')
    cv.text(24, 86, f'{n_nations} nations · {k} checkpoints · {first_last} · '
                    'card-fed ledger, every field punched in full.', 10.5, '#6b6650')

    # ---- card with folded corner + punch holes ---------------------------------------------
    fold = 28
    cx1 = CARD_X + card_w
    cv.ax.add_patch(Polygon([(CARD_X, y_card), (cx1 - fold, y_card), (cx1, y_card + fold),
                             (cx1, card_bottom), (CARD_X, card_bottom)], closed=True,
                            facecolor=CARD, edgecolor=CARD_EDGE, linewidth=1, zorder=0))
    hy = y_sum
    while hy < card_bottom - 14:
        cv.ax.add_patch(FancyBboxPatch((38, hy), 7, 10, boxstyle='round,pad=0,rounding_size=1.5',
                                       facecolor=HOLE, edgecolor=HOLE_EDGE, linewidth=0.8, zorder=1))
        hy += 58

    # ---- summary boxes -----------------------------------------------------------------
    cards = []
    for faction in ('Axis', 'Allies'):
        fs = [s for s in sections if s[0].get('faction') == faction]
        for b, _, c in fs:
            cards.append((b['name'], c[-1]))
        if fs:
            label = 'Combined Axis' if faction == 'Axis' else 'Combined Allies'
            cards.append((label, round(sum(c[-1] for _, _, c in fs), 2)))
    for b, _, c in sections:  # blocs with no faction
        if b.get('faction') not in ('Axis', 'Allies'):
            cards.append((b['name'], c[-1]))
    if cards:
        gap = 11
        bw = (table_w - gap * (len(cards) - 1)) / len(cards)
        for i, (label, v) in enumerate(cards):
            bx = X0 + i * (bw + gap)
            cv.rect(bx, y_sum, bw, 51, 'none', DASH, 1.0, (0, (3, 2)), z=2)
            cv.text(bx + 11, y_sum + 15, f'{label.upper()} · {checkpoint_labels[-1].upper()}',
                    7.5, GREY, spacing=0.6)
            cv.text(bx + 11, y_sum + 34, _fmt(v), 15, INK, weight='bold')

    # ---- table header -----------------------------------------------------------------------
    cv.rect(X0, y_head, table_w, 23, BAND)
    cv.line(X0, X0 + table_w, y_head + 23.5, INK, 2.2)
    cv.line(X0, X0 + table_w, y_head + 25.5, '#8d8a7a', 0.6)
    yc = y_head + 11.5
    cv.text(X0 + 9, yc, 'Nation', 9, INK, weight='bold')
    xr = X0 + w_nation
    col_right = []
    for i, cw in enumerate(num_cols):
        xr += cw
        col_right.append(xr - 9)
        cv.text(xr - 9, yc, checkpoint_labels[i], 9, INK, weight='bold', ha='right')
    growth_right = xr + w_growth - 9
    cv.text(growth_right, yc, 'Growth ×', 9, INK, weight='bold', ha='right')

    # ---- sections ----------------------------------------------------------------------------
    for y0, (b, members, combined) in layout:
        cv.text(X0 + 9, y0 + 18, f"BATCH — {b['name'].upper()}", 8.5, '#5e5a49', spacing=2.4)
        cv.line(X0, X0 + table_w, y0 + SECTION, RULE, 1.0)
        ry = y0 + SECTION
        for j, t in enumerate(members):
            if j % 2 == 1:
                cv.rect(X0, ry, table_w, ROW, GREEN)
                cv.line(X0, X0 + table_w, ry + ROW, GREEN_RULE, 0.8)
            mid = ry + ROW / 2
            cv.text(X0 + 9, mid, f'{names.get(t, t)} ({t})', PX, INK)
            for i in range(k):
                cv.text(col_right[i], mid, _fmt(values[t][i]), PX, INK, ha='right')
            g = _growth(values[t][0], values[t][-1])
            cv.text(growth_right, mid, g, PX, INK, weight='bold' if g != '—' else 'normal',
                    ha='right')
            ry += ROW
        # combined row
        cv.rect(X0, ry, table_w, ROW, BAND)
        cv.line(X0, X0 + table_w, ry + ROW, RULE, 1.2)
        mid = ry + ROW / 2
        cv.text(X0 + 9, mid, f"{b['name']} — Combined", PX, INK, weight='bold')
        for i in range(k):
            cv.text(col_right[i], mid, _fmt(combined[i]), PX, INK, weight='bold', ha='right')
        cv.text(growth_right, mid, _growth(combined[0], combined[-1]), PX, INK, weight='bold',
                ha='right')

    cv.text(X0, y_footer, footer, 8.5, GREY)
    cv.save(path)
