"""
HOI4 save reader + stat extractor.

Reads HOI4 saves - binary ("HOI4bin", the default for multiplayer/autosaves) and
plain text ("HOI4txt"), optionally zip-compressed - and pulls out, per country:

  * military / naval / civilian IC   (sum of production-line `speed`, i.e. daily IC output)
  * army manpower in the field        (division `army_manpower_value`)
  * manpower of divisions waiting in the deployment queue (training / ready)
  * equipment held by divisions and air wings (for the cost graphs)
  * equipment unit costs from military production lines (the "cost library")

Binary saves store field names as 16-bit tokens; text saves spell them out. Both are
parsed into the same tree (text names are translated to the binary tokens via NAMES),
so the extraction code is shared. The token IDs were mapped by hand against HOI4
1.19.x saves and verified against the output of the original "aaa ... looker" scripts.
If Paradox reshuffles tokens in a future patch the binary numbers will come out as
zeros - the tool warns when that happens.
"""
import io
import re
import struct
import zipfile
from datetime import datetime

# ---- binary format primitives -------------------------------------------------------
EQ, OPEN, CLOSE = 0x0001, 0x0003, 0x0004
I32, F32, BOOL, QSTR, U32, USTR, F64, RGB, U64, I64 = (
    0x000c, 0x000d, 0x000e, 0x000f, 0x0014, 0x0017, 0x0167, 0x0243, 0x029c, 0x0317)

# ---- field tokens (name in plain-text saves -> token) -----------------------------
T_DATE = 0x284a              # date
T_COUNTRIES = 0x2d49         # countries
T_ID = 0x000b                # id / id={ id= type= }
T_TYPE = 0x00e1              # type
T_PRODUCTION = 0x2fb4        # production
T_MIL_LINE = 0x2fb0          # military_lines entry
T_NAV_LINE = 0x2fb1          # naval_lines entry
T_NAV_LINE2 = 0x3b7f         # ship_refit_lines entry
T_GEN_LINES = 0x2fb2         # general_lines (construction)
T_GEN_LINE = 0x284f          #   building (construction entry; 0x4cc1 = another kind)
T_SPEED = 0x006e             # speed  (daily IC output of the line)
T_COST = 0x2853              # cost   (IC cost of one unit of the equipment)
T_LINE_EQUIP = 0x3643        # equipment variant produced by the line
T_UNITS = 0x2faa             # units
T_DIVISION = 0x01d7          # division
T_DIV_OWNER = 0x3410         # logical_country
T_EXPED_OWNER = 0x2edd       # expeditionary_owner (country that lent the division)
T_EQUIPMENT = 0x2f4e         # equipment (list and entries)
T_AMOUNT = 0x01a1            # amount
T_MANPOWER = 0x36fa          # army_manpower (block of a division)
T_ARMY_MP = 0x36fb           # army_manpower_value
T_VALUE = 0x0308             # value
T_TAG = 0x2a02               # tag
T_DEPLOYMENT = 0x2f95        # deployment
T_DEP_LINE = 0x34b2          #   military_deployment_conveyor
T_DEP_ENTRY = 0x34b1         #   military_deployment_line
T_DEP_DIV = 0x34b0           #   military_deployment (a queued division)
T_TRAINING = 0x2fba          #   training (progress, 1 = finished)
T_MAX_TRAINING = 0x3316      #   max_training
T_AIR = 0x2fd7               # strategic_air (per-country air data)
T_AIR_POOL = 0x2fd8          # air_wing_pool
T_AIR_WING = 0x2fb5          # air_wings

