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
import os, json, requests, subprocess, zipfile, uuid, shutil, hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pathlib import Path

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

APP_VERSION = "1.0.8"
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

# ---------------- V2 全域高速下載核心
DOWNLOAD_WORKERS = 24
DOWNLOAD_TIMEOUT = (8, 45)
_CHUNK = 512 * 1024
_tls = threading.local()

def _session():
    s = getattr(_tls, "session", None)
    if s is None:
        s = requests.Session()
        retry = Retry(
            total=4, connect=4, read=4,
            backoff_factor=0.35,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET"]),
        )
        adapter = HTTPAdapter(
            max_retries=retry,
            pool_connections=DOWNLOAD_WORKERS,
            pool_maxsize=DOWNLOAD_WORKERS,
        )
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _tls.session = s
    return s

def _sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def _valid_file(path, size=None, sha1=None):
    """高速快取判斷：既有檔案只檢查存在與大小，不在每次啟動重算 SHA-1。"""
    if not os.path.exists(path):
        return False
    try:
        if size is not None and os.path.getsize(path) != int(size):
            return False
        return True
    except OSError:
        return False

def _download_one(job, byte_cb=None):
    url, path, size, sha1, kind = job
    if _valid_file(path, size, sha1):
        return ("cached", path, 0, kind)

    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    max_attempts = 3
    last_error = None

    for attempt in range(1, max_attempts + 1):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)

            # integrity retry 時加 cache-buster，並要求不要使用中間快取
            req_url = url
            headers = {}
            if attempt > 1:
                sep = "&" if "?" in url else "?"
                req_url = f"{url}{sep}_retry={int(time.time() * 1000)}_{attempt}"
                headers = {"Cache-Control": "no-cache", "Pragma": "no-cache"}

            with _session().get(
                req_url, stream=True, timeout=DOWNLOAD_TIMEOUT, headers=headers
            ) as r:
                r.raise_for_status()
                written = 0
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(chunk_size=_CHUNK):
                        if not chunk:
                            continue
                        f.write(chunk)
                        written += len(chunk)
                        if byte_cb:
                            byte_cb(len(chunk))

            actual_size = os.path.getsize(tmp)
            if size is not None and actual_size != int(size):
                raise IOError(
                    f"檔案大小驗證失敗：{os.path.basename(path)} "
                    f"(expected={size}, got={actual_size}, attempt={attempt}/{max_attempts})"
                )

            if sha1:
                got_sha1 = _sha1_file(tmp).lower()
                expected_sha1 = sha1.lower()
                if got_sha1 != expected_sha1:
                    raise IOError(
                        f"SHA1 驗證失敗：{os.path.basename(path)} "
                        f"(expected={expected_sha1}, got={got_sha1}, "
                        f"attempt={attempt}/{max_attempts})"
                    )

            os.replace(tmp, path)
            return ("downloaded", path, written, kind)

        except Exception as e:
            last_error = e
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except OSError:
                pass

            if attempt < max_attempts:
                time.sleep(0.6 * attempt)
                continue

    raise last_error

def build_download_plan(version, vjson):
    jobs = []

    # Client
    c = vjson.get("downloads", {}).get("client")
    if c:
        jobs.append((
            c["url"],
            os.path.join(VERSIONS_DIR, version, "client.jar"),
            c.get("size"), c.get("sha1"), "Client"
        ))

    # Libraries + natives
    native_paths = []
    for lib in vjson.get("libraries", []):
        downloads = lib.get("downloads", {})
        art = downloads.get("artifact")
        if art:
            jobs.append((
                art["url"], os.path.join(LIB_DIR, art["path"]),
                art.get("size"), art.get("sha1"), "Libraries"
            ))
        nat = downloads.get("classifiers", {}).get("natives-windows")
        if nat:
            p = os.path.join(LIB_DIR, nat["path"])
            jobs.append((nat["url"], p, nat.get("size"), nat.get("sha1"), "Natives"))
            native_paths.append(p)

    # Asset index 必須先取得，才能知道 assets 清單
    asset_index_path = None
    ai = vjson.get("assetIndex")
    if ai:
        asset_index_path = os.path.join(ASSETS_DIR, "indexes", vjson["assets"] + ".json")

    return jobs, native_paths, ai, asset_index_path

