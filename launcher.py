import os, json, requests, subprocess, zipfile, uuid, shutil, hashlib

BASE = os.getcwd()
VERSIONS_DIR = os.path.join(BASE, "versions")
LIB_DIR = os.path.join(BASE, "libraries")
ASSETS_DIR = os.path.join(BASE, "assets")
GAME_DIR = os.path.join(BASE, "game")
NATIVES_DIR = os.path.join(BASE, "natives")

os.makedirs(VERSIONS_DIR, exist_ok=True)
os.makedirs(LIB_DIR, exist_ok=True)
os.makedirs(ASSETS_DIR, exist_ok=True)
os.makedirs(GAME_DIR, exist_ok=True)
os.makedirs(NATIVES_DIR, exist_ok=True)

APP_VERSION = "1.0.1"
UPDATE_MANIFEST_URL = "https://raw.githubusercontent.com/nono936/MyLauncherUpdate/main/update.json"

# ---------------- UUID
def offline_uuid(name):
    return str(uuid.uuid3(uuid.NAMESPACE_DNS, name))

# ---------------- 版本
def get_version_list():
    manifest = requests.get("https://launchermeta.mojang.com/mc/game/version_manifest.json").json()
    return [v["id"] for v in manifest["versions"] if v["type"]=="release"]

def get_local_versions():
    return os.listdir(VERSIONS_DIR) if os.path.exists(VERSIONS_DIR) else []

def get_all_versions():
    return sorted(set(get_version_list()[:20] + get_local_versions()), reverse=True)

# ---------------- JSON
def get_version_json(version):
    local = os.path.join(VERSIONS_DIR, version, version + ".json")
    if os.path.exists(local):
        return json.load(open(local))

    manifest = requests.get("https://launchermeta.mojang.com/mc/game/version_manifest.json").json()
    for v in manifest["versions"]:
        if v["id"] == version:
            vjson = requests.get(v["url"]).json()
            os.makedirs(os.path.dirname(local), exist_ok=True)
            json.dump(vjson, open(local,"w"))
            return vjson

# ---------------- 下載
def download_client(version, vjson):
    if "downloads" not in vjson: return
    path = os.path.join(VERSIONS_DIR, version, "client.jar")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path,"wb").write(requests.get(vjson["downloads"]["client"]["url"]).content)

def download_libraries(vjson):
    for lib in vjson.get("libraries",[]):
        art = lib.get("downloads",{}).get("artifact")
        if art:
            full=os.path.join(LIB_DIR, art["path"])
            if not os.path.exists(full):
                os.makedirs(os.path.dirname(full),exist_ok=True)
                open(full,"wb").write(requests.get(art["url"]).content)

def download_asset_index(vjson):
    if "assetIndex" not in vjson: return
    path=os.path.join(ASSETS_DIR,"indexes",vjson["assets"]+".json")
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path),exist_ok=True)
        open(path,"wb").write(requests.get(vjson["assetIndex"]["url"]).content)

def download_assets(vjson):
    if "assetIndex" not in vjson: return
    index=os.path.join(ASSETS_DIR,"indexes",vjson["assets"]+".json")
    if not os.path.exists(index): return
    data=json.load(open(index))
    for obj in data["objects"].values():
        h=obj["hash"]; sub=h[:2]
        path=os.path.join(ASSETS_DIR,"objects",sub,h)
        if not os.path.exists(path):
            url=f"https://resources.download.minecraft.net/{sub}/{h}"
            os.makedirs(os.path.dirname(path),exist_ok=True)
            open(path,"wb").write(requests.get(url).content)

def download_natives(vjson):
    for lib in vjson.get("libraries",[]):
        nat=lib.get("downloads",{}).get("classifiers",{}).get("natives-windows")
        if nat:
            path=os.path.join(LIB_DIR,nat["path"])
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path),exist_ok=True)
                open(path,"wb").write(requests.get(nat["url"]).content)
            zipfile.ZipFile(path).extractall(NATIVES_DIR)