# plain-text name -> binary token
NAMES = {
    'date': T_DATE, 'countries': T_COUNTRIES, 'id': T_ID, 'type': T_TYPE,
    'production': T_PRODUCTION, 'military_lines': T_MIL_LINE, 'naval_lines': T_NAV_LINE,
    'ship_refit_lines': T_NAV_LINE2, 'general_lines': T_GEN_LINES, 'building': T_GEN_LINE,
    'speed': T_SPEED, 'cost': T_COST, 'equipment_variant_index': T_LINE_EQUIP,
    'units': T_UNITS, 'division': T_DIVISION, 'logical_country': T_DIV_OWNER,
    'expeditionary_owner': T_EXPED_OWNER, 'equipment': T_EQUIPMENT, 'amount': T_AMOUNT,
    'army_manpower': T_MANPOWER, 'army_manpower_value': T_ARMY_MP, 'value': T_VALUE,
    'tag': T_TAG, 'deployment': T_DEPLOYMENT, 'military_deployment_conveyor': T_DEP_LINE,
    'military_deployment_line': T_DEP_ENTRY, 'military_deployment': T_DEP_DIV,
    'training': T_TRAINING, 'max_training': T_MAX_TRAINING, 'strategic_air': T_AIR, 'air_wing_pool': T_AIR_POOL,
    'air_wings': T_AIR_WING,
}

ALL = True  # spec marker: keep whole subtree

# Only these parts of the (40-90 MB) save are materialised; everything else is skipped.
SPEC = {
    T_COUNTRIES: {
        'str': {
            T_PRODUCTION: {T_MIL_LINE: ALL, T_NAV_LINE: ALL, T_NAV_LINE2: ALL, T_GEN_LINES: ALL},
            T_UNITS: {T_DIVISION: {T_ID: ALL, T_MANPOWER: ALL, T_EQUIPMENT: ALL}},
            # any other block holding divisions (volunteers_transfer, exile_divisions_transfer..)
            '*': {T_DIVISION: {T_MANPOWER: ALL}},
            T_DEPLOYMENT: {T_DEP_LINE: ALL},
        }
    },
    T_AIR: {'str': {T_AIR_POOL: {T_AIR_WING: {T_ID: ALL, T_EQUIPMENT: ALL}}}},
}


class Node(list):
    """Ordered list of (key, value) pairs. Array items have key None."""
    __slots__ = ()

    def get(self, key, default=None):
        for k, v in self:
            if k == key:
                return v
        return default

    def all(self, key):
        return [v for k, v in self if k == key]


class SaveFormatError(Exception):
    pass


def read_save_bytes(path):
    with open(path, 'rb') as f:
        data = f.read()
    if data[:2] == b'PK':  # compressed save
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = z.namelist()
            data = z.read('gamestate' if 'gamestate' in names else names[0])
    if not (data.startswith(b'HOI4bin') or data.startswith(b'HOI4txt')):
        raise SaveFormatError('not a HOI4 save file')
    return data


def header_date(data):
    """In-game date from the save header."""
    if data.startswith(b'HOI4txt'):
        m = re.search(rb'\ndate="?(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?', data[:4096])
        if not m:
            return None
        y, mo, d, h = (int(x) if x else 0 for x in m.groups())
        return datetime(y, mo, d, min(h, 23))  # the game writes hour 24 for end of day
    # binary: hours since 5000 BC, 365-day years
    i = data.find(b'\x4a\x28\x01\x00\x0c\x00', 0, 4096)
    if i < 0:
        return None
    return hours_to_datetime(struct.unpack_from('<i', data, i + 6)[0])


_MONTHS = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)


def hours_to_datetime(v):
    hour = v % 24
    days = v // 24
    year, doy = divmod(days, 365)
    m = 0
    while doy >= _MONTHS[m]:
        doy -= _MONTHS[m]
        m += 1
    return datetime(year - 5000, m + 1, doy + 1, hour)


# ---- parser --------------------------------------------------------------------------
_SIZE = [0] * 65536
for _t in (I32, U32):
    _SIZE[_t] = 4
for _t in (F32, F64, U64, I64):
    _SIZE[_t] = 8
_SIZE[BOOL] = 1
_SIZE[QSTR] = -1
_SIZE[USTR] = -1


