#!/usr/bin/env python3
"""hajimi 爬到的 key 全量体检 — 只读测试，不写库"""
import glob, re, os, json, sys
from concurrent.futures import ThreadPoolExecutor
import urllib.request
import urllib.error

PROVIDERS = {
    'deepseek':  ('https://api.deepseek.com/models', re.compile(r'\bsk-[A-Za-z0-9_\-]{20,}\b')),
    'aliyun':    ('https://dashscope.aliyuncs.com/compatible-mode/v1/models', re.compile(r'\bsk-[A-Za-z0-9_\-]{20,}\b')),
    'openai':    ('https://api.openai.com/v1/models', re.compile(r'\bsk-[A-Za-z0-9_\-]{20,}\b')),
    'anthropic': ('https://api.anthropic.com/v1/models', re.compile(r'\bsk-ant-[A-Za-z0-9_\-]{20,}\b')),
    'gemini':    ('https://generativelanguage.googleapis.com/v1beta/models', re.compile(r'\bAIza[0-9A-Za-z_\-]{30,}\b')),
}

DATA = '/home/wang/hajimi-king-local/data'

def collect(p, rx):
    found = set()
    d = os.path.join(DATA, p)
    for f in glob.glob(d + '/**/*', recursive=True):
        if os.path.isfile(f):
            try:
                with open(f, encoding='utf-8', errors='ignore') as fh:
                    for m in rx.findall(fh.read()):
                        found.add(m)
            except Exception:
                pass
    return sorted(found)

def test(args):
    key, url, p = args
    try:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {key}'})
        with urllib.request.urlopen(req, timeout=15) as r:
            return p, key, r.status, 'OK'
    except urllib.error.HTTPError as e:
        return p, key, e.code, 'HTTP_ERR'
    except Exception as e:
        return p, key, -1, type(e).__name__

def main():
    pool = {}
    for p, (url, rx) in PROVIDERS.items():
        ks = collect(p, rx)
        pool[p] = ks
        print(f'{p}: 收集 {len(ks)} 个', flush=True)

    jobs = []
    for p, (url, rx) in PROVIDERS.items():
        for k in pool[p]:
            jobs.append((k, url, p))
    print(f'\n总计待测: {len(jobs)}', flush=True)

    good, bad = {}, {}
    with ThreadPoolExecutor(max_workers=16) as ex:
        for i, (p, key, code, st) in enumerate(ex.map(test, jobs)):
            if code == 200:
                good.setdefault(p, []).append(key)
            else:
                bad.setdefault(p, []).append((key, code))
            if (i + 1) % 200 == 0:
                print(f'  已测 {i+1}/{len(jobs)}  有效 {sum(len(v) for v in good.values())}', flush=True)

    print('\n' + '=' * 50)
    print('体检结果')
    print('=' * 50)
    tg = 0
    for p in PROVIDERS:
        g = len(good.get(p, []))
        tg += g
        print(f'{p:12} 有效 {g:5} / {len(pool[p])}')
    print(f'{"合计":12} 有效 {tg}')

    out = '/home/wang/hajimi-king-local/verify_results/hajimi_healthcheck_20261010.json'
    json.dump({'good': good, 'bad_count': {k: len(v) for k, v in bad.items()}},
              open(out, 'w'), indent=2)
    print(f'\n已保存: {out}')

if __name__ == '__main__':
    main()
