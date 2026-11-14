"""Metadata-driven CRUD engine: one Resource definition gives list/get/create/update/delete endpoints with
validation, search, sorting, pagination, permissions and audit logging."""
from datetime import date as _date

from flask import Blueprint, g, jsonify, request

from . import audit, db
from .errors import ApiError
from .money import from_cents, to_cents
from .security import require

BOOL_KEYS = {'done'}


def serialize(obj):
    """cents -> decimals (balance_cents -> balance), is_* -> bool, hide secrets; recursive."""
    if isinstance(obj, list):
        return [serialize(x) for x in obj]
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == 'password_hash':
                continue
            if k.endswith('_cents') and (v is None or isinstance(v, (int, float))):
                out[k[:-6]] = from_cents(v)
            elif (k.startswith('is_') or k in BOOL_KEYS) and isinstance(v, int) and not isinstance(v, bool):
                out[k] = bool(v)
            else:
                out[k] = serialize(v) if isinstance(v, (dict, list)) else v
        return out
    return obj


class FieldError(Exception):
    def __init__(self, code):
        self.code = code


class F:
    def __init__(self, name, type='str', required=False, default=None, choices=None, maxlen=500,
                 min=None, max=None, ref=None):
        self.name, self.type, self.required, self.default = name, type, required, default
        self.choices, self.maxlen, self.min, self.max, self.ref = choices, maxlen, min, max, ref
        self.column = name + '_cents' if type == 'money' else name

    def parse(self, value, company_id=None):
        if value is None or (isinstance(value, str) and value.strip() == ''):
            if self.required:
                raise FieldError('required')
            return self.parse(self.default, company_id=company_id) if self.default is not None else None
        t = self.type
        if t in ('str', 'text'):
            s = str(value).strip()
            if len(s) > self.maxlen:
                raise FieldError('too_long')
            return s
        if t in ('int', 'ref'):
            try:
                n = int(value)
            except (TypeError, ValueError):
                raise FieldError('invalid_number')
            if self.min is not None and n < self.min:
                raise FieldError('min')
            if self.max is not None and n > self.max:
                raise FieldError('max')
            if t == 'ref':
                if company_id is None:
                    raise FieldError('active_company_required')
                if not db.one(
                    f'SELECT 1 AS x FROM {self.ref} WHERE id=? AND company_id=?',
                    (n, company_id),
                ):
                    raise FieldError('invalid_ref')
            return n
        if t == 'float':
            try:
                n = float(value)
            except (TypeError, ValueError):
                raise FieldError('invalid_number')
            if self.min is not None and n < self.min:
                raise FieldError('min')
            if self.max is not None and n > self.max:
                raise FieldError('max')
            return n
        if t == 'money':
            try:
                cents = to_cents(value)
            except (ValueError, ArithmeticError):
                raise FieldError('invalid_number')
            if cents < (self.min if self.min is not None else 0):
                raise FieldError('min')
            return cents
        if t == 'bool':
            if value in (True, 1, '1', 'true', 'True', 'on'):
                return 1
            if value in (False, 0, '0', 'false', 'False', 'off'):
                return 0
            raise FieldError('invalid_value')
        if t == 'date':
            try:
                return _date.fromisoformat(str(value)[:10]).isoformat()
            except ValueError:
                raise FieldError('invalid_date')
        if t == 'choice':
            if value not in self.choices:
                raise FieldError('invalid_choice')
            return value
        raise FieldError('invalid_value')