def _ensure_asset_index(ai, path):
    if not ai or not path:
        return
    job = (ai["url"], path, ai.get("size"), ai.get("sha1"), "Asset index")
    _download_one(job)

def _asset_jobs(asset_index_path):
    if not asset_index_path or not os.path.exists(asset_index_path):
        return []
    with open(asset_index_path, encoding="utf-8") as f:
        data = json.load(f)
    jobs = []
    for obj in data.get("objects", {}).values():
        h = obj["hash"]
        sub = h[:2]
        jobs.append((
            f"https://resources.download.minecraft.net/{sub}/{h}",
            os.path.join(ASSETS_DIR, "objects", sub, h),
            obj.get("size"), h, "Assets"
        ))
    return jobs

def download_all(version, vjson, progress=None):
    """
    建立單一下載計畫，Client / Libraries / Assets / Natives 共用 worker pool。
    progress(info_dict) 會收到：
      phase, done_files, total_files, downloaded_bytes, total_bytes,
      speed_bps, eta_seconds, cached_files
    """
    jobs, native_paths, ai, asset_index_path = build_download_plan(version, vjson)

    # index 很小，先拿到它，接著 assets 才能併入全域 queue
    _ensure_asset_index(ai, asset_index_path)
    jobs.extend(_asset_jobs(asset_index_path))

    # 高速掃描快取：只看檔案存在/大小，不重算既有檔案 SHA-1
    pending = []
    cached = 0
    total_bytes = 0
    total_files = len(jobs)
    downloaded_bytes = 0
    started = time.monotonic()
    lock = threading.Lock()

    # 掃描階段也回報進度，避免 GUI 看起來卡住
    for idx, j in enumerate(jobs, 1):
        _, path, size, sha1, _ = j
        if _valid_file(path, size, sha1):
            cached += 1
        else:
            pending.append(j)
            if size:
                total_bytes += int(size)

        if progress and (idx == total_files or idx % 100 == 0):
            progress({
                "phase": "檢查檔案",
                "done_files": idx,
                "total_files": total_files,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "speed_bps": 0,
                "eta_seconds": None,
                "cached_files": cached,
            })

    done_files = cached

    def emit(phase="下載"):
        if not progress:
            return
        elapsed = max(time.monotonic() - started, 0.001)
        speed = downloaded_bytes / elapsed
        remain = max(total_bytes - downloaded_bytes, 0)
        eta = (remain / speed) if speed > 1 else None
        progress({
            "phase": phase,
            "done_files": done_files,
            "total_files": total_files,
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": total_bytes,
            "speed_bps": speed,
            "eta_seconds": eta,
            "cached_files": cached,
        })

    def add_bytes(n):
        nonlocal downloaded_bytes
        with lock:
            downloaded_bytes += n
            # 不每個 512KB 都更新 Tk，約每次 chunk 更新即可，GUI 會自行節流
            emit("下載中")

    emit("檢查快取")

    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as pool:
        futs = {pool.submit(_download_one, j, add_bytes): j for j in pending}
        for fut in as_completed(futs):
            j = futs[fut]
            fut.result()
            with lock:
                done_files += 1
                emit(j[4])

    # 解壓 natives
    os.makedirs(NATIVES_DIR, exist_ok=True)
    for path in native_paths:
        if os.path.exists(path):
            with zipfile.ZipFile(path) as zf:
                for member in zf.infolist():
                    if not member.filename.startswith("META-INF/"):
                        zf.extract(member, NATIVES_DIR)

    emit("完成")

