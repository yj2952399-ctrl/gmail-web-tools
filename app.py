# ==========================================================
#  Gmail 連絡送信システム
#  「Connection closed」 → 自動再接続 追加版
#  ※ 送信速度・並列数は一切変更なし
# ==========================================================
from flask import Flask, render_template_string, request, jsonify, session
import smtplib
import random
import threading
import time
import os
import re
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

app = Flask(__name__)
app.secret_key = os.environ.get("SESSION_SECRET") or os.urandom(32).hex()

@app.after_request
def add_headers(response):
    response.headers["Server"] = "Flask"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    return response

session_data = {}
lock = threading.Lock()

# ========== 死活監視 ==========
def keep_connection_alive():
    url = os.getenv("REPLIT_APP_URL") or os.getenv("REPLIT_URL")
    if not url:
        domain = os.getenv("REPLIT_DOMAINS", "").split(",")[0].strip()
        url = domain if domain.startswith(("http://", "https://")) else f"https://{domain}" if domain else "http://127.0.0.1:8080"
    url = url.rstrip("/")
    while True:
        try:
            requests.get(url, timeout=8)
        except Exception:
            pass
        time.sleep(180)

threading.Thread(target=keep_connection_alive, daemon=True).start()

# ========== ✅ セッション安全取得 ==========
def ensure_session(sid):
    with lock:
        if sid not in session_data:
            session_data[sid] = {
                "logs": [],
                "running": False,
                "stop": threading.Event()
            }
        return session_data[sid]

# ========== アカウント解析 ==========
def parse_account_list(text):
    text = text.strip()
    if not text:
        return []
    items = re.split(r'[,\n\s]+', text)
    accounts = []
    for item in items:
        item = item.strip()
        if ":" in item:
            addr, pw = item.split(":", 1)
            accounts.append({"address": addr.strip(), "password": pw.strip()})
    return accounts

# ========== 件名パターン ==========
BASE_SUBJECTS = [
    "ちょっと話がある",
    "正直な気持ち",
    "どうかしてるの？",
    "一回考えてみて",
    "それでいいの？",
    "人としてどうかと",
    "結果が全てだよ",
    "誰も認めてない",
    "自分を見つめ直せ",
    "最後に言っておく"
]

def make_unique_subject(base_title, seq):
    rand_str = ''.join(random.choices('ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789', k=4))
    return f"{base_title} 【{seq}-{rand_str}】"

# ========== 本文 一切変更なし ==========
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
誰もお前の味方なんていないよ。孤独な負け犬、おつかれさま〜""",

    """言っておくけど、お前がどれだけ頑張ったところで、結果なんて見えてるんだよ。
だって根本的に「能力がない」んだから。それを認めたくなくて喚いてるだけ。
そうやって現実逃避してる間にも、周りはどんどん先に行く。お前だけがいつまでもそこに立ち止まってる😂""",

    """お前の発言って全部が全部的外れで、聞いてるこっちが恥ずかしくなるんだよね😂
「こいつ本気で言ってるの？」って。周りの人たち内心全部笑ってるよ？ お前のこと。
それにいつ気づくの？ 一生気づかないまま死んでいくのがお前らしいけどwww""",

    """最後に言っておくけど、お前がどれだけ足掻いても、何も変わらないよ。
