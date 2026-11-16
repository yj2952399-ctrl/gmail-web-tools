# ==========================================================
#  Gmail 連絡送信システム / HF Spaces 版
#   - 接続保持ワーカー（25通/接続で張り替え）
#   - アカウント単位の同時接続制限 + 1日上限
#   - SSE によるログ配信
#   - 指数バックオフ / 認証失敗はアカウント離脱
# ==========================================================
import os
import re
import json
import time
import uuid
import queue
import random
import smtplib
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, date
from email.mime.text import MIMEText
from email.utils import formatdate, make_msgid

import requests
from flask import Flask, request, jsonify, session, Response

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET") or os.urandom(32).hex()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "1") == "1",
    PERMANENT_SESSION_LIFETIME=86400,
)

# ---------- チューニング定数 ----------
SENDS_PER_CONN   = int(os.environ.get("SENDS_PER_CONN", "25"))
ACCOUNT_CONCURRENCY = int(os.environ.get("ACCOUNT_CONCURRENCY", "3"))
DAILY_CAP        = int(os.environ.get("DAILY_CAP", "400"))
MAX_ACCOUNTS     = int(os.environ.get("MAX_ACCOUNTS", "200"))
SMTP_HOST        = "smtp.gmail.com"
SMTP_PORT        = 465
CONNECT_TIMEOUT  = 20
MAX_BACKOFF      = 20.0

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.after_request
def add_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


lock = threading.Lock()
session_data = {}

# ---------- セッション ----------
def ensure_session(sid):
    with lock:
        sdata = session_data.get(sid)
        if sdata is None:
            sdata = {
                "logs": [],
                "subs": set(),
                "running": False,
                "stop": threading.Event(),
                "stats": {"sent": 0, "success": 0, "failed": 0},
                "daily": {},
                "daily_date": date.today().isoformat(),
                "last_seen": time.time(),
            }
            session_data[sid] = sdata
        sdata["last_seen"] = time.time()
        return sdata


def get_sid():
    if "sid" not in session:
        session.permanent = True
        session["sid"] = uuid.uuid4().hex[:12]
    return session["sid"]


def janitor():
    while True:
        time.sleep(600)
        now = time.time()
        with lock:
            for sid, s in list(session_data.items()):
                if not s["running"] and now - s["last_seen"] > 3600:
                    session_data.pop(sid, None)


threading.Thread(target=janitor, daemon=True).start()


# ---------- 死活監視（HF では不要だが残す） ----------
def keep_connection_alive():
    url = os.environ.get("APP_URL")
    if not url:
        return
    url = url.rstrip("/")
    while True:
        try:
            requests.get(url + "/healthz", timeout=8)
        except Exception:
            pass
        time.sleep(180)


threading.Thread(target=keep_connection_alive, daemon=True).start()


# ---------- ログ ----------
def add_log(sid, text, level="info"):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {text}"
    with lock:
        sdata = session_data.get(sid)
        if not sdata:
            return
        sdata["logs"].append(line)
        if len(sdata["logs"]) > 500:
            del sdata["logs"][:100]
        subs = list(sdata["subs"])
    for q in subs:
        try:
            q.put_nowait({"type": "log", "level": level, "text": line})
        except queue.Full:
            pass
    print(line, flush=True)


def push_stat(sid):
    with lock:
        sdata = session_data.get(sid)
        if not sdata:
            return
        stats = dict(sdata["stats"])
        subs = list(sdata["subs"])
    for q in subs:
        try:
            q.put_nowait({"type": "stat", **stats})
        except queue.Full:
            pass


# ---------- 入力解析 ----------
def parse_account_list(text):
    accounts, seen = [], set()
    for item in re.split(r"[,\n\r\t ]+", (text or "").strip()):
        item = item.strip()
        if not item or ":" not in item:
            continue
        addr, pw = item.split(":", 1)
        addr, pw = addr.strip(), pw.strip().replace(" ", "")
        if not EMAIL_RE.match(addr) or not pw or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        accounts.append({"address": addr, "password": pw})
        if len(accounts) >= MAX_ACCOUNTS:
            break
    return accounts