class Resource:
    def __init__(self, name, table, fields, perm, select=None, search=(), order=None, where=None,
                 base_values=None, filters=(), flags=None, sort_extra=None, date_field=None,
                 before_save=None, after_save=None, before_delete=None, alias='t'):
        self.name, self.table, self.fields, self.perm = name, table, fields, perm
        self.alias = alias
        self.select = select or f'SELECT {alias}.* FROM {table} {alias}'
        self.search, self.where = search, where
        self.order = order or f'{alias}.id DESC'
        self.base_values = base_values or {}
        self.filters, self.flags = filters, flags or {}
        self.date_field = date_field
        self.before_save, self.after_save, self.before_delete = before_save, after_save, before_delete
        self.sortable = {'id': f'{alias}.id', **{f.name: f'{alias}.{f.column}' for f in fields},
                         'created_at': f'{alias}.created_at', **(sort_extra or {})}

    # ------------------------------------------------------------------ helpers
    def _active_company_id(self):
        """Return the company from the verified server-side membership context."""
        company = g.get('company')
        company_id = company.get('id') if isinstance(company, dict) else None
        if isinstance(company_id, bool):
            company_id = None
        try:
            company_id = int(company_id)
        except (TypeError, ValueError):
            company_id = None
        if company_id is None or company_id <= 0:
            raise ApiError(
                'active_company_required',
                'Select an active company before accessing company-owned records.',
                409,
            )
        if 'company_id' not in db.table_columns(self.table):
            raise ApiError(
                'tenant_scope_unavailable',
                f'Tenant ownership is not installed for {self.table}.',
                503,
            )
        return company_id

    def _get(self, rid, company_id=None):
        company_id = company_id if company_id is not None else self._active_company_id()
        clauses = [f'{self.alias}.id=?', f'{self.alias}.company_id=?']
        if self.where:
            clauses.append(f'({self.where})')
        row = db.one(f"{self.select} WHERE {' AND '.join(clauses)}", (rid, company_id))
        if not row:
            raise ApiError('not_found', f'{self.name} not found.', 404)
        return row

    def _parse(self, body, creating, company_id):
        data, errors = {}, {}
        for f in self.fields:
            if f.name in body:
                try:
                    data[f.column] = f.parse(body[f.name], company_id=company_id)
                except FieldError as e:
                    errors[f.name] = e.code
            elif creating:
                try:
                    data[f.column] = f.parse(None, company_id=company_id)
                except FieldError as e:
                    errors[f.name] = e.code
        if errors:
            raise ApiError('validation', 'Validation failed.', 422, details=errors)
        return data

    # ------------------------------------------------------------------ endpoints
    def list_view(self):
        company_id = self._active_company_id()
        args, where, params = request.args, [f'{self.alias}.company_id=?'], [company_id]
        if self.where:
            where.append(f'({self.where})')
        text = (args.get('q') or '').strip()
        if text and self.search:
            where.append('(' + ' OR '.join(f'{c} LIKE ?' for c in self.search) + ')')
            params += [f'%{text}%'] * len(self.search)
        for name in self.filters:
            val = args.get(name)
            if val not in (None, ''):
                where.append(f'{self.alias}.{name}=?')
                params.append(1 if val in ('true', 'True') else 0 if val in ('false', 'False') else val)
        for flag, sql in self.flags.items():
            if args.get(flag) in ('1', 'true'):
                where.append(sql)
        if self.date_field:
            if args.get('date_from'):
                where.append(f'{self.alias}.{self.date_field} >= ?'), params.append(args['date_from'])
            if args.get('date_to'):
                where.append(f'{self.alias}.{self.date_field} <= ?'), params.append(args['date_to'])
        sql_where = (' WHERE ' + ' AND '.join(where)) if where else ''
        sort = args.get('sort') or ''
        col = sort.lstrip('-')
        order = f"{self.sortable[col]} {'DESC' if sort.startswith('-') else 'ASC'}, {self.alias}.id DESC" \
            if col in self.sortable else self.order
        total = db.scalar(f'SELECT COUNT(*) FROM ({self.select}{sql_where}) x', params)
        if args.get('all'):
            page, per = 1, 5000
        else:
            page, per = max(1, int(args.get('page') or 1)), min(200, max(1, int(args.get('per_page') or 25)))
        rows = db.q(f'{self.select}{sql_where} ORDER BY {order} LIMIT ? OFFSET ?', (*params, per, (page - 1) * per))
        return jsonify(items=serialize(rows), total=total, page=page, per_page=per)

    def get_view(self, rid):
        company_id = self._active_company_id()
        return jsonify(serialize(self._get(rid, company_id)))

    def create_view(self):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError('validation', 'JSON object expected.', 422)
        company_id = self._active_company_id()
        if 'company_id' in body:
            raise ApiError(
                'tenant_field_forbidden',
                'company_id is assigned by the server and cannot be supplied by the client.',
                422,
            )
        with db.transaction():
            data = self._parse(body, creating=True, company_id=company_id)
            data.update(self.base_values)
            if self.before_save:
                self.before_save(data, None, body)
            # This assignment is deliberately last: neither request data nor callbacks
            # may choose the tenant owner of a newly-created business row.
            data['company_id'] = company_id
            cols = list(data)
            rid = db.execute(f"INSERT INTO {self.table}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                             [data[c] for c in cols])
            if self.after_save:
                self.after_save(rid, data, None, body)
            audit.log('create', self.name, rid, serialize(data))
            row = self._get(rid, company_id)
        return jsonify(serialize(row)), 201

    def update_view(self, rid):
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ApiError('validation', 'JSON object expected.', 422)
        company_id = self._active_company_id()
        if 'company_id' in body:
            raise ApiError(
                'tenant_field_forbidden',
                'company_id is immutable and cannot be supplied by the client.',
                422,
            )
        with db.transaction():
            existing = self._get(rid, company_id)
            data = self._parse(body, creating=False, company_id=company_id)
            if self.before_save:
                self.before_save(data, existing, body)
            if data:
                sets = [f'{c}=?' for c in data]
                if 'updated_at' in db.table_columns(self.table):
                    sets.append("updated_at=datetime('now')")
                db.execute(
                    f"UPDATE {self.table} SET {','.join(sets)} WHERE id=? AND company_id=?",
                    [*data.values(), rid, company_id],
                )
            if self.after_save:
                self.after_save(rid, data, existing, body)
            changes = {k: [existing.get(k), v] for k, v in data.items() if existing.get(k) != v}
            if changes:
                audit.log('update', self.name, rid, serialize({k: v[1] for k, v in changes.items()}))
            row = self._get(rid, company_id)
        return jsonify(serialize(row))

    def delete_view(self, rid):
        company_id = self._active_company_id()
        with db.transaction():
            existing = self._get(rid, company_id)
            if self.before_delete:
                self.before_delete(existing)
            db.execute(f'DELETE FROM {self.table} WHERE id=? AND company_id=?', (rid, company_id))
            audit.log('delete', self.name, rid, serialize({k: v for k, v in existing.items()
                                                           if k in ('name', 'title', 'sku', 'code')}))
        return jsonify(ok=True)

    def register(self, app):
        bp = Blueprint(f'crud_{self.name}', __name__, url_prefix=f'/api/{self.name}')
        read, write = require(f'{self.perm}.read'), require(f'{self.perm}.write')
        bp.add_url_rule('', 'list', read(self.list_view), methods=['GET'])
        bp.add_url_rule('', 'create', write(self.create_view), methods=['POST'])
        bp.add_url_rule('/<int:rid>', 'get', read(self.get_view), methods=['GET'])
        bp.add_url_rule('/<int:rid>', 'update', write(self.update_view), methods=['PUT', 'PATCH'])
        bp.add_url_rule('/<int:rid>', 'delete', write(self.delete_view), methods=['DELETE'])
        app.register_blueprint(bp)