だってお前自身が変わる気がないんだから。いつも誰かのせい、環境のせい。
そうやって一生言い訳して生きていくんだな。それがお前の選んだ道だ。好きにすればいい😂"""
]

def get_sid():
    if "sid" not in session:
        session["sid"] = str(uuid.uuid4())[:8]
    return session["sid"]

def add_log(sid, text):
    t = datetime.now().strftime("%H:%M:%S")
    line = f"[{t}] {text}"
    with lock:
        sdata = session_data.get(sid)
        if sdata:
            sdata["logs"].append(line)
            if len(sdata["logs"]) > 300:
                sdata["logs"].pop(0)
    print(line)

# ========== ✅ 自動再接続・リトライ機能 追加 ==========
def send_one(acc, dest, subj, body, num, max_retries=3):
    """切断されても自動で再接続して再送 最大3回まで"""
    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            msg = MIMEMultipart()
            msg["From"] = acc["address"]
            msg["To"] = dest
            msg["Subject"] = subj
            msg.attach(MIMEText(f"【{num} 通目】\n\n{body}", "plain", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(acc["address"], acc["password"])
                server.send_message(msg)

            if attempt > 1:
                return True, f"OK {num:>4} 通目 再接続成功({attempt}回目) {acc['address']}"
            return True, f"OK {num:>4} 通目 {acc['address']}"

        except Exception as e:
            last_error = str(e)[:60]
            if attempt < max_retries:
                # 切断エラーの場合 → 少し待って自動的に再接続
                time.sleep(0.5)
                continue
            # 3回失敗 → 諦めてNG
            return False, f"NG {num:>4} 通目 失敗:{last_error}"

# ========== 一斉送信メイン 【設定は元のまま】 ==========
def delivery_worker(sid, accounts, dest, interval, max_count, fixed_subject):
    sent = 0
    success = 0
    failed = 0
    sdata = ensure_session(sid)
    stop_flag = sdata["stop"]
    NUM = len(accounts)
    BATCH_SIZE = 100      # ✅ 元のまま
    MAX_WORKERS = 100     # ✅ 元のまま

    sdata["running"] = True

    try:
        total_label = "無制限" if max_count == 0 else f"{max_count} 通"
        add_log(sid, f"アカウント: {NUM} 件  送信先: {dest}  上限: {total_label}")
        add_log(sid, f"送信間隔: {interval}秒  同時実行: {MAX_WORKERS}")
        add_log(sid, f"自動再接続: ON（最大3回リトライ）")
        if fixed_subject:
            add_log(sid, f"固定件名: {fixed_subject}（末尾に識別子を付与）")

        with ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="sender") as executor:
            batch = []
            while not stop_flag.is_set():
                for _ in range(BATCH_SIZE):
                    if stop_flag.is_set():
                        break
                    if max_count > 0 and sent >= max_count:
                        break
                    sent += 1
                    acc = accounts[(sent - 1) % NUM]
                    idx = random.randrange(len(MESSAGE_PATTERNS))
                    body = MESSAGE_PATTERNS[idx]

                    if fixed_subject:
                        base = fixed_subject
                    else:
                        base = BASE_SUBJECTS[idx]
                    unique_subj = make_unique_subject(base, sent)

                    batch.append(executor.submit(send_one, acc, dest, unique_subj, body, sent))

                for future in as_completed(batch):
                    ok, msg = future.result()
                    add_log(sid, msg)
                    if ok:
                        success += 1
                    else:
                        failed += 1
                batch.clear()

                if max_count > 0 and sent >= max_count:
                    break
                if interval > 0:
                    time.sleep(interval)

    except Exception as e:
        add_log(sid, f"実行エラー: {str(e)}")
    finally:
        sdata["running"] = False
        add_log(sid, f"処理終了  合計:{sent}  成功:{success}  失敗:{failed}")

# ========== 画面 ==========
PAGE_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gmail 連絡送信システム</title>
    <style>
        * { box-sizing: border-box; font-family: sans-serif; margin: 0; padding: 0; }
        body { background: #121212; color: #e0e0e0; padding: 20px; max-width: 800px; margin: 0 auto; }
        h1 { text-align: center; color: #64b5f6; font-size: 20px; margin-bottom: 20px; }
        .counter { text-align: center; font-size: 22px; font-weight: bold; color: #81c784; margin-bottom: 20px; }
        .card { background: #1e1e1e; padding: 20px; border-radius: 8px; margin-bottom: 15px; border: 1px solid #333; }
        label { display: block; margin: 12px 0 4px; font-weight: bold; color: #ccc; font-size: 14px; }
        textarea, input { width: 100%; padding: 10px; border: 1px solid #444; border-radius: 4px; background: #2a2a2a; color: #fff; font-size: 14px; }
        textarea { height: 100px; line-height: 1.5; resize: vertical; }
        input:focus, textarea:focus { outline: none; border-color: #64b5f6; }
        .note { font-size: 12px; color: #999; margin-top: 6px; line-height: 1.5; }
        .btn-area { display: flex; gap: 12px; margin-top: 20px; }
        button { flex: 1; padding: 12px; font-size: 16px; font-weight: bold; border: none; border-radius: 4px; cursor: pointer; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        .start { background: #2e7d32; color: #fff; }
        .stop { background: #c62828; color: #fff; }
        pre { background: #0a0a0a; padding: 12px; border-radius: 4px; border: 1px solid #222; white-space: pre-wrap; height: 300px; overflow-y: auto; font-family: monospace; font-size: 12px; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>Gmail 連絡送信システム</h1>
    <div class="counter" id="counter">送信済み: 0 件</div>
    <div class="card">
        <label>送信元Gmailアカウント</label>
        <textarea id="accounts" placeholder="メールアドレス:アプリパスワード"></textarea>
        <div class="note">複数可：改行/カンマ/スペース区切り</div>
        <label>送信先メールアドレス</label>
        <input type="email" id="dest" placeholder="xxx@gmail.com">
        <label>送信間隔（秒） 0=待機なし</label>
        <input type="number" id="interval" value="0" min="0" step="0.1">
        <label>送信回数（0=無制限）</label>
        <input type="number" id="count" value="0" min="0" step="1">
        <label>件名（空欄=自動）</label>
        <input type="text" id="subj" placeholder="任意の件名 または 空欄">
        <div class="note">※ 件名末尾に識別子を付与しスレッド統合を回避 / 自動再接続:ON</div>
        <div class="btn-area">
            <button class="start" id="btn_start" onclick="startSend()">送信開始</button>
            <button class="stop" id="btn_stop" onclick="stopSend()" disabled>停止</button>
        </div>
    </div>
    <div class="card">
        <h3>実行ログ</h3>
        <pre id="log">準備完了。送信開始を押してください。</pre>
    </div>
    <script>
        let isRunning = false;
        async function startSend() {
            const res = await fetch("/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    accounts: accounts.value,
                    destination: dest.value.trim(),
                    interval: parseFloat(interval.value)||0,
                    max_count: parseInt(count.value)||0,
                    subject: subj.value.trim()
                })
            });
            const d = await res.json();
            if (d.ok) { isRunning=true; btn_start.disabled=true; btn_stop.disabled=false; }
            else alert(d.msg||"エラー");
        }
        async function stopSend() {
            if(!confirm("停止しますか？"))return;
            await fetch("/stop",{method:"POST"});
            isRunning=false; btn_start.disabled=false; btn_stop.disabled=true;
        }
        async function updateLog() {
            const t = await (await fetch("/log")).text();
            log.textContent=t; log.scrollTop=log.scrollHeight;
            const m=t.match(/(\\d+) 通目/);
            if(m) counter.textContent="送信済み: "+m[1]+" 件";
        }
        setInterval(updateLog,500);
    </script>
</body>
</html>
"""