BASE_SUBJECTS = [
    "ちょっと話がある", "正直な気持ち", "どうかしてるの？", "一回考えてみて",
    "それでいいの？", "人としてどうかと", "結果が全てだよ", "誰も認めてない",
    "自分を見つめ直せ", "最後に言っておく",
]

MESSAGE_PATTERNS = [
    """おーい、生きてるー？？ お前みたいな役立たずが何を頑張っても無駄だって早く気づけよ😂
一生その狭い頭で妄想ばっかり繰り返して、現実では誰にも相手にされてないの気づいてる？🤣
人間としての価値すら怪しいレベルで生きてて恥ずかしくないの？ まじで消えた方がいいよwww""",

    """ねえ、自分が何様だと思ってるの？ ただのカスみたいな人生送ってるくせに偉そうにするなよ🤏
周りの全員がお前のことを心底見下してるの、そろそろ気づけよ無能😂
お前が存在するだけで周りが迷惑してるって、親に教えてもらわなかったの？""",

    """まともな反論もできないで逃げ回ってるだけのゴミが何言っても無駄だってww
お前の存在そのものがゴミ以下なんだよ？ 生まれてきたことが最大の過ちレベル🤣
さっさと消えて、どうぞ。誰も探さないから安心して😂""",

    """そのしょうもない脳みそで少しは考えてみろよ。あ、無理かw お前には難しいよな🤣
誰もお前のことなんて認めてないし、誰もお前に興味なんてないの。ただの哀れな負け犬😂
一生そうやって誰かの陰で震えて生きていけ。お前にはそれがお似合いだよ""",

    """お前さ、自分が何をやっても中途半端で終わるの、なんでかわかる？ 頭も悪いし根性もないし、何一つまともに続かないからだよ😂
それでいて偉そうなんだから笑えるよね。まじで生きてる価値ある？ よく考えてみろよwww""",

    """見てるとイライラするんだよね。何もできないくせに態度だけは一人前で、全部人のせいにして、自分は悪くないと思ってる。
そうやって甘えてるから一生成長しないんだよ。お前が今置かれてる状況は全部お前自身のせいだからな😂""",

    """可哀想だね〜 何をやってもうまくいかなくて、誰からも相手にされなくて、一人で寂しくないの？😂
あ、それがお前の平常運転だったね！ 悪いな、悪いなw でも事実だから仕方ないよね🤣
誰もお前の味方なんていはいよ。孤独な負け犬、おつかれさま〜""",

    """言っておくけど、お前がどれだけ頑張ったところで、結果なんて見えてるんだよ。
だって根本的に「能力がない」んだから。それを認めたくなくて喚いてるだけ。
そうやって現実逃避してる間にも、周りはどんどん先に行く。お前だけがいつまでもそこに立ち止まってる😂""",

    """お前の発言って全部が全部的外れで、聞いてるこっちが恥ずかしくなるんだよね😂
「こいつ本気で言ってるの？」って。周りの人たち内心全部笑ってるよ？ お前のこと。
それにいつ気づくの？ 一生気づかないまま死んでいくのがお前らしいけどwww""",

    """最後に言っておくけど、お前がどれだけ足掻いても、何も変わらないよ。
だってお前自身が変わる気が無いんだから。いつも誰かのせい、環境のせい。
そうやって一生言い訳して生きていくんだな。それがお前の選んだ道だ。好きにすればいい😂""",
]


def make_unique_subject(base, seq):
    rand = "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=4))
    return f"{base} 【{seq}-{rand}】"


# ---------- 送信 ----------
def _connect(acc):
    s = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=CONNECT_TIMEOUT)
    s.ehlo()
    s.login(acc["address"], acc["password"])
    return s


def _reserve_slot(ctx, interval):
    if interval <= 0:
        return
    with ctx["lock"]:
        now = time.monotonic()
        wait = max(0.0, ctx["next_at"] - now)
        ctx["next_at"] = max(now, ctx["next_at"]) + interval
    if wait:
        time.sleep(wait)


