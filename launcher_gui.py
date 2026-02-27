import tkinter as tk
from tkinter import ttk, messagebox
import threading
import os
import sys
import launcher

root = tk.Tk()
root.title("Minecraft Launcher")
root.geometry("460x420")

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

            status.set("下載 client / libs / assets / natives...")
            root.update()

            launcher.download_client(v, vjson)
            launcher.download_libraries(vjson)
            launcher.download_asset_index(vjson)
            launcher.download_assets(vjson)
            launcher.download_natives(vjson)

            status.set("啟動中...")
            root.update()

            launcher.launch(v, vjson, p, r)
            status.set("完成")
        except Exception as e:
            status.set(f"錯誤：{e}")

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
