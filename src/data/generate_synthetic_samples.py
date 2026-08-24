"""
generate_synthetic_samples.py

Generates 10 synthetic Italian purchase invoices/receipts in each of the
file formats ingestion.py handles that no ready-made real dataset covers:
XML (FatturaPA e-invoice), PDF (native text), PDF (scanned -- image-only,
no text layer, forces OCR), and plain text.

Real photographed receipts come from SROIE/CORD instead (see
download_real_samples.py) -- these synthetic ones exist to exercise the
OTHER file-format code paths in ingestion.py (XMLIngestor, PDFIngestor's
two branches, TextIngestor) with content the pipeline can actually
categorize, since suppliers/line-item descriptions here are deliberately
drawn from chart_of_accounts.json's own keyword lists.

Output: data_set/samples/{xml,pdf,pdf_scanned,text}/synthetic_invoice_N.*

Usage:
    python generate_synthetic_samples.py [--n 10]
"""

import argparse
import json
import os
import random
from datetime import date, timedelta
from xml.sax.saxutils import escape as xml_escape

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_SET_DIR = os.path.join(_REPO_ROOT, "data_set")
SAMPLES_DIR = os.path.join(DATA_SET_DIR, "samples")
COA_PATH = os.path.join(DATA_SET_DIR, "chart_of_accounts.json")

random.seed(42)  # reproducible demo dataset

# ----------------------------------------------------------------------
# Synthetic supplier / line-item pools, drawn from real COA keywords so
# validator.py's keyword categorizer hits a real category for these
# (no model call needed) -- spans several expense categories on purpose.
# ----------------------------------------------------------------------

SUPPLIERS = [
    ("Enel Energia SpA", "IT00834710156", ["energia elettrica"], "B-07-UTIL"),
    ("Studio Legale Bianchi & Associati", "IT01928374650", ["consulenza legale"], "B-07-CONS"),
    ("Trasporti Rossi Srl", "IT02938475610", ["trasporto merci"], "B-07-TRAS"),
    ("Ristorante Da Mario", "IT03847562910", ["pranzo di lavoro"], "B-07-REPR"),
    ("TechStore Informatica Srl", "IT04756293810", ["laptop", "monitor"], "B-II-03"),
    ("Assicurazioni Generali SpA", "IT05938471029", ["polizza assicurazione"], "B-07-ASSI"),
    ("Immobiliare Verdi Srl", "IT06827364910", ["canone di locazione ufficio"], "B-08"),
    ("Agenzia Pubblicita Milano Srl", "IT07736251840", ["campagna pubblicita"], "B-07-MARK"),
    ("Manutenzioni Industriali Srl", "IT08645172930", ["manutenzione impianto"], "B-07-MANU"),
    ("Cartoleria Ufficio Facile Srl", "IT09564083720", ["cancelleria", "carta A4"], "B-14"),
]

CUSTOMERS = [
    ("Rossi Impianti Srl", "01234567890"),
    ("Bianchi Consulting Srl", "09876543210"),
    ("Verdi Logistica Srl", "11223344556"),
]


def _load_coa_keyword(code: str) -> str:
    with open(COA_PATH, "r", encoding="utf-8") as f:
        coa = json.load(f)
    for section in ("conto_economico",):
        for top in coa.get(section, []):
            for cat in top.get("categories", []):
                if cat["code"] == code:
                    return cat["keywords"][0]
    return "servizio"


def _make_invoice_data(i: int) -> dict:
    supplier_name, supplier_vat, keywords, coa_code = SUPPLIERS[i % len(SUPPLIERS)]
    customer_name, customer_cf = CUSTOMERS[i % len(CUSTOMERS)]
    doc_date = date(2026, 7, 1) + timedelta(days=i * 2)
    due_date = doc_date + timedelta(days=30)

    quantity = round(random.uniform(1, 5), 2)
    unit_price = round(random.uniform(50, 500), 2)
    line_total = round(quantity * unit_price, 2)
    vat_rate = 22.0
    vat_amount = round(line_total * vat_rate / 100, 2)
    total_amount = round(line_total + vat_amount, 2)
    description = keywords[0].capitalize()

    return {
        "doc_number": str(1000 + i),
        "doc_date": doc_date.isoformat(),
        "due_date": due_date.isoformat(),
        "supplier_name": supplier_name,
        "supplier_vat": supplier_vat,
        "customer_name": customer_name,
        "customer_cf": customer_cf,
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "line_total": line_total,
        "vat_rate": vat_rate,
        "vat_amount": vat_amount,
        "subtotal": line_total,
        "total_amount": total_amount,
        "coa_code": coa_code,
    }


