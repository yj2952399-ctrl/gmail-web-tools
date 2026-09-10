from flask import Flask, render_template_string, request, jsonify, session
import smtplib
import random
import threading
import time
import os
import re
import uuid
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests

app = Flask(__name__)
app.secret_key = os.urandom(32).hex()

user_sessions = {}
lock = threading.Lock()

# ========== 🔄 スリープ防止：自分自身に定期アクセス ==========
def keep_alive():
    url = os.getenv("REPLIT_URL", "")
    if not url:
        try:
            url = f"https://{os.environ['REPL_SLUG']}.{os.environ['REPL_OWNER']}.repl.co"
        except:
            url = ""
    print(f"🔄 キープアライブURL: {url}")
    while True:
        try:
            if url:
                requests.get(url, timeout=10)
                print(f"[{datetime.now().strftime('%H:%M:%S')}] 🔄 キープアライブ実行 → スリープ防止")
        except Exception as e:
            print(f"キープアライブエラー: {e}")
        time.sleep(180)  # 3分ごとに実行

# ========== アカウント解析 ==========
def parse_accounts(text):
    text = text.strip()
    if not text:
        return []
    parts = re.split(r'[,\n\s]+', text)
    accounts = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if ":" in p:
            addr, pw = p.split(":", 1)
            accounts.append({"address": addr.strip(), "password": pw.strip()})
    return accounts