# ---------------- Launch
def build_classpath(version,vjson):
    cp=[]
    for lib in vjson.get("libraries",[]):
        art=lib.get("downloads",{}).get("artifact")
        if art:
            cp.append(os.path.join(LIB_DIR, art["path"]))
    jar=os.path.join(VERSIONS_DIR,version,"client.jar")
    cp.append(jar)
    return ";".join(cp)

def launch(version,vjson,player="Player",ram="4G"):
    main=vjson.get("mainClass","net.minecraft.client.main.Main")
    cp=build_classpath(version,vjson)
    uuid_val=offline_uuid(player)

    cmd=[
        "java",
        f"-Xmx{ram}",
        f"-Djava.library.path={NATIVES_DIR}",
        "-cp",cp,
        main,
        "--username",player,
        "--version",version,
        "--gameDir",GAME_DIR,
        "--assetsDir",ASSETS_DIR,
        "--assetIndex",vjson.get("assets",""),
        "--uuid",uuid_val,
        "--accessToken","0",
        "--userType","legacy"
    ]
    subprocess.run(cmd)

# ---------------- 清理
def clean_minecraft():
    for t in ["versions","game","natives","logs","resourcepacks","shaderpacks","crash-reports"]:
        p=os.path.join(BASE,t)
        if os.path.exists(p):
            shutil.rmtree(p)

# ---------------- 更新
import time

UPDATE_TIMEOUT = 15

def _semver_tuple(v: str):
    parts = (v or "").strip().split(".")
    nums = []
    for p in parts:
        try:
            nums.append(int(p))
        except:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])

def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()

def check_update():
    """
    回傳 dict:
    {
      has_update: bool,
      current: str,
      latest: str,
      count: int,
      notes: str,
      files: list,
      raw: dict|None
    }
    """
    try:
        # ⭐ 防快取：每次加時間戳
        url = f"{UPDATE_MANIFEST_URL}?_={int(time.time())}"
        r = requests.get(url, timeout=UPDATE_TIMEOUT, headers={"Cache-Control": "no-cache"})
        r.raise_for_status()
        data = r.json()

        latest = str(data.get("latest", "")).strip()
        files = data.get("files", []) or []
        notes = str(data.get("notes", "")).strip()

        has_update = _semver_tuple(latest) > _semver_tuple(APP_VERSION)

        return {
            "has_update": has_update,
            "current": APP_VERSION,
            "latest": latest if latest else APP_VERSION,
            "count": len(files) if has_update else 0,
            "notes": notes,
            "files": files,
            "raw": data,
        }
    except Exception as e:
        return {
            "has_update": False,
            "current": APP_VERSION,
            "latest": APP_VERSION,
            "count": 0,
            "notes": f"更新檢查失敗：{e}",
            "files": [],
            "raw": None,
        }

def apply_update(update_info: dict):
    """
    下載並覆蓋 update.json 指定的檔案
    回傳 (ok: bool, msg: str)
    """
    if not update_info.get("has_update"):
        return False, "目前已是最新版本"

    files = update_info.get("files", [])
    if not files:
        return False, "更新清單為空"

    tmp_dir = os.path.join(BASE, ".update_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        downloaded = []
        for f in files:
            rel_path = (f.get("path") or "").strip()
            url = (f.get("url") or "").strip()
            expected = (f.get("sha256") or "").strip().lower()

            if not rel_path or not url:
                continue

            rr = requests.get(f"{url}?_={int(time.time())}", timeout=UPDATE_TIMEOUT, headers={"Cache-Control": "no-cache"})
            rr.raise_for_status()
            content = rr.content

            if expected:
                got = _sha256_bytes(content)
                if got != expected:
                    return False, f"SHA256 不符：{rel_path}"

            out_path = os.path.join(tmp_dir, rel_path)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as wf:
                wf.write(content)

            downloaded.append(rel_path)

        if not downloaded:
            return False, "沒有可更新的檔案"

        for rel_path in downloaded:
            src = os.path.join(tmp_dir, rel_path)
            dst = os.path.join(BASE, rel_path)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(src, "rb") as rf, open(dst, "wb") as wf:
                wf.write(rf.read())

        shutil.rmtree(tmp_dir, ignore_errors=True)
        return True, f"已更新到 {update_info.get('latest')}"
    except Exception as e:
        return False, f"套用更新失敗：{e}"
