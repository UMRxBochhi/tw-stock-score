# 台股評分看板

每個交易日台灣時間 18:30，由 GitHub Actions 自動抓證交所公開資料，計算基本面 / 籌碼面 / 技術面分數，發佈到 GitHub Pages。

- `scripts/update.py`：抓資料、計分，輸出 `site/data/latest.json`
- `data/`：歷史資料（收盤價、法人買賣超、本益比月取樣），代替資料庫
- `site/`：靜態網頁
- `data/probe.json`：每次執行的資料源狀態，除錯用

分數是相對排名，不是投資建議。

## 在自己電腦看（本機版）

需要 Python 3 與 git。第一次執行 `pip install requests`，之後點兩下 `start.bat`：
先拉雲端已算好的資料，再補抓最新的一兩天並計分，最後自動開啟 http://localhost:8765 。
