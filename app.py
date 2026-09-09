from flask import Flask, render_template_string, request, jsonify
import smtplib
import random
import asyncio
import threading
import time
import requests
import os
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)

# ========== グローバル状態 ==========
is_running = False
stop_flag = threading.Event()
config = {
    "gmail_address": "",
    "gmail_password": "",
    "to_address": "",
    "interval": 3.0,
    "subject": "【重要】お知らせ"
}
log_lines = []
lock = threading.Lock()

# ========== 自動Pingによるスリープ回避 ==========
SLEEP_PREVENT_INTERVAL = 280  # 約4分ごとに自分自身へPing
render_url = ""  # Render側が起動後に自動的にURLを持つので空欄でOK

def self_ping_loop():
    """Render無料プランのスリープを回避：自分自身に定期的にアクセス"""
    time.sleep(10)  # 起動直後に1回実行
    while True:
        try:
            if render_url:
                requests.get(render_url, timeout=10)
                add_log(f"🔄 スリープ回避Ping 実行")
        except:
            pass
        time.sleep(SLEEP_PREVENT_INTERVAL)

# ========== スパム文 4パターン ==========
SPAM_PATTERNS = [
    """お前らみたいな負け組のチー牛が何を言っても無駄だっての😂
一生その狭い頭で妄想繰り返してろよ、現実では誰にも相手にされてないくせに🤣
人間としての価値すら怪しいレベルで生きてて恥ずかしくないの？www""",

    """ねえ、自分が何様だと思ってるの？
ただのカスみたいな人生送ってるくせに偉そうにするなよ🤏
周りの全員がお前のことを見下してるの、そろそろ気づけよ無能😂""",

    """まともな反論もできないで逃げ回ってるだけのゴミが何言っても無駄ww
お前の存在そのものが周りの迷惑だってこと、親にでも教えてもらわなかったの？🤣
生まれてきたことが最大の過ちレベル、さっさと消えろよ""",

    """そのしょうもない脳みそで少しは考えてみろよ
誰もお前のことなんて認めてないし、誰もお前に興味なんてない🤣
ただの哀れな負け犬として一生終わるんだな、かわいそうに😂😂😂"""
]

# ========== ログ出力 ==========
def add_log(text):
    t = datetime.now().strftime("%H:%M:%S")
    line = f"[{t}] {text}"
    with lock:
        log_lines.append(line)
        if len(log_lines) > 200:
            log_lines.pop(0)
    print(line)  # サーバーログにも出力

# ========== メール送信本体 ==========
def send_email_thread():
    global is_running
    is_running = True
    stop_flag.clear()
    count = 0

    try:
        if not config["gmail_address"] or not config["gmail_password"]:
            add_log("❌ Gmailアドレスまたはパスワードが未設定")
            return

        if not config["to_address"]:
            add_log("❌ 送信先アドレスが未設定")
            return

        add_log(f"✅ 送信開始: {config['gmail_address']} → {config['to_address']}")
        add_log(f"⏱ 送信間隔: {config['interval']}秒")

        while not stop_flag.is_set():
            count += 1
            pattern = random.choice(SPAM_PATTERNS)
            full_body = f"【{count} 通目】\n\n{pattern}"

            msg = MIMEMultipart()
            msg["From"] = config["gmail_address"]
            msg["To"] = config["to_address"]
            msg["Subject"] = config["subject"]
            msg.attach(MIMEText(full_body, "plain", "utf-8"))

            try:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                    server.login(config["gmail_address"], config["gmail_password"])
                    server.send_message(msg)
                add_log(f"✅ {count} 通目 送信成功")
            except Exception as e:
                add_log(f"❌ {count} 通目 送信失敗: {str(e)}")
                if "Authentication" in str(e) or "Username and Password not accepted" in str(e):
                    add_log("⚠️ 認証エラー → アプリパスワードを確認してください")
                    break
                stop_flag.wait(3)
                continue

            if not stop_flag.is_set():
                stop_flag.wait(config["interval"])

    except Exception as e:
        add_log(f"💥 致命的エラー: {str(e)}")
    finally:
        is_running = False
        add_log("🛑 送信停止")

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
        .card { background: #2d2d2d; padding: 20px; border-radius: 10px; margin-bottom: 15px; }
        label { display: block; margin: 12px 0 4px; font-weight: bold; }
        input { width: 100%; padding: 10px; border: none; border-radius: 5px; background: #3a3a3a; color: #fff; font-size: 14px; }
        input:focus { outline: 2px solid #ff4444; }
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

    <div class="card">
        <label>📤 送信元Gmailアドレス</label>
        <input type="email" id="gmail_address" placeholder="例: xxxx@gmail.com">

        <label>🔑 アプリパスワード</label>
        <input type="password" id="gmail_password" placeholder="Google アプリパスワード（16文字）">

        <label>📥 送信先メールアドレス</label>
        <input type="email" id="to_address" placeholder="例: target@gmail.com">

        <label>⏱ 送信間隔（秒）</label>
        <input type="number" id="interval" value="3" min="1" step="1">

        <label>📝 メール件名</label>
        <input type="text" id="subject" value="【重要】お知らせ">

        <div class="btn-area">
            <button class="start" id="btn_start" onclick="startSpam()">🚀 送信開始</button>
            <button class="stop" id="btn_stop" onclick="stopSpam()" disabled>🛑 停止</button>
        </div>
    </div>

    <div class="card">
        <h3>📋 実行ログ</h3>
        <pre id="log_area">準備完了。「送信開始」を押してください。</pre>
    </div>

    <script>
        let isRunning = false;

        async function startSpam() {
            const data = {
                gmail_address: document.getElementById("gmail_address").value.trim(),
                gmail_password: document.getElementById("gmail_password").value.trim(),
                to_address: document.getElementById("to_address").value.trim(),
                interval: parseFloat(document.getElementById("interval").value) || 3,
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
            }
        }

        async function stopSpam() {
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
        }

        setInterval(updateLog, 1000);
    </script>
</body>
</html>
"""

# ========== APIルート ==========
@app.route("/")
def index():
    return render_template_string(INDEX_HTML)


@app.route("/start", methods=["POST"])
def start():
    global config
    if is_running:
        return jsonify({"ok": False, "msg": "実行中です"})

    data = request.get_json()
    config["gmail_address"] = data.get("gmail_address", "")
    config["gmail_password"] = data.get("gmail_password", "")
    config["to_address"] = data.get("to_address", "")
    config["interval"] = float(data.get("interval", 3))
    config["subject"] = data.get("subject", "【重要】お知らせ")

    thread = threading.Thread(target=send_email_thread, daemon=True)
    thread.start()
    return jsonify({"ok": True})


@app.route("/stop", methods=["POST"])
def stop():
    stop_flag.set()
    return jsonify({"ok": True})


@app.route("/log")
def get_log():
    with lock:
        return "\n".join(log_lines)


if __name__ == "__main__":
    add_log("🚀 サーバー起動完了")
    
    # ✅ RenderのURLを環境変数から自動取得
    RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")
    if RENDER_EXTERNAL_URL:
        render_url = RENDER_EXTERNAL_URL
        add_log(f"✅ RenderURL自動取得: {render_url}")
    else:
        render_url = "http://localhost:8080"
    
    # ✅ スリープ回避Pingを別スレッドで起動
    ping_thread = threading.Thread(target=self_ping_loop, daemon=True)
    ping_thread.start()
    add_log("✅ スリープ回避Ping 起動完了（約4分ごとに実行）")
    
    app.run(host="0.0.0.0", port=8080, debug=False)