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

APP_VERSION = "1.0.0"
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
def check_update():
    try:
        data=requests.get(UPDATE_MANIFEST_URL).json()
        latest=data["latest"]
        return latest != APP_VERSION, data
    except:
        return False, None

def apply_update(data):
    for f in data["files"]:
        path=f["path"]
        url=f["url"]
        full=os.path.join(BASE,path)
        os.makedirs(os.path.dirname(full),exist_ok=True)
        open(full,"wb").write(requests.get(url).content)