# 保留舊 API，避免其他程式 import launcher 時壞掉
def download_client(version, vjson, progress=None):
    c = vjson.get("downloads", {}).get("client")
    if not c: return
    _download_one((c["url"], os.path.join(VERSIONS_DIR, version, "client.jar"),
                   c.get("size"), c.get("sha1"), "Client"))

def download_libraries(vjson, progress=None):
    for lib in vjson.get("libraries", []):
        art = lib.get("downloads", {}).get("artifact")
        if art:
            _download_one((art["url"], os.path.join(LIB_DIR, art["path"]),
                           art.get("size"), art.get("sha1"), "Libraries"))

def download_asset_index(vjson, progress=None):
    ai = vjson.get("assetIndex")
    if ai:
        path = os.path.join(ASSETS_DIR, "indexes", vjson["assets"] + ".json")
        _ensure_asset_index(ai, path)

def download_assets(vjson, progress=None):
    path = os.path.join(ASSETS_DIR, "indexes", vjson["assets"] + ".json")
    for job in _asset_jobs(path):
        _download_one(job)

def download_natives(vjson, progress=None):
    for lib in vjson.get("libraries", []):
        nat = lib.get("downloads", {}).get("classifiers", {}).get("natives-windows")
        if nat:
            path = os.path.join(LIB_DIR, nat["path"])
            _download_one((nat["url"], path, nat.get("size"), nat.get("sha1"), "Natives"))
            os.makedirs(NATIVES_DIR, exist_ok=True)
            with zipfile.ZipFile(path) as zf:
                for member in zf.infolist():
                    if not member.filename.startswith("META-INF/"):
                        zf.extract(member, NATIVES_DIR)

# ---------------- 內建 Java Runtime
RUNTIME_DIR = os.path.join(BASE, "runtime")
RUNTIME_JAVA = os.path.join(RUNTIME_DIR, "bin", "java.exe")
ADOPTIUM_API = "https://api.adoptium.net/v3/assets/latest/{major}/hotspot"

def _required_java_major(vjson):
    # Mojang metadata on modern versions normally provides javaVersion.majorVersion.
    try:
        return int(vjson.get("javaVersion", {}).get("majorVersion", 21))
    except Exception:
        return 21

def _runtime_java():
    return RUNTIME_JAVA if os.path.isfile(RUNTIME_JAVA) else None