def _take_job(ctx):
    with ctx["lock"]:
        if ctx["stop"].is_set():
            return None
        if ctx["max_count"] and ctx["sent"] >= ctx["max_count"]:
            return None
        ctx["sent"] += 1
        return ctx["sent"]


def _bump(ctx, key):
    with ctx["lock"]:
        ctx[key] += 1


def account_worker(sid, acc, ctx):
    addr = acc["address"]
    conn, used, backoff = None, 0, 1.0
    try:
        while True:
            # 1日上限チェック
            with ctx["lock"]:
                if ctx["daily"].get(addr, 0) >= DAILY_CAP:
                    add_log(sid, f"上限到達のため離脱: {addr} ({DAILY_CAP}通)", "warn")
                    return
            num = _take_job(ctx)
            if num is None:
                return

            _reserve_slot(ctx, ctx["interval"])

            idx = random.randrange(len(MESSAGE_PATTERNS))
            body = MESSAGE_PATTERNS[idx]
            base = ctx["fixed_subject"] or BASE_SUBJECTS[idx]

            msg = MIMEText(f"【{num} 通目】\n\n{body}", "plain", "utf-8")
            msg["From"] = addr
            msg["To"] = ctx["dest"]
            msg["Subject"] = make_unique_subject(base, num)
            msg["Date"] = formatdate(localtime=True)
            msg["Message-ID"] = make_msgid(domain=addr.split("@")[-1])
            msg["X-Entity-Ref-ID"] = uuid.uuid4().hex
            msg["Precedence"] = "bulk"
            msg["Auto-Submitted"] = "auto-generated"

            try:
                if conn is None:
                    conn = _connect(acc)
                    used = 0
                conn.send_message(msg)
                used += 1
                backoff = 1.0
                with ctx["lock"]:
                    ctx["daily"][addr] = ctx["daily"].get(addr, 0) + 1
                _bump(ctx, "success")
                add_log(sid, f"OK {num:>5} 通目 {addr}")
                if used >= SENDS_PER_CONN:
                    try:
                        conn.quit()
                    except Exception:
                        pass
                    conn = None
                push_stat(sid)

            except smtplib.SMTPAuthenticationError as e:
                _bump(ctx, "failed")
                add_log(sid, f"NG {num:>5} 認証失敗 {addr} → 離脱 ({e.smtp_code})", "error")
                with ctx["lock"]:
                    ctx["accounts"].discard(addr)
                push_stat(sid)
                return

            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                    OSError, TimeoutError) as e:
                try:
                    conn.quit()
                except Exception:
                    pass
                conn = None
                _bump(ctx, "failed")
                add_log(sid, f"NG {num:>5} 切断 {str(e)[:40]} → {backoff:.0f}s後再接続", "warn")
                push_stat(sid)
                time.sleep(backoff + random.random())
                backoff = min(backoff * 2, MAX_BACKOFF)

            except smtplib.SMTPResponseException as e:
                _bump(ctx, "failed")
                if e.smtp_code in (421, 429, 454):
                    add_log(sid, f"NG {num:>5} レート制限 {e.smtp_code} → {backoff:.0f}s待機", "warn")
                    try:
                        conn.quit()
                    except Exception:
                        pass
                    conn = None
                    time.sleep(backoff + random.random())
                    backoff = min(backoff * 2, MAX_BACKOFF)
                else:
                    add_log(sid, f"NG {num:>5} SMTP {e.smtp_code} {str(e.smtp_error)[:40]}", "error")
                push_stat(sid)

            except Exception as e:
                _bump(ctx, "failed")
                add_log(sid, f"NG {num:>5} {str(e)[:50]}", "error")
                push_stat(sid)

    finally:
        if conn:
            try:
                conn.quit()
            except Exception:
                pass


