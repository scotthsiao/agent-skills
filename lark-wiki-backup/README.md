# lark-wiki-backup — 安裝操作手冊

在無頭主機或容器上把 Lark/Feishu 內容備份下來的逐步流程。`SKILL.md` 是給 agent 讀的
參考文件，這份是給人照做的操作手冊。

可以備份兩種東西，先挑一種開始：

- **知識庫空間（Wiki space）** — 團隊知識庫。認證方式：一個「加入該空間」的 app。
- **個人 / 共享雲端資料夾（我的空間）** — 你自己的雲端檔案。認證方式：你自己的登入（OAuth）。

兩者產出一樣：內容的本地 git repo，增量更新，外加一份可選的異地 `.tar.gz`。

---

## Part A — 備份個人 / 共享雲端資料夾

### 1. 前置需求

- 主機上要有 `git`、Python 3.9+。
- 一個你能設定的 Lark app（沿用現有的 App ID / Secret 就行 — 這裡不是加 bot，只是把它
  當 OAuth client 用）。
- 你要備份哪個帳號的雲端空間，就用那個帳號，另外需要一次瀏覽器登入。

### 2. 在開發者後台設定 app

開 <https://open.larksuite.com/>（或 `open.feishu.cn`）→ 你的 app。

1. **安全設定 → 重定向 URL** → 加上（要完全一致）：
   ```
   http://localhost:9899
   ```
2. **權限管理** → 加入：
   ```
   offline_access
   drive:drive:readonly
   docx:document:readonly
   sheets:spreadsheet:readonly
   drive:export:readonly
   ```
   `offline_access` 是必要的 — 少了它 Lark 不會發 refresh token，cron 就無法自己續期。
3. **版本管理** → 建立並發布版本。權限變更沒發布不會生效。

### 3. 取得程式碼與 token 工具

```bash
git clone https://github.com/scotthsiao/agent-skills ~/src/agent-skills
cd ~/src/agent-skills/lark-wiki-backup
```

### 4. 提供 app 憑證給工具

用 export：

```bash
export FEISHU_APP_ID=cli_xxxxxxxx
export FEISHU_APP_SECRET=xxxxxxxx
```

……或指向一個含有 `FEISHU_APP_ID` / `FEISHU_APP_SECRET` 的 dotenv：

```bash
export LARK_ENV_FILE=~/.hermes/.env
```

### 5. 授權一次（需要瀏覽器）

```bash
python3 scripts/lark_user_token.py authorize
```

- 用瀏覽器開印出來的 URL。
- **用你要備份的那個帳號登入**，然後同意授權。
- 瀏覽器最後會停在打不開的頁面
  `http://localhost:9899/?code=...&state=...` — 這是正常的。把整條網址列複製起來。

```bash
python3 scripts/lark_user_token.py exchange "http://localhost:9899/?code=...&state=..."
```

這會印出第一個 access token，並把（會輪替的）refresh token 存到
`~/.config/lark/user_token.json`（權限 600）。

### 6. 確認 token 能刷新

```bash
python3 scripts/lark_user_token.py
```

應該只印出一個新的 access token、其他什麼都沒有。這一行指令就是備份 job 之後要呼叫的。

### 7. 第一次備份 — 先用一個小資料夾

先別對著整個雲端空間。從某個資料夾的網址
（`https://…/drive/folder/<TOKEN>`）取得它的 token：

```bash
export LARK_SOURCE=drive
export BACKUP_ROOT=~/lark-drive/test
export LARK_USER_TOKEN_CMD="python3 $PWD/scripts/lark_user_token.py"
export LARK_DRIVE_FOLDER_TOKEN=fldxxxxxxxx      # 那個測試用小資料夾

mkdir -p "$BACKUP_ROOT" && git -C "$BACKUP_ROOT" init -q
printf '%s\n' '.manifest.json' '.trash/' '*.tmp' > "$BACKUP_ROOT/.gitignore"

python3 scripts/lark_wiki_backup.py
```

檢查 `$BACKUP_ROOT` — 應該看到資料夾內的文件被鏡像下來、一個 git commit、一個
`.manifest.json`。原生文件會轉成 `.docx` / `.xlsx` / `.pdf`；上傳的檔案保留原檔名。

### 8. 對著整個雲端空間跑

拿掉 `LARK_DRIVE_FOLDER_TOKEN`，換 `BACKUP_ROOT`：

```bash
unset LARK_DRIVE_FOLDER_TOKEN
export BACKUP_ROOT=~/lark-drive/my-space
mkdir -p "$BACKUP_ROOT" && git -C "$BACKUP_ROOT" init -q
printf '%s\n' '.manifest.json' '.trash/' '*.tmp' > "$BACKUP_ROOT/.gitignore"
python3 scripts/lark_wiki_backup.py
```

沒指定資料夾 token 時，它會從你的個人根目錄（「我的空間」）開始，把整棵樹走完。

### 9. 排程

兩個 job，都是「執行腳本、把 stdout 原樣送出、不呼叫模型」：

| job | 排程（在地時間） | 指令 |
|---|---|---|
| mirror（鏡像） | 每天，早一點 | `lark_wiki_backup.py` |
| archive（打包） | 每天，在 mirror 之後 | `archive_snapshot.py` |

cron 是跑 **UTC** 的 — 把在地時間換算過去（見 `cron-timezone-discipline` skill；
例如台北 05:15 = `15 21 * * *`）。