# ----------------------------------------------------------------------
# XML (FatturaPA e-invoice) -- structure matches data_set/IT01234567890_FPR01.xml
# ----------------------------------------------------------------------

XML_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<p:FatturaElettronica xmlns:ds="http://www.w3.org/2000/09/xmldsig#" xmlns:p="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" versione="FPR12" xsi:schemaLocation="http://ivaservizi.agenziaentrate.gov.it/docs/xsd/fatture/v1.2 http://www.fatturapa.gov.it/export/fatturazione/sdi/fatturapa/v1.2/Schema_del_file_xml_FatturaPA_versione_1.2.xsd">
  <FatturaElettronicaHeader>
    <DatiTrasmissione>
      <IdTrasmittente>
        <IdPaese>IT</IdPaese>
        <IdCodice>{supplier_vat_digits}</IdCodice>
      </IdTrasmittente>
      <ProgressivoInvio>{doc_number}</ProgressivoInvio>
      <FormatoTrasmissione>FPR12</FormatoTrasmissione>
      <CodiceDestinatario>ABC1234</CodiceDestinatario>
      <ContattiTrasmittente/>
    </DatiTrasmissione>
    <CedentePrestatore>
      <DatiAnagrafici>
        <IdFiscaleIVA>
          <IdPaese>IT</IdPaese>
          <IdCodice>{supplier_vat_digits}</IdCodice>
        </IdFiscaleIVA>
        <Anagrafica>
          <Denominazione>{supplier_name}</Denominazione>
        </Anagrafica>
        <RegimeFiscale>RF19</RegimeFiscale>
      </DatiAnagrafici>
      <Sede>
        <Indirizzo>VIA ROMA 1</Indirizzo>
        <CAP>00100</CAP>
        <Comune>ROMA</Comune>
        <Provincia>RM</Provincia>
        <Nazione>IT</Nazione>
      </Sede>
    </CedentePrestatore>
    <CessionarioCommittente>
      <DatiAnagrafici>
        <CodiceFiscale>{customer_cf}</CodiceFiscale>
        <Anagrafica>
          <Denominazione>{customer_name}</Denominazione>
        </Anagrafica>
      </DatiAnagrafici>
      <Sede>
        <Indirizzo>VIA TORINO 38</Indirizzo>
        <CAP>00145</CAP>
        <Comune>ROMA</Comune>
        <Provincia>RM</Provincia>
        <Nazione>IT</Nazione>
      </Sede>
    </CessionarioCommittente>
  </FatturaElettronicaHeader>
  <FatturaElettronicaBody>
    <DatiGenerali>
      <DatiGeneraliDocumento>
        <TipoDocumento>TD01</TipoDocumento>
        <Divisa>EUR</Divisa>
        <Data>{doc_date}</Data>
        <Numero>{doc_number}</Numero>
      </DatiGeneraliDocumento>
    </DatiGenerali>
    <DatiBeniServizi>
      <DettaglioLinee>
        <NumeroLinea>1</NumeroLinea>
        <Descrizione>{description}</Descrizione>
        <Quantita>{quantity:.2f}</Quantita>
        <PrezzoUnitario>{unit_price:.2f}</PrezzoUnitario>
        <PrezzoTotale>{line_total:.2f}</PrezzoTotale>
        <AliquotaIVA>{vat_rate:.2f}</AliquotaIVA>
      </DettaglioLinee>
      <DatiRiepilogo>
        <AliquotaIVA>{vat_rate:.2f}</AliquotaIVA>
        <ImponibileImporto>{subtotal:.2f}</ImponibileImporto>
        <Imposta>{vat_amount:.2f}</Imposta>
        <EsigibilitaIVA>I</EsigibilitaIVA>
      </DatiRiepilogo>
    </DatiBeniServizi>
    <DatiPagamento>
      <CondizioniPagamento>TP01</CondizioniPagamento>
      <DettaglioPagamento>
        <ModalitaPagamento>MP01</ModalitaPagamento>
        <DataScadenzaPagamento>{due_date}</DataScadenzaPagamento>
        <ImportoPagamento>{total_amount:.2f}</ImportoPagamento>
      </DettaglioPagamento>
    </DatiPagamento>
  </FatturaElettronicaBody>