def _skip_value(data, p):
    """p points just after '='; skip one value (scalar or whole block). Returns new p."""
    t = data[p] | (data[p + 1] << 8)
    p += 2
    if t != OPEN:
        s = _SIZE[t]
        if s > 0:
            return p + s
        if s < 0:
            return p + 2 + (data[p] | (data[p + 1] << 8))
        return p  # bare token value / rgb marker
    size = _SIZE
    depth = 1
    while depth:
        t = data[p] | (data[p + 1] << 8)
        p += 2
        if t == OPEN:
            depth += 1
        elif t == CLOSE:
            depth -= 1
        else:
            s = size[t]
            if s > 0:
                p += s
            elif s < 0:
                p += 2 + (data[p] | (data[p + 1] << 8))
    return p


def parse(data, spec=SPEC):
    """Parse a save (binary or text) into a Node tree, materialising only `spec`."""
    if data.startswith(b'HOI4txt'):
        return parse_text(data, spec)
    return parse_binary(data, spec)


def parse_binary(data, spec=SPEC):
    unpack_q = struct.Struct('<q').unpack_from
    unpack_i = struct.Struct('<i').unpack_from
    unpack_Q = struct.Struct('<Q').unpack_from
    n = len(data)

    def scalar(t, p):
        if t == I32:
            return unpack_i(data, p)[0], p + 4
        if t == F32:
            return unpack_q(data, p)[0] / 100000.0, p + 8
        if t == U32:
            return unpack_i(data, p)[0] & 0xffffffff, p + 4
        if t == QSTR or t == USTR:
            l = data[p] | (data[p + 1] << 8)
            return data[p + 2:p + 2 + l].decode('utf-8', 'replace'), p + 2 + l
        if t == BOOL:
            return data[p] != 0, p + 1
        if t == F64:
            return unpack_q(data, p)[0] / 32768.0, p + 8
        if t == U64:
            return unpack_Q(data, p)[0], p + 8
        if t == I64:
            return unpack_q(data, p)[0], p + 8
        return ('tok', t), p  # bare token

    def sub_spec(spec, key):
        if spec is ALL:
            return ALL
        s = spec.get(key)
        if s is None and isinstance(key, str):
            s = spec.get('str')
        if s is None:
            s = spec.get('*')
        return s

    def obj(p, spec):
        node = Node()
        pend = None
        have = False
        while p + 2 <= n:
            t = data[p] | (data[p + 1] << 8)
            p += 2
            if t == CLOSE:
                if have:
                    node.append((None, pend))
                return node, p
            if t == OPEN:  # anonymous block inside an array
                if have:
                    node.append((None, pend))
                    have = False
                if spec is ALL:
                    child, p = obj(p, ALL)
                    node.append((None, child))
                else:
                    p = _skip_value(data, p - 2)
                continue
            if t == EQ:
                key = pend
                have = False
                nt = data[p] | (data[p + 1] << 8)
                if nt == OPEN:
                    s = sub_spec(spec, key)
                    if s is None:
                        p = _skip_value(data, p)
                    else:
                        child, p = obj(p + 2, s)
                        node.append((key, child))
                else:
                    p += 2
                    if nt == RGB:
                        p = _skip_value(data, p)  # rgb { r g b }
                        val = ('rgb',)
                    else:
                        val, p = scalar(nt, p)
                    node.append((key, val))
                continue
            # a key or an array item
            if have:
                node.append((None, pend))
            if _SIZE[t] or t in (QSTR, USTR):
                pend, p = scalar(t, p)
            else:
                pend = t
            have = True
        return node, p

    root, _ = obj(7, spec)
    return root


# ---- plain-text parser -------------------------------------------------------------
_TXT_TOKEN = re.compile(rb'[ \t\r\n]*(?:([{}=])|"((?:[^"\\]|\\.)*)"|([^\s{}="]+))')
_TXT_BRACE = re.compile(rb'"(?:[^"\\]|\\.)*"|[{}]')
_INT = re.compile(rb'-?\d+\Z')
_FLOAT = re.compile(rb'-?\d*\.\d+\Z')


def _txt_scalar(raw):
    if _INT.match(raw):
        return int(raw)
    if _FLOAT.match(raw):
        return float(raw)
    return raw.decode('utf-8', 'replace')


