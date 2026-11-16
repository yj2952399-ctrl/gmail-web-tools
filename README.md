---
title: Gmail Web Tools
sdk: docker
app_port: 7860
---

# Gmail 連絡送信システム

## 設定
Settings → Variables and secrets に登録:

- `SESSION_SECRET` … ランダム32文字（secret として登録。無いと再起動でセッションが飛ぶ）
- `ACCOUNT_CONCURRENCY` … 1アカウントあたりの同時接続（既定 3、上限 5 推奨）
- `SENDS_PER_CONN` … 接続を使い回す通数（既定 25）
- `DAILY_CAP` … 1アカウント/日の上限（既定 400、Workspace は 1500 まで可）
- `MAX_ACCOUNTS` … 登録可能なアカウント数上限（既定 200）

## 前提
- 送信元 Gmail は 2段階認証 ON + アプリパスワード発行済みであること
- アプリパスワードは 16 文字。スペースは自動除去されます
- SMTP は `smtp.gmail.com:465`（SSL）