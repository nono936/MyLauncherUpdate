import tkinter as tk
from tkinter import ttk
import threading, launcher

root=tk.Tk()
root.title("Minecraft Launcher")
root.geometry("420x380")

tk.Label(root,text="玩家名稱").pack()
name_var=tk.StringVar(value="Player")
tk.Entry(root,textvariable=name_var).pack()

tk.Label(root,text="版本").pack()
versions=launcher.get_all_versions()
version_var=tk.StringVar()
combo=ttk.Combobox(root,textvariable=version_var,values=versions)
combo.current(0)
combo.pack()

tk.Label(root,text="記憶體").pack()
ram_var=tk.StringVar()
ram_combo=ttk.Combobox(root,textvariable=ram_var,values=["2G","4G","8G"])
ram_combo.current(1)
ram_combo.pack()

status=tk.StringVar(value="準備就緒")
tk.Label(root,textvariable=status).pack(pady=5)

update_hint=tk.StringVar(value="更新：檢查中...")
tk.Label(root,textvariable=update_hint).pack()

def start_game():
    def run():
        v=version_var.get()
        p=name_var.get()
        r=ram_var.get()
        vjson=launcher.get_version_json(v)
        launcher.download_client(v,vjson)
        launcher.download_libraries(vjson)
        launcher.download_asset_index(vjson)
        launcher.download_assets(vjson)
        launcher.download_natives(vjson)
        launcher.launch(v,vjson,p,r)
    threading.Thread(target=run).start()

def clean():
    launcher.clean_minecraft()
    status.set("已清理")

def check_updates():
    has,data=launcher.check_update()
    if has:
        update_hint.set("更新：有更新")
    else:
        update_hint.set("更新：無更新（0）")

def apply_updates():
    has,data=launcher.check_update()
    if has:
        launcher.apply_update(data)
        update_hint.set("已更新")

tk.Button(root,text="啟動",command=start_game).pack(pady=5)
tk.Button(root,text="一鍵清理",command=clean).pack(pady=5)


root.mainloop()