def delivery_worker(sid, accounts, dest, interval, max_count, fixed_subject):
    sdata = ensure_session(sid)
    ctx = {
        "lock": threading.Lock(),
        "stop": sdata["stop"],
        "sent": 0, "success": 0, "failed": 0,
        "interval": interval, "max_count": max_count,
        "next_at": 0.0, "dest": dest, "fixed_subject": fixed_subject,
        "accounts": {a["address"] for a in accounts},
        "daily": {},
    }
    sdata["running"] = True

    try:
        total = "無制限" if max_count == 0 else f"{max_count}通"
        add_log(sid, f"アカウント {len(accounts)}件 / 送信先 {dest} / 上限 {total}")
        add_log(sid, f"間隔 {interval}s / 接続保持 {SENDS_PER_CONN}通 / 並列 {ACCOUNT_CONCURRENCY}×アカウント")
        add_log(sid, f"1日上限 {DAILY_CAP}通/アカウント")
        if fixed_subject:
            add_log(sid, f"固定件名: {fixed_subject}（識別子付与）")

        threads = []
        for acc in accounts:
            for _ in range(ACCOUNT_CONCURRENCY):
                t = threading.Thread(target=account_worker, args=(sid, acc, ctx), daemon=True)
                t.start()
                threads.append(t)
        for t in threads:
            t.join()

    except Exception as e:
        add_log(sid, f"実行エラー: {e}", "error")
    finally:
        sdata["running"] = False
        with lock:
            stats = dict(sdata["stats"])
        add_log(sid, f"処理終了 合計:{ctx['sent']} 成功:{ctx['success']} 失敗:{ctx['failed']}")
        with lock:
            subs = list(sdata["subs"])
        for q in subs:
            try:
                q.put_nowait({"type": "done", **ctx["stop"].is_set() and {"stopped": True} or {}})
            except queue.Full:
                pass