# ========== API ==========
@app.route("/")
def idx():
    sid = get_sid()
    ensure_session(sid)
    return render_template_string(PAGE_HTML)

@app.route("/start", methods=["POST"])
def api_start():
    sid = get_sid()
    sdata = ensure_session(sid)

    if sdata.get("running", False):
        return jsonify({"ok": False, "msg": "既に実行中です。停止してから再実行してください。"})

    data = request.get_json() or {}
    accounts = parse_account_list(data.get("accounts", ""))
    if not accounts:
        return jsonify({"ok": False, "msg": "アカウントを入力してください。"})

    destination = data.get("destination", "").strip()
    if not destination:
        return jsonify({"ok": False, "msg": "送信先を入力してください。"})

    interval = float(data.get("interval", 0))
    max_count = int(data.get("max_count", 0))
    subject = data.get("subject", "").strip()

    with lock:
        sdata["logs"] = []
        sdata["stop"].clear()
        sdata["running"] = False

    threading.Thread(
        target=delivery_worker,
        args=(sid, accounts, destination, interval, max_count, subject),
        daemon=True
    ).start()

    return jsonify({"ok": True})

@app.route("/stop", methods=["POST"])
def api_stop():
    sid = get_sid()
    sdata = ensure_session(sid)
    with lock:
        sdata["stop"].set()
    return jsonify({"ok": True})

@app.route("/log")
def api_log():
    sid = get_sid()
    sdata = ensure_session(sid)
    with lock:
        return "\n".join(sdata["logs"]) if sdata.get("logs") else "準備完了。送信開始を押してください。"

if __name__ == "__main__":
    print("=== Gmail 連絡送信システム 起動 ===")
    print("✅ 自動再接続機能追加（最大3回リトライ）")
    print("⚠  送信速度・並列数は元の設定のまま変更なし")
    app.run(host="0.0.0.0", port=8080)