job 的環境需要：`LARK_SOURCE=drive`、`BACKUP_ROOT`、`LARK_USER_TOKEN_CMD`，以及
`FEISHU_APP_*` 兩個變數或 `LARK_ENV_FILE`。

**異地打包（可選）：**

```bash
export ARCHIVE_SRC=~/lark-drive/my-space
export ARCHIVE_NAME=my-lark-drive
export ARCHIVE_UPLOAD=rclone            # 需要一個 rclone remote；見 headless-config-backup
export RCLONE_REMOTE=gdrive
export RCLONE_REMOTE_DIR="Lark Drive Backups"
python3 scripts/archive_snapshot.py
```

### 10. 維護

- refresh token 閒置約 30 天會過期，而且**每次使用都會輪替** — 只要每天的 job 有在跑，
  它就一直活著。如果失效了（或你手動撤銷），重做第 5 步。
- 如果某次執行印出很大的 `pruned N`，停下來檢查 — 通常代表 token 掉了某個 scope 或
  失去存取權，不是真的刪了 N 個檔。如果清單回傳是空的，腳本會直接拒絕執行。

---

## 幫多個人備份（共用一個 app）

**不需要每個人各自申請 App ID / Secret。** App ID / Secret 代表的是「OAuth 用戶端
（應用程式）」，不是使用者。使用者身分來自第 5 步 `authorize` 時**誰登入、誰同意授權**。

所以一個 app（例如你的）當共用 OAuth client，每位同事各自跑一次 `authorize`、用**自己
的帳號**登入，就會拿到綁定「(這個 app, 那個人)」的專屬 refresh token。

需要滿足：

1. **App 的可用範圍要包含這些同事。** 若 app 限定特定成員/部門，同事要在範圍內；若是
   企業自建應用且已發布給全組織就沒問題。範圍不夠要改「可用範圍」（發布給組織通常需要
   管理員審核，或把同事加為協作者/測試人員）。
2. **App Secret 放在哪** — 兩種做法：
   - **建議：所有備份都跑在你控制的一台機器上。** Secret 只存在一處，每位同事只要開
     一次授權連結、用自己帳號登入即可。用 `LARK_USER_CREDS` 幫每個人分開存 token：
     ```bash
     # 對每位同事，換一個 creds 路徑跑一次 authorize + exchange
     export LARK_USER_CREDS=~/.config/lark/alice.json
     python3 scripts/lark_user_token.py authorize
     # 把 URL 傳給 Alice，她登入後把 redirect URL 回傳給你
     python3 scripts/lark_user_token.py exchange "http://localhost:9899/?code=..."

     # 該同事的備份 job 就帶對應的 creds 路徑：
     LARK_USER_CREDS=~/.config/lark/alice.json \
     LARK_USER_TOKEN_CMD="LARK_USER_CREDS=~/.config/lark/alice.json python3 $PWD/scripts/lark_user_token.py" \
     BACKUP_ROOT=~/lark-drive/alice \
       python3 scripts/lark_wiki_backup.py
     ```
   - 若同事要在**自己的機器**上跑，他們的環境裡就會有 App Secret — 只在信任的團隊內
     這樣做。

3. **如果這是公司認可的備份需求**，比起逐人 OAuth，更乾淨的做法是走 Lark **管理員後台
   的資料匯出**功能，或請 IT 統一處理。

> 提醒：`authorize` 會把 `pending_state` 寫進 `LARK_USER_CREDS` 指的檔案，`exchange` 再
> 寫入 refresh token。多人共用一台機器時，**每個人一定要用不同的 `LARK_USER_CREDS`
> 路徑**，否則會互相覆蓋。

---

## Part B — 備份知識庫空間

### 1. 把 app 加進空間

在知識庫空間 → **設定 → 成員** → 加入你的 app。沒加的話節點清單會回傳空的。

app 權限（後台設定，然後發布版本）：`wiki:wiki:readonly`、`docx:document:readonly`、
`drive:export:readonly`。

### 2. 找出 space id

```bash
lark-cli wiki +space-list --as user
```

或開空間裡任一頁，呼叫
`wiki/v2/spaces/get_node?token=<page token>` — 回應裡有 `space_id`。

### 3. 執行

```bash
export LARK_SPACE_ID=7xxxxxxxxxxxxxxxxxx
export LARK_ENV_FILE=~/.hermes/.env         # 內含 FEISHU_APP_ID / FEISHU_APP_SECRET
export BACKUP_ROOT=~/lark-wiki/team-space

mkdir -p "$BACKUP_ROOT" && git -C "$BACKUP_ROOT" init -q
printf '%s\n' '.manifest.json' '.trash/' '*.tmp' > "$BACKUP_ROOT/.gitignore"

python3 scripts/lark_wiki_backup.py
```

不需要 user token — 空間成員身分下，app 自己的 `tenant_access_token` 就夠了。排程與
打包跟 Part A 的第 9、10 步一樣。

---

## 還原

- **單一頁面 / 檔案：** 從 `$BACKUP_ROOT` 複製回去（若是上游刪掉的，從 `.trash/` 拿），
  手動重新上傳到 Lark。
- **歷史版本：** `git -C "$BACKUP_ROOT" log --follow -- "<path>"`、
  `git checkout <sha> -- "<path>"`。
- Lark 沒有批次匯入 — 「還原回 Lark」是一份一份手動。你保住的是內容本身、完整編輯
  歷史、以及一份本地工具能讀的副本。

## 環境變數對照

見 [`SKILL.md`](SKILL.md) 的「Options (env)」表格。