</p:FatturaElettronica>
"""


def generate_xml(data: dict) -> str:
    # Supplier/customer names can contain XML-significant characters
    # (e.g. "Studio Legale Bianchi & Associati") -- escape every
    # free-text field before interpolating into the template, or the
    # XML comes out malformed for exactly the suppliers most likely to
    # have a "&" in a real company name.
    escaped = dict(data)
    for key in ("supplier_name", "customer_name", "description"):
        escaped[key] = xml_escape(str(data[key]))
    return XML_TEMPLATE.format(supplier_vat_digits=data["supplier_vat"].replace("IT", ""), **escaped)


# ----------------------------------------------------------------------
# Plain text invoice
# ----------------------------------------------------------------------

def generate_text(data: dict) -> str:
    return (
        f"{data['supplier_name']}\n"
        f"P.IVA: {data['supplier_vat']}\n"
        f"\n"
        f"FATTURA n. {data['doc_number']}\n"
        f"Data: {data['doc_date']}\n"
        f"Scadenza: {data['due_date']}\n"
        f"\n"
        f"Cliente: {data['customer_name']}\n"
        f"\n"
        f"Descrizione: {data['description']}\n"
        f"Quantita: {data['quantity']}\n"
        f"Prezzo unitario: {data['unit_price']:.2f}\n"
        f"Totale riga: {data['line_total']:.2f}\n"
        f"\n"
        f"Subtotale: {data['subtotal']:.2f}\n"
        f"IVA ({data['vat_rate']:.0f}%): {data['vat_amount']:.2f}\n"
        f"Totale: {data['total_amount']:.2f} EUR\n"
    )


# ----------------------------------------------------------------------
# PDF (native text)
# ----------------------------------------------------------------------

def generate_pdf_native(data: dict, out_path: str) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(out_path, pagesize=A4)
    width, height = A4
    y = height - 2.5 * 72 / 72 * 72  # start ~2.5cm from top, in points-ish

    lines = generate_text(data).splitlines()
    c.setFont("Helvetica", 11)
    y = height - 60
    for line in lines:
        c.drawString(60, y, line)
        y -= 16
    c.save()


# ----------------------------------------------------------------------
# PDF (scanned -- image-only, no extractable text layer, forces OCR)
# ----------------------------------------------------------------------

def generate_pdf_scanned(data: dict, out_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFont

    width, height = 1240, 1754  # ~A4 at 150dpi
    img = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()

    lines = generate_text(data).splitlines()
    y = 80
    for line in lines:
        draw.text((80, y), line, fill="black", font=font)
        y += 44

    # Slight rotation + noise-free render is still "scanned-like" enough
    # to have zero native text layer once written out as PDF -- the
    # actual OCR-forcing property is that it's an image, not vector text.
    img.save(out_path, "PDF", resolution=150.0)


# ----------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------

def generate_all(n: int = 10) -> dict:
    for sub in ("xml", "pdf", "pdf_scanned", "text"):
        os.makedirs(os.path.join(SAMPLES_DIR, sub), exist_ok=True)

    counts = {"xml": 0, "pdf": 0, "pdf_scanned": 0, "text": 0}

    for i in range(n):
        data = _make_invoice_data(i)

        xml_path = os.path.join(SAMPLES_DIR, "xml", f"synthetic_invoice_{i + 1}.xml")
        with open(xml_path, "w", encoding="utf-8") as f:
            f.write(generate_xml(data))
        counts["xml"] += 1

        text_path = os.path.join(SAMPLES_DIR, "text", f"synthetic_invoice_{i + 1}.txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(generate_text(data))
        counts["text"] += 1

        pdf_path = os.path.join(SAMPLES_DIR, "pdf", f"synthetic_invoice_{i + 1}.pdf")
        generate_pdf_native(data, pdf_path)
        counts["pdf"] += 1

        pdf_scanned_path = os.path.join(SAMPLES_DIR, "pdf_scanned", f"synthetic_invoice_{i + 1}.pdf")
        generate_pdf_scanned(data, pdf_scanned_path)
        counts["pdf_scanned"] += 1

    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate synthetic demo invoices in every ingestion.py format.")
    parser.add_argument("--n", type=int, default=10, help="Samples per format (default: 10)")
    args = parser.parse_args()

    counts = generate_all(args.n)
    print(f"Generated into {SAMPLES_DIR}:")
    for fmt, count in counts.items():
        print(f"  {fmt}: {count} files")
