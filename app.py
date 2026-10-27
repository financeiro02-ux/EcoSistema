"""Relatório de Contas a Pagar — versão Streamlit.

Fluxo: escolher empresa -> anexar CSV do financeiro -> ver prévia -> baixar PDF.
"""
import base64
import csv
import io
import json
import re
import time
import unicodedata
from datetime import date
from pathlib import Path

import streamlit as st
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (BaseDocTemplate, Frame, PageTemplate,
                                Paragraph, Spacer, Table, TableStyle, Flowable)
from reportlab.lib.styles import ParagraphStyle

BASE = Path(__file__).parent
DATA_FILE = BASE / "empresas.json"          # empresas cadastradas pela interface
LOTUS_LOGO = BASE / "assets" / "lotus.png"
BUILTIN = [{"id": "lotus", "name": "Lotus", "color": "#C46A18", "builtin": True}]

st.set_page_config(page_title="Relatório de Contas a Pagar", page_icon="🧾", layout="centered")


# ---------------------------------------------------------------- empresas
def load_custom():
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_custom(items):
    DATA_FILE.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def all_companies():
    custom = load_custom()
    out = []
    for b in BUILTIN:
        o = next((c for c in custom if c["id"] == b["id"]), None)
        c = {**b, **(o or {})}
        c["builtin"] = True
        if not c.get("logo_b64") and LOTUS_LOGO.exists():
            c["logo_b64"] = base64.b64encode(LOTUS_LOGO.read_bytes()).decode()
        out.append(c)
    out += [c for c in custom if not any(b["id"] == c["id"] for b in BUILTIN)]
    return out


