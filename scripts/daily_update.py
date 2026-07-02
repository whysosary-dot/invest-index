#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
invest-index 일일 업데이트 스크립트 (SKILL.md 로직의 스크립트판 — 기능 동일)

명령:
  python3 scripts/daily_update.py last
      → 모든 지표의 마지막 label 출력 (Claude가 index.html을 읽을 필요 없음)

  python3 scripts/daily_update.py auto
      → 완전 자동 지표 일괄 수집·반영: pubg, jmtba, palm_pals, 카지노 3사
        + DATA_UPDATED 갱신 + JS 3단계 검증. (DRAM/NAND, clarksons는 별도)

  python3 scripts/daily_update.py append <indicator_id> <label> <value> [--note "..."] [--src "..."] [--product N]
      → 지표 데이터 1건 추가 (DRAM/NAND 등 수동 수집분 반영용) + 검증 + DATA_UPDATED 갱신
        중복 label은 자동 skip. --product N 은 products[N].data 에 추가.

  python3 scripts/daily_update.py verify
      → JS 검증만 (이중콤마 + node 문법)

모든 변경은 저장 전 검증을 통과해야 하며, 실패 시 원본 유지 + exit 1.
"""
import json, os, re, sys, subprocess, tempfile, time
from datetime import datetime, timedelta

IDX = os.environ.get('IDX_PATH', 'index.html')
DART_KEY = 'b6953d1daadfdbcd0bbf4790f21aa23b7fd08794'
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
      'Accept': 'text/html,application/xhtml+xml'}

# ───────────────────── index.html 구조 파서 ─────────────────────

def load():
    with open(IDX, encoding='utf-8') as f:
        return f.read()

def indicator_span(content, iid):
    m = re.search(r'id:\s*"' + re.escape(iid) + r'"', content)
    if not m:
        return None
    start = m.start()
    nxt = re.search(r'\n\s*id:\s*"', content[m.end():])
    end = m.end() + nxt.start() if nxt else len(content)
    return (start, end)

def data_array_span(content, iid, product=None):
    """해당 지표(또는 products[N])의 data 배열 내부 (open_idx, close_idx) — '[' 다음 ~ ']' 위치"""
    span = indicator_span(content, iid)
    if not span:
        return None
    s, e = span
    seg_start = s
    if product is not None:
        pm = re.search(r'products\s*:\s*\[', content[s:e])
        if not pm:
            return None
        pos = s + pm.end()
        # products 안에서 N번째 data: [ 찾기
        count = -1
        while True:
            dm = re.search(r'data\s*:\s*\[', content[pos:e])
            if not dm:
                return None
            count += 1
            pos_open = pos + dm.end()
            if count == product:
                open_idx = pos_open
                break
            pos = pos_open
    else:
        dm = re.search(r'data\s*:\s*\[', content[s:e])
        if not dm:
            return None
        open_idx = s + dm.end()
    depth = 1
    i = open_idx
    while i < len(content):
        c = content[i]
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                return (open_idx, i)
        i += 1
    return None

def last_label(content, iid, product=None):
    span = data_array_span(content, iid, product)
    if not span:
        return None
    seg = content[span[0]:span[1]]
    labels = re.findall(r'label:\s*"([^"]+)"', seg)
    return labels[-1] if labels else None

def has_label(content, iid, label, product=None):
    span = data_array_span(content, iid, product)
    if not span:
        return False
    return f'label: "{label}"' in content[span[0]:span[1]] or f'label:"{label}"' in content[span[0]:span[1]]

def append_point(content, iid, label, value, note=None, src=None, product=None):
    """data 배열 끝에 항목 추가. 중복 label이면 그대로 반환(None flag)."""
    if has_label(content, iid, label, product):
        return content, False
    span = data_array_span(content, iid, product)
    if not span:
        raise RuntimeError(f'지표 {iid} (product={product}) data 배열을 찾지 못함')
    open_idx, close_idx = span
    # 닫는 ] 직전에 '// ↑ 새 데이터를 여기에 추가' 마커가 있으면 그 줄 앞에 삽입
    seg = content[open_idx:close_idx]
    insert_at = close_idx
    marker = re.search(r'\n([ \t]*// ↑ 새 데이터를[^\n]*)\s*$', seg)
    if marker:
        insert_at = open_idx + marker.start()
    before = content[:insert_at].rstrip()
    comma = ',' if before.endswith('}') else ''
    parts = [f'label: "{label}"', f'value: {value}']
    if src:
        parts.append('src: "' + src.replace('"', '\\"') + '"')
    if note:
        parts.append('note: "' + note.replace('"', '\\"') + '"')
    entry = '            { ' + ', '.join(parts) + ' },'
    tail = content[insert_at:]
    if marker:
        # tail은 '\n// ↑ 새 데이터를...' 마커 줄부터 시작
        return before + comma + '\n' + entry + tail, True
    # tail은 ']' 부터 시작 — 닫는 괄호 들여쓰기 복원
    return before + comma + '\n' + entry + '\n        ' + tail, True

def bump_data_updated(content):
    today = datetime.now().strftime('%Y-%m-%d')
    return re.sub(r'const DATA_UPDATED = "[^"]*";', f'const DATA_UPDATED = "{today}";', content)

def verify(content):
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', content, re.DOTALL)
    biggest = max(scripts, key=len)
    for i, l in enumerate(biggest.split('\n')):
        if ',,' in l:
            print(f'❌ Step B 실패 — 이중 콤마 line {i+1}: {l.strip()[:100]}')
            return False
    with tempfile.NamedTemporaryFile('w', suffix='.js', delete=False, encoding='utf-8') as t:
        t.write(biggest); tmp = t.name
    r = subprocess.run(['node', '--check', tmp], capture_output=True, text=True)
    os.unlink(tmp)
    if r.returncode != 0:
        print('❌ Step C 실패 — JS 문법 오류:\n' + r.stderr[:500])
        return False
    print('✅ JS 검증 통과 (이중콤마 없음 + 문법 OK)')
    return True

def save(content):
    if not verify(content):
        print('원본 유지, exit 1'); sys.exit(1)
    with open(IDX, 'w', encoding='utf-8') as f:
        f.write(content)

# ───────────────────── 자동 수집기 ─────────────────────

def fetch_pubg(content, changes):
    """PUBG 스팀 동접 일별 스냅샷 (Steam API) — 현행 데이터 패턴(일별 label)과 동일"""
    import requests
    try:
        label = datetime.now().strftime('%Y-%m-%d')
        if has_label(content, 'pubg', label):
            print('  pubg: 오늘 스냅샷 이미 존재 — skip')
            return content
        r = requests.get('https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/?appid=578080', timeout=15)
        val = r.json()['response']['player_count']
        hh = datetime.now().strftime('%H:00')
        content, ok = append_point(content, 'pubg', label, int(val), note=f'스팀 동접 스냅샷 ({hh} KST)')
        if ok:
            changes.append(f'pubg {label} = {val:,}')
    except Exception as e:
        print(f'  pubg skip: {e}')
    return content

def fetch_jmtba(content, changes):
    """Trading Economics meta description"""
    import requests
    try:
        r = requests.get('https://tradingeconomics.com/japan/machine-tool-orders', headers=UA, timeout=20)
        m = re.search(r'(\d+)\s*JPY Million in (\w+) from (\d+)\s*JPY Million in (\w+) of (\d{4})', r.text)
        if m:
            month_map = {'January':1,'February':2,'March':3,'April':4,'May':5,'June':6,'July':7,
                         'August':8,'September':9,'October':10,'November':11,'December':12}
            val_mn = int(m.group(1))
            mon_to, mon_from, from_year = m.group(2), m.group(4), int(m.group(5))
            if mon_to in month_map and mon_from in month_map:
                # to월이 from월보다 앞이면(12→1 래핑) 연도 +1
                year = from_year + 1 if month_map[mon_to] < month_map[mon_from] else from_year
                label = f'{year}-{month_map[mon_to]:02d}'
                value = round(val_mn / 100, 1)  # 百万円 → 億円
                content, ok = append_point(content, 'jmtba', label, value,
                                           src='https://tradingeconomics.com/japan/machine-tool-orders',
                                           note=f'Trading Economics ({val_mn:,}百万円)')
                if ok:
                    changes.append(f'jmtba {label} = {value}억엔')
    except Exception as e:
        print(f'  jmtba skip: {e}')
    return content

def fetch_palm_pals(content, changes):
    """Google Trends 주간 (pytrends) — 마지막 label 7일 이상 경과 시에만"""
    try:
        last = last_label(content, 'palm_pals')
        if not last:
            return content
        if len(last) == 7:  # YYYY-MM → 해당 월 마지막 일요일
            y, m = int(last[:4]), int(last[5:7])
            d = datetime(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
            while d.weekday() != 6:
                d -= timedelta(days=1)
            cutoff = d
        else:
            cutoff = datetime.strptime(last, '%Y-%m-%d')
        if (datetime.today() - cutoff).days < 7:
            print('  palm_pals: 7일 미경과 — skip')
            return content
        from pytrends.request import TrendReq
        start_date = (cutoff + timedelta(days=7)).strftime('%Y-%m-%d')
        end_date = datetime.today().strftime('%Y-%m-%d')
        pytrends = TrendReq(hl='en-US', tz=360)
        time.sleep(10)
        pytrends.build_payload(['palm pals'], cat=0, timeframe=f'{start_date} {end_date}', geo='US', gprop='')
        df = pytrends.interest_over_time()
        if df is not None and not df.empty:
            for idx, row in df.iterrows():
                val = int(row['palm pals'])
                label = idx.strftime('%Y-%m-%d')
                if val > 0 and label > cutoff.strftime('%Y-%m-%d'):
                    content, ok = append_point(content, 'palm_pals', label, val, note='Google Trends US (weekly)')
                    if ok:
                        changes.append(f'palm_pals {label} = {val}')
    except Exception as e:
        print(f'  palm_pals skip: {e}')
    return content

CASINOS = [
    ('lotte_casino',    '00231372', '드롭액', lambda nm: ('잠정' in nm and '영업' in nm)),
    ('gkl_casino',      '00557508', '드롭액', lambda nm: ('잠정' in nm and '영업' in nm and '연결재무제표기준' not in nm)),
    ('paradise_casino', '00171265', '드랍액', lambda nm: ('잠정' in nm and '영업' in nm)),
]

def parse_dart_doc(rcept_no):
    """DART document.xml → 태그 제거·공백 정규화된 텍스트"""
    import requests, zipfile, io
    r = requests.get('https://opendart.fss.or.kr/api/document.xml',
                     params={'crtfc_key': DART_KEY, 'rcept_no': rcept_no}, timeout=30)
    z = zipfile.ZipFile(io.BytesIO(r.content))
    text = ''
    for n in z.namelist():
        raw = z.read(n)
        for enc in ('utf-8', 'euc-kr', 'cp949'):
            try:
                text += raw.decode(enc); break
            except Exception:
                continue
    text = re.sub(r'<[^>]+>', ' ', text)
    return re.sub(r'\s+', ' ', text)

def extract_period(text, rcept_dt):
    m = re.search(r'당기실적\s+(\d{4})-(\d{2})-\d{2}\s*~', text)
    if m:
        return f'{m.group(1)}-{m.group(2)}'
    m = re.search(r'당기실적\((\d{2})년(\d{2})월\)', text)
    if m:
        return f'20{m.group(1)}-{m.group(2)}'
    m = re.search(r'\((\d{4})년\s*(\d{1,2})월\)', text)
    if m:
        return f'{m.group(1)}-{int(m.group(2)):02d}'
    d = datetime.strptime(rcept_dt, '%Y%m%d')
    prev = d.replace(day=1) - timedelta(days=1)
    return prev.strftime('%Y-%m')

def fetch_casinos(content, changes):
    import requests
    for iid, corp, drop_kw, name_filter in CASINOS:
        try:
            last = last_label(content, iid)
            if not last:
                continue
            y, m = int(last[:4]), int(last[5:7])
            bgn = (datetime(y, m, 1) + timedelta(days=32)).replace(day=1).strftime('%Y%m%d')
            end = datetime.now().strftime('%Y%m%d')
            if bgn > end:
                continue
            disclosures, page = [], 1
            while True:
                r = requests.get('https://opendart.fss.or.kr/api/list.json',
                                 params={'crtfc_key': DART_KEY, 'corp_code': corp, 'bgn_de': bgn,
                                         'end_de': end, 'page_count': 100, 'page_no': page}, timeout=20).json()
                disclosures += r.get('list', [])
                if page >= int(r.get('total_page', 1)):
                    break
                page += 1
            cands = [d for d in disclosures if name_filter(d.get('report_nm', ''))]
            by_month = {}
            for d in cands:
                by_month.setdefault(d['rcept_dt'][:6], []).append(d)
            for month, ds in sorted(by_month.items()):
                ds.sort(key=lambda x: x['rcept_no'])
                if iid == 'paradise_casino':
                    try_list = [ds[0], ds[-1]] if len(ds) > 1 else ds
                else:
                    ds2 = [d for d in ds if '기재정정' not in d['report_nm']] or ds
                    try_list = [ds2[-1]]
                for d in try_list:
                    text = parse_dart_doc(d['rcept_no'])
                    if drop_kw not in text and d is not try_list[-1]:
                        continue
                    label = extract_period(text, d['rcept_dt'])
                    # 유효성: 공시일 기준 1~2달 이내
                    ld = datetime.strptime(label + '-01', '%Y-%m-%d')
                    rd = datetime.strptime(d['rcept_dt'], '%Y%m%d')
                    gap = (rd.year - ld.year) * 12 + rd.month - ld.month
                    if gap < 1 or gap > 2:
                        continue
                    mm = re.search(r'카지노\s*매출액\s+([\d,]+)', text)
                    if not mm:
                        continue
                    sales = int(mm.group(1).replace(',', ''))
                    drop = None
                    kpos = text.find(drop_kw)
                    if kpos >= 0:
                        window = text[kpos:kpos + 700]
                        dm = re.search(r'대비\s*증감율\s*\(%\)\s+([\d,]{5,})', window) or re.search(r'=+\s+([\d,]{5,})', window)
                        if dm:
                            v = int(dm.group(1).replace(',', ''))
                            if v >= 30000:
                                drop = v
                    value = round(sales / 100, 1)
                    if drop:
                        hold = round(sales / drop * 100, 2)
                        note = f'드롭 {drop/100:.0f}억 | 홀드율 {hold}%'
                    else:
                        note = f'순매출 {sales/100:.0f}억 (드롭 미공시)'
                    content, ok = append_point(content, iid, label, value, note=note)
                    if ok:
                        changes.append(f'{iid} {label} = {value}억')
                    break
        except Exception as e:
            print(f'  {iid} skip: {e}')
    return content

# ───────────────────── main ─────────────────────

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'last'
    content = load()

    if cmd == 'last':
        ids = re.findall(r'id:\s*"(\w+)"', content)
        for iid in dict.fromkeys(ids):
            print(f'{iid}: {last_label(content, iid)}')
            span = indicator_span(content, iid)
            seg = content[span[0]:span[1]]
            pm = re.search(r'products\s*:\s*\[', seg)
            if pm:
                names = re.findall(r'name:\s*"([^"]+)"', seg[pm.end():])
                for i, n in enumerate(names):
                    print(f'  product[{i}] {n}: {last_label(content, iid, product=i)}')
        return

    if cmd == 'verify':
        sys.exit(0 if verify(content) else 1)

    if cmd == 'append':
        iid, label, value = sys.argv[2], sys.argv[3], sys.argv[4]
        note = sys.argv[sys.argv.index('--note') + 1] if '--note' in sys.argv else None
        src = sys.argv[sys.argv.index('--src') + 1] if '--src' in sys.argv else None
        product = int(sys.argv[sys.argv.index('--product') + 1]) if '--product' in sys.argv else None
        content, ok = append_point(content, iid, label, value, note=note, src=src, product=product)
        if not ok:
            print(f'{iid} {label}: 이미 존재 — skip'); return
        content = bump_data_updated(content)
        save(content)
        print(f'✅ {iid} {label} = {value} 추가 완료' + (f' (product {product})' if product is not None else ''))
        return

    if cmd == 'auto':
        changes = []
        print('· PUBG (SteamCharts)...'); content = fetch_pubg(content, changes)
        print('· JMTBA (Trading Economics)...'); content = fetch_jmtba(content, changes)
        print('· Palm Pals (Google Trends)...'); content = fetch_palm_pals(content, changes)
        print('· 카지노 3사 (DART)...'); content = fetch_casinos(content, changes)
        if not changes:
            print('\n변경 없음 — 저장/커밋 불필요'); return
        content = bump_data_updated(content)
        save(content)
        print('\n===== 변경 요약 =====')
        for c in changes:
            print(' +', c)
        return

    print(__doc__)

if __name__ == '__main__':
    main()