# ---------- 画面 ----------
PAGE_HTML = r"""
<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Gmail 連絡送信システム</title>
<style>
  *{box-sizing:border-box;font-family:system-ui,sans-serif;margin:0;padding:0}
  body{background:#121212;color:#e0e0e0;padding:20px;max-width:820px;margin:0 auto}
  h1{text-align:center;color:#64b5f6;font-size:20px;margin-bottom:18px}
  .counter{text-align:center;font-size:22px;font-weight:700;color:#81c784;margin-bottom:8px}
  .sub{text-align:center;font-size:12px;color:#888;margin-bottom:18px}
  .card{background:#1e1e1e;padding:18px;border-radius:8px;margin-bottom:14px;border:1px solid #333}
  label{display:block;margin:12px 0 4px;font-weight:600;color:#ccc;font-size:13px}
  textarea,input{width:100%;padding:10px;border:1px solid #444;border-radius:4px;background:#2a2a2a;color:#fff;font-size:14px}
  textarea{height:96px;line-height:1.5;resize:vertical;font-family:monospace}
  input:focus,textarea:focus{outline:none;border-color:#64b5f6}
  .note{font-size:11px;color:#8a8a8a;margin-top:6px;line-height:1.6}
  .row{display:flex;gap:10px}
  .row>div{flex:1}
  .btn-area{display:flex;gap:10px;margin-top:18px}
  button{flex:1;padding:12px;font-size:15px;font-weight:700;border:none;border-radius:4px;cursor:pointer}
  button:disabled{opacity:.4;cursor:not-allowed}
  .start{background:#2e7d32;color:#fff}
  .stop{background:#c62828;color:#fff}
  .test{background:#37474f;color:#fff;flex:0 0 120px;font-size:13px;padding:10px}
  pre{background:#0a0a0a;padding:12px;border-radius:4px;border:1px solid #222;white-space:pre-wrap;word-break:break-all;height:320px;overflow-y:auto;font-family:monospace;font-size:12px;margin-top:8px}
  .err{color:#ef5350}.warn{color:#ffb74d}.ok{color:#a5d6a7}
  #dot{display:inline-block;width:8px;height:8px;border-radius:50%;background:#555;margin-right:6px}
  #dot.on{background:#43a047}
</style>
</head>
<body>
<h1>Gmail 連絡送信システム</h1>
<div class="counter" id="counter">送信済み: 0 / 成功 0 / 失敗 0</div>
<div class="sub"><span id="dot"></span><span id="state">待機中</span></div>

<div class="card">
  <label>送信元 Gmail アカウント（1行1件）</label>
  <textarea id="accounts" placeholder="mail1@gmail.com:アプリパスワード&#10;mail2@gmail.com:アプリパスワード"></textarea>
  <div class="note">形式: アドレス:アプリパスワード / 改行・カンマ・スペース区切り / 重複は自動排除</div>

  <label>送信先メールアドレス</label>
  <input type="email" id="dest" placeholder="xxx@gmail.com">

  <div class="row">
    <div>
      <label>送信間隔（秒） 0=待機なし</label>
      <input type="number" id="interval" value="0" min="0" step="0.05">
    </div>
    <div>
      <label>送信回数（0=無制限）</label>
      <input type="number" id="count" value="0" min="0" step="1">
    </div>
  </div>

  <label>件名（空欄=自動）</label>
  <input type="text" id="subj" placeholder="任意の件名 または 空欄">
  <div class="note">末尾に識別子を付与しスレッド統合を回避 / 接続保持 / 自動再接続 ON</div>

  <div class="btn-area">
    <button class="test" id="btn_test" onclick="testAccounts()">疎通確認</button>
    <button class="start" id="btn_start" onclick="startSend()">送信開始</button>
    <button class="stop" id="btn_stop" onclick="stopSend()" disabled>停止</button>
  </div>
</div>

<div class="card">
  <h3 style="font-size:14px;color:#bbb">実行ログ</h3>
  <pre id="log">準備完了。</pre>
</div>

<script>
const $ = id => document.getElementById(id);
let es = null;

function setState(text, on){
  $('state').textContent = text;
  $('dot').classList.toggle('on', !!on);
}
function appendLog(level, text){
  const span = document.createElement('span');
  span.className = level === 'error' ? 'err' : level === 'warn' ? 'warn' : '';
  span.textContent = text + '\n';
  $('log').appendChild(span);
  $('log').scrollTop = $('log').scrollHeight;
}
function openStream(){
  if (es) return;
  es = new EventSource('/stream');
  es.onmessage = ev => {
    const d = JSON.parse(ev.data);
    if (d.type === 'log') appendLog(d.level, d.text);
    else if (d.type === 'stat')
      $('counter').textContent = `送信済み: ${d.sent} / 成功 ${d.success} / 失敗 ${d.failed}`;
    else if (d.type === 'done') finish(d.stopped);
  };
  es.onerror = () => { /* EventSource は自動再接続する */ };
}
function closeStream(){ if (es) { es.close(); es = null; } }

function finish(stopped){
  $('btn_start').disabled = false;
  $('btn_stop').disabled = true;
  setState(stopped ? '停止しました' : '完了', false);
  closeStream();
}

async function startSend(){
  $('btn_start').disabled = true;
  const res = await fetch('/start', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      accounts: $('accounts').value,
      destination: $('dest').value.trim(),
      interval: parseFloat($('interval').value) || 0,
      max_count: parseInt($('count').value) || 0,
      subject: $('subj').value.trim()
    })
  });
  const d = await res.json();
  if (!d.ok){
    alert(d.msg || 'エラー');
    $('btn_start').disabled = false;
    return;
  }
  $('log').textContent = '';
  $('counter').textContent = '送信済み: 0 / 成功 0 / 失敗 0';
  $('btn_stop').disabled = false;
  setState('送信中', true);
  openStream();
}

async function stopSend(){
  if (!confirm('停止しますか？')) return;
  await fetch('/stop', {method:'POST'});
  setState('停止処理中', true);
}

async function testAccounts(){
  const text = $('accounts').value;
  if (!text.trim()) { alert('アカウントを入力してください'); return; }
  $('btn_test').disabled = true;
  $('log').textContent = '';
  const res = await fetch('/test', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({accounts: text})
  });
  const d = await res.json();
  if (d.ok){
    openStream();
  } else {
    alert(d.msg || 'エラー');
  }
  $('btn_test').disabled = false;
}
</script>
</body>
</html>
"""