def slug(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


# ---------------------------------------------------------------- cores
def hex_to_rgb(h):
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    n = int(h, 16)
    return (n >> 16) & 255, (n >> 8) & 255, n & 255


def lum(rgb):
    def f(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ink_for(hex_):
    return "#1A1A1A" if lum(hex_to_rgb(hex_)) > 0.45 else "#FFFFFF"


def soft(hex_, a=0.15):
    return tuple(round(255 - (255 - c) * a) for c in hex_to_rgb(hex_))


def rgb_hex(t):
    return "#%02x%02x%02x" % t


# ---------------------------------------------------------------- CSV
def norm(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(ch for ch in s if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z]", "", s)


def money(s):
    s = re.sub(r"[R$\s]", "", str(s).strip())
    if not s:
        return 0.0
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def brl(v):
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_cat(s):
    parts = [p for p in re.split(r"\s*;\s*", s.strip()) if p]

    def pct(m):
        v = float(f"{m.group(1)}.{m.group(2)}")
        return "(" + f"{v:.2f}".rstrip("0").rstrip(".").replace(".", ",") + "%)"

    return "\n".join(re.sub(r"\((\d+)[,.](\d+)\s*%\)", pct, p) for p in parts)


def fmt_date(s):
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    return f"{m.group(3)}/{m.group(2)}/{m.group(1)}" if m else s


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_csv(text):
    first = text.splitlines()[0] if text.strip() else ""
    delim = ";" if first.count(";") >= first.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    return [r for r in rows if any(c.strip() for c in r)]


def load_rows(raw: bytes):
    """Retorna (rows, supplier_label) ou levanta ValueError com mensagem amigável."""
    data = parse_csv(decode(raw))
    if len(data) < 2:
        raise ValueError("O arquivo está vazio ou não tem lançamentos.")
    h = [norm(x) for x in data[0]]

    def find(*keys):
        return next((i for i, x in enumerate(h) if any(k in x for k in keys)), -1)

    label = "Razão Social"
    i_rs = find("fornecedorrazaosocial", "razaosocial")
    if i_rs < 0:
        i_rs = find("fornecedornomefantasia", "nomefantasia")
        if i_rs >= 0:
            label = "Fornecedor"
    if i_rs < 0:
        i_rs = next((i for i, x in enumerate(h) if x.startswith("fornecedor")
                     and "cnpj" not in x and "cpf" not in x), -1)
        if i_rs >= 0:
            label = "Fornecedor"
    i_cat, i_proj = find("categoria"), find("projeto")
    i_val = find("valorliquido", "valor")
    i_dat = find("previsaodepagamento", "vencimento", "data")

    missing = []
    if i_rs < 0:
        missing.append("Fornecedor (Razão Social ou Nome Fantasia)")
    if i_cat < 0:
        missing.append("Categoria")
    if i_val < 0:
        missing.append("Valor Líquido")
    if missing:
        raise ValueError("Não encontrei a(s) coluna(s): " + ", ".join(missing)
                         + ". Confira se é a exportação de contas a pagar.")

    def cell(r, i):
        return r[i].strip() if 0 <= i < len(r) else ""

    rows = []
    for r in data[1:]:
        item = {
            "rs": cell(r, i_rs),
            "cat": fmt_cat(cell(r, i_cat)),
            "proj": (cell(r, i_proj) or "Sem projeto") if i_proj >= 0 else "Sem projeto",
            "val": money(cell(r, i_val)),
            "data": cell(r, i_dat)[:10] if i_dat >= 0 else "",
        }
        if item["rs"] or item["val"]:
            rows.append(item)
    rows.sort(key=lambda x: (x["rs"].casefold(), x["cat"].casefold()))
    return rows, label


def periodo(rows):
    ds = sorted(r["data"] for r in rows if r["data"])
    if not ds:
        return ""
    return fmt_date(ds[0]) if ds[0] == ds[-1] else f"{fmt_date(ds[0])} a {fmt_date(ds[-1])}"


def by_project(rows):
    m = {}
    for r in rows:
        x = m.setdefault(r["proj"], {"q": 0, "v": 0.0})
        x["q"] += 1
        x["v"] += r["val"]
    return sorted(m.items(), key=lambda kv: -kv[1]["v"])


# ---------------------------------------------------------------- PDF
class Band(Flowable):
    """Faixa colorida do topo com logo/nome e título."""

    def __init__(self, company, per, width):
        super().__init__()
        self.c, self.per, self.width, self.height = company, per, width, 30 * mm

    def draw(self):
        c = self.canv
        brand = colors.Color(*[v / 255 for v in hex_to_rgb(self.c["color"])])
        ink = colors.HexColor(ink_for(self.c["color"]))
        c.setFillColor(brand)
        c.rect(0, 0, self.width, self.height, stroke=0, fill=1)
        c.setFillColor(ink)
        logo = self.c.get("logo_b64")
        if logo:
            img = ImageReader(io.BytesIO(base64.b64decode(logo)))
            iw, ih = img.getSize()
            h = 20 * mm
            w = h * iw / ih
            if w > 55 * mm:
                w = 55 * mm
                h = w * ih / iw
            c.drawImage(img, 7 * mm, (self.height - h) / 2, w, h, mask="auto")
        else:
            c.setFont("Helvetica-Bold", 18)
            c.drawString(7 * mm, self.height - 18 * mm, self.c["name"])
        right = self.width - 7 * mm
        c.setFont("Helvetica-Bold", 16)
        c.drawRightString(right, self.height - 11 * mm, "Contas a Pagar")
        c.setFont("Helvetica", 9)
        c.drawRightString(right, self.height - 17 * mm, "Pagamento semanal")
        if self.per:
            c.drawRightString(right, self.height - 22 * mm, "Previsão de pagamento: " + self.per)


def build_pdf(company, rows, label) -> bytes:
    buf = io.BytesIO()
    W, H = A4
    M = 12 * mm
    brand = colors.HexColor(company["color"])
    ink = colors.HexColor(ink_for(company["color"]))
    lite = colors.Color(*[v / 255 for v in soft(company["color"])])
    total = sum(r["val"] for r in rows)
    per = periodo(rows)
    today = date.today().strftime("%d/%m/%Y")
    avail = W - 2 * M

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.Color(.51, .51, .51))
        canvas.drawString(M, 8 * mm,
                          f"{company['name']} | Relatório de Contas a Pagar | gerado em {today}")
        canvas.drawRightString(W - M, 8 * mm, f"Página {doc.page}")
        canvas.restoreState()

    doc = BaseDocTemplate(buf, pagesize=A4, leftMargin=M, rightMargin=M, topMargin=M,
                          bottomMargin=16 * mm, title="Relatório de Contas a Pagar")
    doc.addPageTemplates([PageTemplate(id="p", frames=[Frame(M, 16 * mm, avail, H - M - 16 * mm,
                                                             leftPadding=0, rightPadding=0,
                                                             topPadding=0, bottomPadding=0)],
                                       onPage=footer)])

    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#1A1A1A"))
    cell_c = ParagraphStyle("cell_c", parent=cell, alignment=1)
    cell_b = ParagraphStyle("cell_b", parent=cell_c, fontName="Helvetica-Bold")
    cell_g = ParagraphStyle("cell_g", parent=cell_c, textColor=colors.Color(.59, .59, .59))
    cell_r = ParagraphStyle("cell_r", parent=cell, alignment=2)
    th = ParagraphStyle("th", parent=cell, fontName="Helvetica-Bold", textColor=ink, fontSize=8.5)
    th_c = ParagraphStyle("th_c", parent=th, alignment=1)
    th_r = ParagraphStyle("th_r", parent=th, alignment=2)

    def esc(s):
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    head = [Paragraph(label, th), Paragraph("Categoria", th_c), Paragraph("Projeto", th_c),
            Paragraph("Valor Líquido", th_r)]
    body = [head]
    for r in rows:
        sem = r["proj"] == "Sem projeto"
        body.append([Paragraph(esc(r["rs"]), cell),
                     Paragraph(esc(r["cat"]).replace("\n", "<br/>"), cell_c),
                     Paragraph(esc(r["proj"]), cell_g if sem else cell_b),
                     Paragraph(brl(r["val"]), cell_r)])
    body.append(["", "", Paragraph("Total", ParagraphStyle("t", parent=th_c)),
                 Paragraph(brl(total), th_r)])
    n = len(body)
    widths = [72 * mm, 38 * mm, 44 * mm, avail - 154 * mm]
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), brand),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 1), (-1, n - 3), 0.4, colors.Color(.78, .78, .78)),
        ("BACKGROUND", (2, n - 1), (3, n - 1), brand),
    ]
    for i, r in enumerate(rows, start=1):
        if r["proj"] != "Sem projeto":
            style.append(("BACKGROUND", (2, i), (2, i), lite))
    t1 = Table(body, colWidths=widths, repeatRows=1)
    t1.setStyle(TableStyle(style))

    resumo = [[Paragraph("Projeto", th), Paragraph("Lançamentos", th_c),
               Paragraph("Valor Total", th_r), Paragraph("% do Total", th_r)]]
    for p, x in by_project(rows):
        pct = (x["v"] / total * 100) if total else 0
        resumo.append([Paragraph(f"<b>{esc(p)}</b>", cell), Paragraph(str(x["q"]), cell_c),
                       Paragraph(brl(x["v"]), cell_r),
                       Paragraph(f"{pct:.1f}".replace(".", ",") + "%", cell_r)])
    t2 = Table(resumo, colWidths=[86 * mm, 30 * mm, 35 * mm, avail - 151 * mm], repeatRows=1)
    t2.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), brand), ("BACKGROUND", (0, 1), (-1, -1), lite),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    h3 = ParagraphStyle("h3", fontName="Helvetica-Bold", fontSize=11, leading=14)

    doc.build([Band(company, per, avail), Spacer(1, 8 * mm), t1, Spacer(1, 12 * mm),
               Paragraph("Resumo por Projeto", h3), Spacer(1, 3 * mm), t2])
    return buf.getvalue()


