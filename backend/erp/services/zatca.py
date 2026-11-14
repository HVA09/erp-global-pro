"""ZATCA (Saudi e-invoicing phase 1) TLV payload + QR rendering (SVG)."""
import base64


def _tlv(tag, value):
    raw = str(value if value is not None else '').encode('utf-8')
    return bytes([tag, len(raw)]) + raw


def payload(seller, vat_number, timestamp, total, vat):
    parts = [_tlv(1, seller), _tlv(2, vat_number or '000000000000000'), _tlv(3, timestamp),
             _tlv(4, f'{total:.2f}'), _tlv(5, f'{vat:.2f}')]
    return base64.b64encode(b''.join(parts)).decode()


def qr_svg(text, size=180):
    from reportlab.graphics import renderSVG
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    widget = qr.QrCodeWidget(text)
    x0, y0, x1, y1 = widget.getBounds()
    w, h = x1 - x0, y1 - y0
    drawing = Drawing(size, size, transform=[size / w, 0, 0, size / h, 0, 0])
    drawing.add(widget)
    return renderSVG.drawToString(drawing)