# ========== 💥 スパム文 10パターン 強化版 ==========
SPAM_PATTERNS = [
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

# ========== ユーザーID取得 ==========
def get_user_id():
    if 'uid' not in session:
        session['uid'] = str(uuid.uuid4())[:8]
    return session['uid']

# ========== ログ出力 ==========
def add_user_log(uid, text):
    t = datetime.now().strftime("%H:%M:%S")
    line = f"[{t}] {text}"
    with lock:
        if uid in user_sessions:
            user_sessions[uid]['logs'].append(line)
            if len(user_sessions[uid]['logs']) > 200:
                user_sessions[uid]['logs'].pop(0)
    print(f"[{uid}] {text}")

# ========== メール送信本体 ==========
def send_email_thread(uid, accounts, to_address, interval, max_count, subject):
    sent_count = 0
    account_index = 0
    success_total = 0
    fail_total = 0
    stop_flag = user_sessions[uid]['stop_flag']

    user_sessions[uid]['is_running'] = True
    stop_flag.clear()

    try:
        max_txt = "無限" if max_count == 0 else f"{max_count} 通"
        add_user_log(uid, f"✅ アカウント数: {len(accounts)} 個")
        add_user_log(uid, f"✅ 送信開始 → {to_address}")
        add_user_log(uid, f"⏱ 送信間隔: {interval}秒 / 📤 送信回数: {max_txt}")
        add_user_log(uid, f"💬 メッセージパターン数: {len(SPAM_PATTERNS)}")

        while not stop_flag.is_set():
            sent_count += 1
            acc = accounts[account_index]
            account_index = (account_index + 1) % len(accounts)

            pattern = random.choice(SPAM_PATTERNS)
            full_body = f"【{sent_count} 通目】\n\n{pattern}"

            msg = MIMEMultipart()
            msg["From"] = acc["address"]
            msg["To"] = to_address
            msg["Subject"] = subject
            msg.attach(MIMEText(full_body, "plain", "utf-8"))

            try:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
                    server.login(acc["address"], acc["password"])
                    server.send_message(msg)
                add_user_log(uid, f"✅ {sent_count} 通目 [{acc['address']}] 送信成功")
                success_total += 1
            except Exception as e:
                err = str(e)
                add_user_log(uid, f"❌ {sent_count} 通目 失敗: {err[:100]}")
                fail_total += 1
                if "Authentication" in err or "Username and Password" in err:
                    add_user_log(uid, "⚠️ 認証エラー → アドレス/アプリパスワード確認")
                    break
                if stop_flag.is_set():
                    add_user_log(uid, "🛑 送信中に停止されました")
                    break
                stop_flag.wait(2)
                continue

            if max_count > 0 and sent_count >= max_count:
                add_user_log(uid, f"✅ ✅ 指定の {max_count} 通に到達 → 自動停止")
                break

            if not stop_flag.is_set():
                stop_flag.wait(interval)

    except Exception as e:
        add_user_log(uid, f"💥 エラー: {str(e)}")
    finally:
        user_sessions[uid]['is_running'] = False
        add_user_log(uid, f"🛑 === 終了 ===")
        add_user_log(uid, f"📊 合計:{sent_count} 成功:{success_total} 失敗:{fail_total}")

# ========== Web画面 ==========
INDEX_HTML = """
<!DOCTYPE html>
<html lang="ja">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Gmail スパムツール</title>
    <style>
        * { box-sizing: border-box; font-family: sans-serif; }
        body { background: #1a1a1a; color: #eee; padding: 20px; max-width: 900px; margin: 0 auto; }
        h1 { text-align: center; color: #ff4444; }
        .counter { text-align: center; font-size: 28px; font-weight: bold; color: #0f0; margin: 10px 0; }
        .card { background: #2d2d2d; padding: 20px; border-radius: 10px; margin-bottom: 15px; }
        label { display: block; margin: 12px 0 4px; font-weight: bold; }
        textarea { width: 100%; padding: 10px; border: none; border-radius: 5px; background: #3a3a3a; color: #fff; font-size: 13px; height: 130px; line-height: 1.6; }
        input { width: 100%; padding: 10px; border: none; border-radius: 5px; background: #3a3a3a; color: #fff; font-size: 14px; }
        input:focus, textarea:focus { outline: 2px solid #ff4444; }
        .hint { font-size: 12px; color: #aaa; margin-top: 6px; line-height: 1.5; }
        .btn-area { display: flex; gap: 12px; margin-top: 20px; }
        button { flex: 1; padding: 14px; font-size: 18px; font-weight: bold; border: none; border-radius: 8px; cursor: pointer; }
        .start { background: #00aa00; color: #fff; }
        .stop { background: #cc0000; color: #fff; }
        button:disabled { opacity: 0.4; cursor: not-allowed; }
        pre { background: #111; padding: 15px; border-radius: 8px; white-space: pre-wrap; height: 350px; overflow-y: auto; font-family: monospace; font-size: 12px; margin-top: 10px; }
    </style>
</head>
<body>
    <h1>📧 Gmail スパムツール</h1>
    <div class="counter" id="counter">📤 送信済み: 0 通</div>

    <div class="card">
        <label>🔑 Gmailアカウント</label>
        <textarea id="accounts" placeholder="例1: tarou@gmail.com:abcd efgh ijkl mnop
例2: hanako@gmail.com:1234 5678 90ab cdef
★ 書き方→【メールアドレス:アプリパスワード】
★ 半角コロン : で区切る！"></textarea>
        <div class="hint">
            ✅ 1行に1アカウント：メールアドレス:アプリパスワード<br>
            ✅ 複数は改行・カンマ・スペースで区切る<br>
            ⚠️ アプリパスワード(16文字)を使う！ 普通のパスワードは不可
        </div>

        <label>📥 送信先メールアドレス</label>
        <input type="email" id="to_address" placeholder="例: target@gmail.com">

        <label>⏱ 送信間隔（秒）※3秒以上推奨</label>
        <input type="number" id="interval" value="3" min="1" step="1">

        <label>📤 送信回数（0=無限）</label>
        <input type="number" id="max_count" value="0" min="0" step="1">

        <label>📝 メール件名</label>
        <input type="text" id="subject" value="【重要】お知らせ">

        <div class="btn-area">
            <button class="start" id="btn_start" onclick="startSpam()">🚀 送信開始</button>
            <button class="stop" id="btn_stop" onclick="stopSpam()" disabled>🛑 停止</button>
        </div>
    </div>

    <div class="card">
        <h3>📋 実行ログ（自分専用）</h3>
        <pre id="log_area">準備完了。「送信開始」を押してください。</pre>
    </div>

    <script>
        let isRunning = false;

        async function startSpam() {
            const data = {
                accounts: document.getElementById("accounts").value,
                to_address: document.getElementById("to_address").value.trim(),
                interval: parseFloat(document.getElementById("interval").value) || 3,
                max_count: parseInt(document.getElementById("max_count").value) || 0,
                subject: document.getElementById("subject").value.trim() || "【重要】お知らせ"
            };

            const res = await fetch("/start", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(data)
            });
            const json = await res.json();
            if (json.ok) {
                isRunning = true;
                document.getElementById("btn_start").disabled = true;
                document.getElementById("btn_stop").disabled = false;
            } else {
                alert(json.msg || "エラー");
            }
        }

        async function stopSpam() {
            if (!confirm("本当に停止しますか？")) return;
            await fetch("/stop", { method: "POST" });
            isRunning = false;
            document.getElementById("btn_start").disabled = false;
            document.getElementById("btn_stop").disabled = true;
        }

        async function updateLog() {
            const res = await fetch("/log");
            const text = await res.text();
            document.getElementById("log_area").textContent = text;
            document.getElementById("log_area").scrollTop = document.getElementById("log_area").scrollHeight;
            const m = text.match(/(\d+) 通目/);
            if (m) document.getElementById("counter").textContent = "📤 送信済み: " + m[1] + " 通";
        }

        setInterval(updateLog, 1000);
    </script>
</body>
</html>
"""

# ========== ルーティング ==========
@app.route("/")
def index():
    uid = get_user_id()
    with lock:
        if uid not in user_sessions:
            user_sessions[uid] = {"logs": [], "is_running": False, "stop_flag": threading.Event()}
    return render_template_string(INDEX_HTML)

@app.route("/start", methods=["POST"])
def start():
    uid = get_user_id()
    data = request.get_json()
    with lock:
        if user_sessions[uid]['is_running']:
            return jsonify({"ok": False, "msg": "実行中です"})
    accounts = parse_accounts(data.get("accounts", ""))
    if not accounts:
        return jsonify({"ok": False, "msg": "アカウント形式エラー。「アドレス:パスワード」で書いて"})
    to_address = data.get("to_address", "")
    if not to_address:
        return jsonify({"ok": False, "msg": "送信先アドレスを入力して"})
    interval = float(data.get("interval", 3))
    max_count = int(data.get("max_count", 0))
    subject = data.get("subject", "【重要】お知らせ")
    with lock:
        user_sessions[uid]['logs'] = []
        user_sessions[uid]['stop_flag'].clear()
    threading.Thread(target=send_email_thread, args=(uid, accounts, to_address, interval, max_count, subject), daemon=True).start()
    return jsonify({"ok": True, "accounts": len(accounts)})

@app.route("/stop", methods=["POST"])
def stop():
    uid = get_user_id()
    with lock:
        if uid in user_sessions:
            user_sessions[uid]['stop_flag'].set()
    return jsonify({"ok": True})

@app.route("/log")
def get_log():
    uid = get_user_id()
    with lock:
        if uid in user_sessions:
            return "\n".join(user_sessions[uid]['logs'])
    return "準備完了。「送信開始」を押してください。"

# ========== 起動時にキープアライブ開始 ==========
if __name__ == "__main__":
    print("🚀 Gmailスパムツール 起動完了")
    print("🔄 キープアライブ機能 → 有効（3分ごとに実行）")
    print("💬 メッセージパターン → 10種類")
    threading.Thread(target=keep_alive, daemon=True).start()
    app.run(host="0.0.0.0", port=8080)