# ---------------------------------------------------------------- prévia HTML
def preview_html(company, rows, label):
    brand = company["color"]
    ink = ink_for(brand)
    soft_hex = rgb_hex(soft(brand))
    total = sum(r["val"] for r in rows)
    per = periodo(rows)
    logo = (f'<img src="data:image/png;base64,{company["logo_b64"]}" style="max-height:58px;max-width:160px">'
            if company.get("logo_b64") else f'<b style="font-size:26px">{company["name"]}</b>')
    esc = lambda s: s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    trs = "".join(
        f'<tr><td>{esc(r["rs"])}</td><td class="c">{esc(r["cat"]).replace(chr(10), "<br>")}</td>'
        f'<td class="c proj{" none" if r["proj"] == "Sem projeto" else ""}">{esc(r["proj"])}</td>'
        f'<td class="r">{brl(r["val"])}</td></tr>' for r in rows)
    resumo = ""
    for p, x in by_project(rows):
        pct = f"{(x['v'] / total * 100 if total else 0):.1f}".replace(".", ",")
        resumo += (f'<tr><td class="proj" style="text-align:left">{esc(p)}</td><td class="c">{x["q"]}</td>'
                   f'<td class="r">{brl(x["v"])}</td><td class="r">{pct}%</td></tr>')
    return f"""
<style>
.paper{{background:#fff;color:#1a1a1a;border-radius:6px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,.15);font-family:sans-serif}}
.band{{background:{brand};color:{ink};display:flex;justify-content:space-between;align-items:center;padding:16px 20px;border-radius:2px;gap:12px}}
.band .meta{{text-align:right;line-height:1.3}} .band .meta b{{font-size:20px;display:block}} .band .meta span{{font-size:12px;display:block}}
.paper table{{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}}
.paper th{{background:{brand};color:{ink};text-align:left;padding:7px 10px}}
.paper td{{padding:6px 10px;border-bottom:1px solid #e4e2dd}}
.c{{text-align:center}} .r{{text-align:right;white-space:nowrap}} th.c{{text-align:center}} th.r{{text-align:right}}
td.proj{{background:{soft_hex};font-weight:600;text-align:center}} td.proj.none{{background:transparent;font-weight:400;color:#959595}}
tr.total td{{border:0}} tr.total td.tl{{background:{brand};color:{ink};font-weight:700}}
.paper h3{{margin:26px 0 8px;font-size:16px}} .scroll{{overflow-x:auto}}
</style>
<div class="paper">
  <div class="band">{logo}<div class="meta"><b>Contas a Pagar</b><span>Pagamento semanal</span>
  {f'<span>Previsão de pagamento: {per}</span>' if per else ''}</div></div>
  <div class="scroll"><table><thead><tr><th>{label}</th><th class="c">Categoria</th><th class="c">Projeto</th><th class="r">Valor Líquido</th></tr></thead>
  <tbody>{trs}<tr class="total"><td></td><td></td><td class="tl">Total</td><td class="r tl">{brl(total)}</td></tr></tbody></table></div>
  <h3>Resumo por projeto</h3>
  <div class="scroll"><table><thead><tr><th>Projeto</th><th class="c">Lançamentos</th><th class="r">Valor total</th><th class="r">% do total</th></tr></thead>
  <tbody>{resumo}</tbody></table></div>
</div>"""