def _txt_skip_block(data, p):
    """p is just after an opening '{'. Return position just after its matching '}'.
    (Indentation can't be trusted: the game sometimes closes blocks on the same line.)"""
    depth = 1
    for m in _TXT_BRACE.finditer(data, p):
        if m.group() == b'{':
            depth += 1
        elif m.group() == b'}':
            depth -= 1
            if depth == 0:
                return m.end()
    return len(data)


def parse_text(data, spec=SPEC):
    match = _TXT_TOKEN.match
    names = NAMES
    n = len(data)

    def tok(p):
        """-> (kind, value, new_p); kind: '{', '}', '=', 's' (scalar) or None at EOF."""
        m = match(data, p)
        if not m or m.end() == p:
            return None, None, n
        if m.group(1):
            return m.group(1).decode(), None, m.end()
        if m.group(2) is not None:
            return 's', m.group(2).decode('utf-8', 'replace'), m.end()
        return 's', _txt_scalar(m.group(3)), m.end()

    def key_of(v):
        # names -> binary tokens; anything else (tags, numeric ids) stays a string so it
        # can never collide with a token number
        return names.get(v, v) if isinstance(v, str) else str(v)

    def sub_spec(spec, key):
        if spec is ALL:
            return ALL
        s = spec.get(key)
        if s is None and isinstance(key, str):
            s = spec.get('str')
        if s is None:
            s = spec.get('*')
        return s

    def obj(p, spec):
        node = Node()
        pend = None
        have = False
        while True:
            kind, val, p = tok(p)
            if kind is None or kind == '}':
                if have:
                    node.append((None, pend))
                return node, p
            if kind == '{':
                if have:
                    node.append((None, pend))
                    have = False
                if spec is ALL:
                    child, p = obj(p, ALL)
                    node.append((None, child))
                else:
                    p = _txt_skip_block(data, p)
                continue
            if kind == '=':
                key = key_of(pend) if have else None
                have = False
                kind2, val2, p2 = tok(p)
                if kind2 == '{':
                    s = sub_spec(spec, key)
                    if s is None:
                        p = _txt_skip_block(data, p2)
                    else:
                        child, p = obj(p2, s)
                        node.append((key, child))
                    continue
                if kind2 == 's':
                    # `key=` with an empty value is followed directly by the next key
                    kind3, _, _ = tok(p2)
                    if kind3 == '=':
                        node.append((key, ''))
                        pend, have, p = val2, True, p2
                    else:
                        node.append((key, val2))
                        p = p2
                    continue
                node.append((key, ''))
                continue
            # scalar: a key or an array item
            if have:
                node.append((None, pend))
            pend, have = val, True

    start = data.find(b'\n') + 1  # skip the HOI4txt magic line
    root, _ = obj(start, spec)
    return root


# ---- extraction --------------------------------------------------------------------
def _eq_list(node):
    """[(equipment_id, amount), ...] from an equipment={...} list."""
    out = []
    if not isinstance(node, Node):
        return out
    for k, e in node:
        if k != T_EQUIPMENT or not isinstance(e, Node):
            continue
        ident = e.get(T_ID)
        amt = e.get(T_AMOUNT)
        if isinstance(ident, Node) and amt is not None:
            out.append((ident.get(T_ID), float(amt)))
    return out


def _first_value(mp_node):
    """army_manpower_value={ value={ tag=X value=N } ... } -> (X, N) of the first entry."""
    if not isinstance(mp_node, Node):
        return None, 0
    v = mp_node.get(T_VALUE)
    if isinstance(v, Node):
        return v.get(T_TAG), v.get(T_VALUE) or 0
    return None, 0


