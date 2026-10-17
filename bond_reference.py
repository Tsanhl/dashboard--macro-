"""Public BondTerminal last-close references; never CDS/OAS or signal confirmation."""
import copy
import hashlib
import json
import re
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

BONDS = [('CRWV', 'CoreWeave', 'US21873SAK42', '9.625% 2032-07-15'),
         ('ORCL', 'Oracle', 'US68389XDX03', '4.95% 2031-02-04')]


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.skip = 0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.skip = max(0, self.skip-1)
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)


def parse_quote(html, bond, asof, retrieved_at):
    ident, name, isin, instrument = bond
    parser = Text(); parser.feed(html)
    text = ' '.join(' '.join(parser.parts).split())
    if isin not in text or name.lower() not in text.lower():
        raise ValueError('Instrument identity not confirmed')
    matches = re.findall(r'Last close\s*\((\d{1,2}\s+[A-Za-z]{3}\s+\d{4})\)\s*:\s*price\s+([\d.]+),?\s*yield to worst\s+([\d.]+)%', text, re.I)
    if len(set(matches)) != 1:
        raise ValueError('No single dated last-close price/YTW pair')
    d, price, ytw = matches[0]
    day = datetime.strptime(d, '%d %b %Y').date().isoformat()
    if day > asof or not 0 < float(price) <= 300 or not 0 <= float(ytw) < 100:
        raise ValueError('Future or invalid reference quote')
    if not re.search(r'Settlement\s*:?\s*T\+2', text, re.I) or 'semiannual' not in text.lower() or 'USD' not in text:
        raise ValueError('Currency, coupon convention or settlement not confirmed')
    return dict(instrument=instrument, isin=isin, currency='USD', price_date=day,
                clean_price_per_100=float(price), yield_pct=float(ytw), yield_type='YTW semiannual',
                provider='BondTerminal', price_basis='vendor_close_per_100',
                settlement_assumption='T+2; valuation/settlement dates not independently verified',
                settlement_basis='T+2', settlement_verified=False,
                source=f'https://bondterminal.com/calculator/{isin}', source_label='About this bond: Last close',
                retrieved_at=retrieved_at, primary_trade_verified=False, eligible_for_signal=False)


def merge_quote(issuer, quote):
    rows = issuer.setdefault('reference_quotes', [])
    active = [r for r in rows if not r.get('superseded') and r.get('price_date') == quote['price_date']
              and r.get('isin') == quote['isin'] and r.get('provider') == quote['provider']]
    fields = ('clean_price_per_100', 'yield_pct', 'price_basis', 'yield_type', 'currency')
    if len(active) == 1 and all(active[0].get(k) == quote.get(k) for k in fields):
        return 'unchanged'
    for row in active:
        row.update(superseded=True, superseded_at=quote['retrieved_at'])
    if active:
        quote['revision_note'] = '同一觀察日來源修訂；不當作新交易日變化。'
    rows.append(quote)
    return 'revision' if active else 'new_observation'


def collect(path, asof, evidence_dir):
    doc = json.loads(path.read_text()) if path.exists() else {'issuers': []}
    doc = copy.deepcopy(doc)
    stamp = datetime.now(timezone.utc).isoformat()
    results = []
    for bond in BONDS:
        ident, name, isin, _ = bond
        issuer = next((i for i in doc['issuers'] if i.get('id') == ident), None)
        if issuer is None:
            issuer = {'id': ident, 'name': name, 'series': [], 'reference_quotes': []}; doc['issuers'].append(issuer)
        try:
            url = f'https://bondterminal.com/calculator/{isin}'
            req = urllib.request.Request(url, headers={'User-Agent': 'MacroBoard/1.0 (personal research)'})
            with urllib.request.urlopen(req, timeout=30) as response:
                if response.geturl().split('?')[0].rstrip('/') != url:
                    raise ValueError('Source redirected; reference unconfirmed')
                raw = response.read(2_000_001)
            if len(raw) > 2_000_000: raise ValueError('Unexpected source size')
            (evidence_dir/f'{isin}.html').write_bytes(raw)
            quote = parse_quote(raw.decode('utf-8'), bond, asof, stamp)
            quote['source_sha256'] = hashlib.sha256(raw).hexdigest()
            state = merge_quote(issuer, quote)
            issuer.update(latest_source_observation=quote['price_date'],
                          note='延遲第三方債價／YTW參考；不是CDS或OAS，信用改善未確認。')
            results.append({'issuer': name, 'state': state, 'date': quote['price_date']})
        except Exception as exc:
            results.append({'issuer': name, 'state': 'unconfirmed', 'reason': str(exc)})
        issuer['last_checked_at'] = stamp
    doc['last_checked_at'] = stamp
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2)+'\n')
    return results