# ---------------------------------------------------------------- UI
st.title("Relatório de contas a pagar")
st.caption("Anexe seu CSV, escolha a empresa e baixe o relatório em PDF com a cor e a logo dela.")

companies = all_companies()
ids = [c["id"] for c in companies]
if st.session_state.get("empresa") not in ids:
    st.session_state["empresa"] = "lotus"

st.subheader("1. Escolha a empresa")
sel = st.radio("Empresa", ids, key="empresa", horizontal=True,
               format_func=lambda i: next(c["name"] for c in companies if c["id"] == i),
               label_visibility="collapsed")
company = next(c for c in companies if c["id"] == sel)

with st.expander("Cadastrar ou editar empresas"):
    mode = st.radio("O que deseja fazer?", ["Nova empresa", f"Editar {company['name']}"], horizontal=True)
    editing = mode.startswith("Editar")
    name = st.text_input("Nome da empresa", value=company["name"] if editing else "", max_chars=40)
    color = st.color_picker("Cor principal", value=company["color"] if editing else "#2F5D8A")
    logo_file = st.file_uploader("Logo (PNG, de preferência branca e com fundo transparente)",
                                 type=["png", "jpg", "jpeg", "webp"], key="logo_up")
    c1, c2 = st.columns(2)
    if c1.button("Salvar empresa", type="primary"):
        if not name.strip():
            st.error("Dê um nome para a empresa.")
        else:
            logo_b64 = company.get("logo_b64", "") if editing else ""
            if logo_file is not None:
                from PIL import Image
                img = Image.open(logo_file).convert("RGBA")
                img.thumbnail((600, 600))
                b = io.BytesIO()
                img.save(b, "PNG")
                logo_b64 = base64.b64encode(b.getvalue()).decode()
            cid = company["id"] if editing else f"{slug(name)}-{int(time.time())}"
            custom = [c for c in load_custom() if c["id"] != cid]
            custom.append({"id": cid, "name": name.strip(), "color": color, "logo_b64": logo_b64})
            save_custom(custom)
            st.session_state["empresa"] = cid
            st.rerun()
    if not company.get("builtin") and c2.button(f"Remover {company['name']}"):
        save_custom([c for c in load_custom() if c["id"] != company["id"]])
        st.session_state["empresa"] = "lotus"
        st.rerun()

st.subheader("2. Anexe o CSV do financeiro")
up = st.file_uploader("Exportação de contas a pagar em .csv (separado por ponto e vírgula)", type=["csv"])

st.subheader("Prévia")
if up is None:
    st.info("Escolha uma empresa e anexe o CSV para ver o relatório.")
else:
    try:
        rows, label = load_rows(up.getvalue())
    except ValueError as e:
        st.error(str(e))
        st.stop()
    st.success(f"{len(rows)} lançamentos lidos.")
    st.html(preview_html(company, rows, label))

    ds = sorted(r["data"] for r in rows if r["data"])
    tag = (fmt_date(ds[-1]) if ds else date.today().strftime("%d/%m/%Y")).replace("/", "-")
    fname = f"Contas_a_Pagar_{re.sub(r'[^A-Za-z0-9]+', '_', company['name']).strip('_')}_{tag}.pdf"
    st.download_button("Baixar PDF", data=build_pdf(company, rows, label), file_name=fname,
                       mime="application/pdf", type="primary")
