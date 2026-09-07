# Copyright (C) 2026 nono936
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
# See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.
# If not, see <https://www.gnu.org/licenses/>.
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import os
import sys
import launcher as launcher

root = tk.Tk()
root.title("Minecraft Launcher V6")
root.geometry("720x440")

tk.Label(root, text="玩家名稱").pack(pady=5)
name_var = tk.StringVar(value="Player")
tk.Entry(root, textvariable=name_var).pack()

tk.Label(root, text="版本").pack(pady=5)
versions = launcher.get_all_versions()
version_var = tk.StringVar()
combo = ttk.Combobox(root, textvariable=version_var, values=versions)
if versions:
    combo.current(0)
combo.pack()

tk.Label(root, text="記憶體").pack(pady=5)
ram_var = tk.StringVar()
ram_combo = ttk.Combobox(root, textvariable=ram_var, values=["2G", "4G", "8G"])
ram_combo.current(1)
ram_combo.pack()

status = tk.StringVar(value="準備就緒")
tk.Label(root, textvariable=status).pack(pady=8)

progress_var = tk.DoubleVar(value=0)
progress_bar = ttk.Progressbar(root, variable=progress_var, maximum=100, length=620)
progress_bar.pack(pady=4)

def _fmt_bytes(n):
    n = float(n or 0)
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024

_last_gui_update = 0.0
def download_progress(info):
    global _last_gui_update
    now = __import__("time").monotonic()
    if now - _last_gui_update < 0.08 and info.get("phase") != "完成":
        return
    _last_gui_update = now

    done = info.get("done_files", 0)
    total = info.get("total_files", 0)
    got = info.get("downloaded_bytes", 0)
    total_b = info.get("total_bytes", 0)
    speed = info.get("speed_bps", 0)
    eta = info.get("eta_seconds")
    cached = info.get("cached_files", 0)
    pct = (done / total * 100) if total else 100

    eta_text = "--" if eta is None else f"{eta:.0f}s"
    text = (
        f"{info.get('phase','下載')}  {done}/{total}（{pct:.1f}%） | "
        f"{_fmt_bytes(got)}/{_fmt_bytes(total_b)} | "
        f"{_fmt_bytes(speed)}/s | ETA {eta_text} | 快取 {cached}"
    )

    def update_ui():
        progress_var.set(pct)
        status.set(text)
    root.after(0, update_ui)

update_hint = tk.StringVar(value="更新：檢查中...")
tk.Label(root, textvariable=update_hint).pack(pady=4)

_last_update_info = None
_update_prompted = False  # 避免啟動後重複彈窗


def start_game():
    def run():
        try:
            v = version_var.get().strip()
            p = (name_var.get().strip() or "Player")
            r = (ram_var.get().strip() or "4G")

            status.set("取得版本資訊...")
            root.update()
            vjson = launcher.get_version_json(v)

            java_exe = launcher.find_java()
            if not java_exe:
                status.set("未偵測到 Java，正在準備內建 Runtime...")
                launcher.download_embedded_java(vjson, download_progress)
                java_exe = launcher.find_java()
                if not java_exe:
                    raise RuntimeError("Java Runtime 安裝失敗")

            status.set("建立下載清單 / 檢查快取...")
            root.update()

            launcher.download_all(v, vjson, download_progress)

            status.set("啟動中...")
            root.update()

            launcher.launch(v, vjson, p, r)
            status.set("完成")
        except Exception as e:
            msg = str(e)
            root.after(0, lambda: status.set(f"錯誤：{msg}"))
            root.after(0, lambda: messagebox.showerror("Minecraft 下載失敗", msg))

    threading.Thread(target=run, daemon=True).start()


def clean():
    try:
        launcher.clean_minecraft()
        status.set("已清理（保留 assets / libraries）")
    except Exception as e:
        status.set(f"清理失敗：{e}")


def check_updates(show_popup=False):
    def run():
        global _last_update_info, _update_prompted
        update_hint.set("更新：檢查中...")
        root.update()

        info = launcher.check_update()  # ✅ dict
        _last_update_info = info

        # 讓你一眼看出抓到的版本
        current = info.get("current", "?")
        latest = info.get("latest", "?")

        if info.get("has_update"):
            cnt = info.get("count", 0)
            notes = info.get("notes", "")
            update_hint.set(f"更新：有更新（{cnt}） {current} → {latest}")

            if show_popup and (not _update_prompted):
                _update_prompted = True
                msg = f"發現新版本：{latest}\n更新檔案數：{cnt}"
                if notes:
                    msg += f"\n\n更新內容：\n{notes}"

                if messagebox.askyesno("有更新可用", msg + "\n\n要立即套用更新嗎？"):
                    apply_updates()
        else:
            # 無更新（0）
            update_hint.set(f"更新：無更新（0） 版本 {current} / 最新 {latest}")

            notes = info.get("notes", "")
            if isinstance(notes, str) and notes.startswith("更新檢查失敗"):
                update_hint.set("更新：檢查失敗")
                if show_popup and (not _update_prompted):
                    _update_prompted = True
                    messagebox.showwarning("更新檢查失敗", notes)

    threading.Thread(target=run, daemon=True).start()


def apply_updates():
    def run():
        try:
            info = _last_update_info or launcher.check_update()

            if not info.get("has_update"):
                messagebox.showinfo("更新", "目前已是最新版本")
                return

            status.set("下載並套用更新中...")
            root.update()

            # ✅ dict 版 apply_update 期待的是整個 info
            ok, msg = launcher.apply_update(info)

            status.set(msg)
            if ok:
                messagebox.showinfo("更新完成", msg + "\n\n即將自動重啟啟動器。")
                python = sys.executable
                os.execl(python, python, *sys.argv)
            else:
                messagebox.showerror("更新失敗", msg)

        except Exception as e:
            status.set("更新失敗")
            messagebox.showerror("更新失敗", str(e))

    threading.Thread(target=run, daemon=True).start()


tk.Button(root, text="啟動", command=start_game).pack(pady=6)
tk.Button(root, text="一鍵清理", command=clean).pack(pady=6)
tk.Button(root, text="套用更新", command=apply_updates).pack(pady=6)

# ✅ 開啟後自動檢查更新：有更新就彈窗
root.after(300, lambda: check_updates(True))

root.mainloop()
