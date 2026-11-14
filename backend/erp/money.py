"""Money helpers. All amounts are stored as integer minor units (cents) to avoid float errors."""
from decimal import Decimal, ROUND_HALF_UP

D = Decimal


def to_cents(value):
    """Convert a user supplied amount (number or string) to integer cents."""
    if value is None or value == '':
        return 0
    return int((D(str(value)) * 100).quantize(D('1'), rounding=ROUND_HALF_UP))


def from_cents(cents):
    return round((cents or 0) / 100, 2)


def round_half_up(x):
    """Round a Decimal/float/int to an integer using commercial rounding."""
    return int(D(str(x)).quantize(D('1'), rounding=ROUND_HALF_UP))


def line_amounts(qty, unit_price_cents, discount_pct, tax_rate):
    """Return (net_cents, tax_cents, total_cents) for an invoice line."""
    gross = D(str(qty)) * D(int(unit_price_cents))
    net = round_half_up(gross * (D(100) - D(str(discount_pct or 0))) / D(100))
    tax = round_half_up(D(net) * D(str(tax_rate or 0)) / D(100))
    return net, tax, net + tax