def download_embedded_java(vjson, progress=None):
    """下載 Windows x64 Temurin JRE 到 launcher/runtime，不修改系統 Java。"""
    major = _required_java_major(vjson)
    params = {
        "architecture": "x64",
        "image_type": "jre",
        "os": "windows",
        "vendor": "eclipse",
    }
    r = requests.get(
        ADOPTIUM_API.format(major=major),
        params=params,
        timeout=DOWNLOAD_TIMEOUT,
        headers={"User-Agent": "MyMinecraftLauncher/1.0"},
    )
    r.raise_for_status()
    assets = r.json()
    if not assets:
        raise RuntimeError(f"找不到 Java {major} Windows x64 Runtime")

    package = assets[0].get("binary", {}).get("package", {})
    url = package.get("link")
    size = package.get("size")
    checksum = package.get("checksum")
    if not url:
        raise RuntimeError(f"Java {major} Runtime 下載資訊不完整")

    archive = os.path.join(BASE, f".java-runtime-{major}.zip")
    if progress:
        progress({"phase": f"下載 Java {major}", "done_files": 0, "total_files": 1,
                  "downloaded_bytes": 0, "total_bytes": int(size or 0),
                  "speed_bps": 0, "eta_seconds": None, "cached_files": 0})

    # Adoptium checksum is SHA256; download with streaming and verify it.
    tmp = archive + ".part"
    started = time.monotonic()
    got = 0
    with requests.get(url, stream=True, timeout=DOWNLOAD_TIMEOUT) as rr:
        rr.raise_for_status()
        with open(tmp, "wb") as f:
            for chunk in rr.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                f.write(chunk)
                got += len(chunk)
                if progress:
                    elapsed = max(time.monotonic() - started, 0.001)
                    speed = got / elapsed
                    remain = max(int(size or 0) - got, 0)
                    progress({"phase": f"下載 Java {major}", "done_files": 0, "total_files": 1,
                              "downloaded_bytes": got, "total_bytes": int(size or 0),
                              "speed_bps": speed,
                              "eta_seconds": remain / speed if speed > 1 else None,
                              "cached_files": 0})
    os.replace(tmp, archive)

    if checksum:
        h = hashlib.sha256()
        with open(archive, "rb") as f:
            for b in iter(lambda: f.read(1024 * 1024), b""):
                h.update(b)
        if h.hexdigest().lower() != checksum.lower():
            os.remove(archive)
            raise RuntimeError("Java Runtime SHA256 驗證失敗")

    shutil.rmtree(RUNTIME_DIR, ignore_errors=True)
    extract_tmp = RUNTIME_DIR + "_extract"
    shutil.rmtree(extract_tmp, ignore_errors=True)
    os.makedirs(extract_tmp, exist_ok=True)
    with zipfile.ZipFile(archive) as zf:
        zf.extractall(extract_tmp)

    # ZIP 通常多包一層 jdk/jre 版本資料夾，把內容移到固定 runtime/
    entries = [p for p in Path(extract_tmp).iterdir()]
    source = entries[0] if len(entries) == 1 and entries[0].is_dir() else Path(extract_tmp)
    shutil.move(str(source), RUNTIME_DIR)
    shutil.rmtree(extract_tmp, ignore_errors=True)
    try:
        os.remove(archive)
    except OSError:
        pass

    java = _runtime_java()
    if not java:
        raise RuntimeError("Java Runtime 解壓完成，但找不到 bin/java.exe")

    if progress:
        progress({"phase": f"Java {major} 完成", "done_files": 1, "total_files": 1,
                  "downloaded_bytes": int(size or got), "total_bytes": int(size or got),
                  "speed_bps": 0, "eta_seconds": 0, "cached_files": 0})
    return java

# ---------------- Java 自動偵測 / 安裝
def find_java():
    """優先使用啟動器自己的 runtime，其次才尋找系統 Java。"""
    bundled = _runtime_java()
    if bundled:
        return bundled
    java = shutil.which("java")
    if java:
        return java

    candidates = []
    for env_name in ("JAVA_HOME",):
        home = os.environ.get(env_name)
        if home:
            candidates.append(os.path.join(home, "bin", "java.exe"))

    roots = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    vendors = [
        ("Eclipse Adoptium", "jdk-*", "bin", "java.exe"),
        ("Java", "jdk-*", "bin", "java.exe"),
        ("Microsoft", "jdk-*", "bin", "java.exe"),
    ]
    import glob
    for root in filter(None, roots):
        for vendor, pat, b, exe in vendors:
            candidates.extend(glob.glob(os.path.join(root, vendor, pat, b, exe)))

    existing = [p for p in candidates if os.path.isfile(p)]
    return existing[-1] if existing else None

def launch_java_installer():
    """
    沒有 Java 時開啟官方 Eclipse Adoptium Temurin 下載頁。
    由使用者選擇/執行安裝包，避免啟動器靜默安裝系統軟體。
    """
    import webbrowser
    url = "https://adoptium.net/temurin/releases/?os=windows&arch=x64"
    webbrowser.open(url)
    return url

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
    java_exe = find_java()
    if not java_exe:
        raise RuntimeError("JAVA_NOT_FOUND")
    main=vjson.get("mainClass","net.minecraft.client.main.Main")
    cp=build_classpath(version,vjson)
    uuid_val=offline_uuid(player)

    cmd=[
        java_exe,
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