def extract(path):
    """Parse one save and return a plain dict with everything the graphs need."""
    data = read_save_bytes(path)
    date = header_date(data)
    root = parse(data)
    del data

    countries = {}
    line_costs = []  # (country, equipment_id, cost) in file order
    training_mp = {}  # manpower the original script subtracted, by manpower tag
    divisions = []   # (owner, [(eq_id, amount)])
    cnodes = root.get(T_COUNTRIES) or Node()
    for tag, c in cnodes:
        if not isinstance(tag, str) or not isinstance(c, Node):
            continue
        rec = {'mil': 0.0, 'nav': 0.0, 'civ': 0.0, 'field_manpower': 0}
        prod = c.get(T_PRODUCTION)
        if isinstance(prod, Node):
            for line in prod.all(T_MIL_LINE):
                rec['mil'] += line.get(T_SPEED) or 0.0
                eq = line.get(T_LINE_EQUIP)
                cost = line.get(T_COST)
                if isinstance(eq, Node) and cost is not None:
                    line_costs.append((tag, eq.get(T_ID), cost))
            for line in prod.all(T_NAV_LINE) + prod.all(T_NAV_LINE2):
                rec['nav'] += line.get(T_SPEED) or 0.0
            for gl in prod.all(T_GEN_LINES):
                for _, line in gl:  # construction entries (several kinds)
                    if isinstance(line, Node):
                        rec['civ'] += line.get(T_SPEED) or 0.0
        units = c.get(T_UNITS)
        if isinstance(units, Node):
            for d in units.all(T_DIVISION):
                mp = d.get(T_MANPOWER)
                mtag, mval = _first_value(mp.get(T_ARMY_MP) if isinstance(mp, Node) else None)
                if mtag == tag:
                    rec['field_manpower'] += mval
                owner = d.get(T_EXPED_OWNER) or d.get(T_DIV_OWNER) or tag
                divisions.append((owner, _eq_list(d.get(T_EQUIPMENT))))
        # Deployment queue, replicating the original regex script exactly:
        #  * every queued division's army_manpower_value (own tag) counts towards the nation;
        #  * a `deployment={` block that contains a fractional training/max_training value
        #    (finished divisions are written `training=1`) has ALL its queued
        #    divisions subtracted, keyed by their manpower tag;
        #  * each unfinished `military_deployment={` block is subtracted again.
        # divisions in transit (volunteers_transfer, exile_divisions_transfer, ...) count as
        # fielded manpower - the original counted every army_manpower_value in the country
        for k, blk in c:
            if k == T_UNITS or k == T_DEPLOYMENT or not isinstance(blk, Node):
                continue
            for d in blk.all(T_DIVISION):
                mp = d.get(T_MANPOWER)
                mtag, mval = _first_value(mp.get(T_ARMY_MP) if isinstance(mp, Node) else None)
                if mtag == tag:
                    rec['field_manpower'] += mval
        rec['queue_manpower'] = 0
        dep = c.get(T_DEPLOYMENT)
        if isinstance(dep, Node):
            queued = []
            for dl in dep.all(T_DEP_LINE):
                for entry in dl.all(T_DEP_ENTRY):
                    for dv in entry.all(T_DEP_DIV):
                        mtag, mval = _first_value(dv.get(T_ARMY_MP))
                        # the original's `training=<decimal>` regex also matched max_training
                        unfinished = any(isinstance(t, (int, float)) and t != int(t)
                                         for t in (dv.get(T_TRAINING), dv.get(T_MAX_TRAINING)))
                        queued.append((mtag, mval, unfinished))
            any_unfinished = any(u for _, _, u in queued)
            for mtag, mval, unfinished in queued:
                if mtag == tag:
                    rec['queue_manpower'] += mval
                if mtag:
                    sub = (mval if any_unfinished else 0) + (mval if unfinished else 0)
                    if sub:
                        training_mp[mtag] = training_mp.get(mtag, 0) + sub
        countries[tag] = rec

    air = []  # (tag, [(eq_id, amount)])
    anodes = root.get(T_AIR) or Node()
    for tag, a in anodes:
        if not isinstance(tag, str) or not isinstance(a, Node):
            continue
        for pool in a.all(T_AIR_POOL):
            for wing in pool.all(T_AIR_WING):
                air.append((tag, _eq_list(wing.get(T_EQUIPMENT))))

    return {
        'date': date.isoformat() if date else None,
        'countries': countries,
        'training_manpower': training_mp,
        'line_costs': line_costs,
        'divisions': divisions,
        'air_wings': air,
    }