# ---------- ルート ----------
@app.route("/")
def idx():
    sid = get_sid()
    ensure_session(sid)
    return PAGE_HTML


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "sessions": len(session_data)})


@app.route("/start", methods=["POST"])
def api_start():
    sid = get_sid()
    sdata = ensure_session(sid)

    if sdata.get("running"):
        return jsonify({"ok": False, "msg": "既に実行中です。停止してから再実行してください。"})

    data = request.get_json(silent=True) or {}
    accounts = parse_account_list(data.get("accounts", ""))
    if not accounts:
        return jsonify({"ok": False, "msg": "有効なアカウントがありません（アドレス:アプリパスワード）"})

    destination = (data.get("destination") or "").strip()
    if not EMAIL_RE.match(destination):
        return jsonify({"ok": False, "msg": "送信先の形式が不正です。"})

    try:
        interval = max(0.0, float(data.get("interval", 0)))
        max_count = max(0, int(data.get("max_count", 0)))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "msg": "間隔/回数は数値で指定してください。"})

    subject = (data.get("subject") or "").strip()[:120]

    with lock:
        sdata["logs"].clear()
        sdata["stats"] = {"sent": 0, "success": 0, "failed": 0}
        sdata["daily"] = {}
        sdata["daily_date"] = date.today().isoformat()
        sdata["stop"].clear()
        sdata["running"] = True

    threading.Thread(
        target=delivery_worker,
        args=(sid, accounts, destination, interval, max_count, subject),
        daemon=True,
    ).start()

    return jsonify({"ok": True})


@app.route("/stop", methods=["POST"])
def api_stop():
    sid = get_sid()
    sdata = ensure_session(sid)
    with lock:
        sdata["stop"].set()
    return jsonify({"ok": True})


@app.route("/test", methods=["POST"])
def api_test():
    sid = get_sid()
    sdata = ensure_session(sid)
    if sdata.get("running"):
        return jsonify({"ok": False, "msg": "実行中は疎通確認できません。"})

    data = request.get_json(silent=True) or {}
    accounts = parse_account_list(data.get("accounts", ""))
    if not accounts:
        return jsonify({"ok": False, "msg": "有効なアカウントがありません。"})

    with lock:
        sdata["logs"].clear()

    def probe(acc):
        try:
            s = _connect(acc)
            try:
                s.noop()
            finally:
                s.quit()
            add_log(sid, f"OK 疎通 {acc['address']}", "ok")
        except smtplib.SMTPAuthenticationError:
            add_log(sid, f"NG 認証失敗 {acc['address']}（アプリパスワード/2段階認証を確認）", "error")
        except Exception as e:
            add_log(sid, f"NG 接続不可 {acc['address']} {str(e)[:50]}", "error")

    threading.Thread(
        target=lambda: [probe(a) for a in accounts],
        daemon=True,
    ).start()

    return jsonify({"ok": True, "count": len(accounts)})


@app.route("/status")
def api_status():
    sid = get_sid()
    sdata = ensure_session(sid)
    with lock:
        return jsonify({
            "running": sdata["running"],
            "stats": sdata["stats"],
            "logs": sdata["logs"][-200:],
        })


@app.route("/stream")
def api_stream():
    sid = get_sid()
    sdata = ensure_session(sid)
    q = queue.Queue(maxsize=1000)

    with lock:
        sdata["subs"].add(q)
        backlog = sdata["logs"][-200:]
        stats = dict(sdata["stats"])

    def gen():
        try:
            yield f"data: {json.dumps({'type':'stat', **stats})}\n\n"
            for line in backlog:
                yield f"data: {json.dumps({'type':'log','level':'info','text':line})}\n\n"

            while True:
                try:
                    ev = q.get(timeout=15)
                except queue.Empty:
                    yield ": ping\n\n"
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                if ev.get("type") == "done":
                    break
        finally:
            with lock:
                sdata["subs"].discard(q)

    return Response(
        gen(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))