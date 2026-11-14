from flask import request

from ..errors import ApiError
from ..util import parse_date


def body():
    b = request.get_json(silent=True)
    if not isinstance(b, dict):
        raise ApiError('validation', 'JSON object expected.', 422)
    return b


def date_arg(name, default=None):
    return parse_date(request.args.get(name), name, required=False) or default


def int_arg(name, default=None):
    v = request.args.get(name)
    try:
        return int(v) if v not in (None, '') else default
    except ValueError:
        raise ApiError('validation', f'{name} must be a number.', 422)


def paging():
    page = max(1, int_arg('page', 1))
    per = min(200, max(1, int_arg('per_page', 25)))
    return page, per, (page - 1) * per
