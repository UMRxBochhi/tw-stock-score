@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [1/3] 取得雲端最新資料...
git checkout -- data 2>nul
git pull -q
echo [2/3] 補抓最新資料並計分（第一次需要先 pip install requests）...
python scripts\update.py
echo [3/3] 開啟網頁 http://localhost:8765 （關掉這個視窗就停止）
start "" http://localhost:8765
python -m http.server 8765 -d site
