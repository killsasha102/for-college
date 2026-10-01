import os
import sys
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

# Папки, которые удаляются из ВСЕЙ истории
PURGE_DIRS = ["Intermediate", "Saved", "Binaries", "DerivedDataCache", ".vs"]

UNREAL_IGNORE = """# === Unreal Engine generated files ===
Binaries/
DerivedDataCache/
Intermediate/
Saved/
.vs/
*.suo
*.user
*.userosscache
*.sln.docstates
Build/
Builds/
.DS_Store
Thumbs.db
"""

# Что переносим в Git LFS
LFS_PATTERNS = [
    "*.uasset", "*.umap", "*.fbx", "*.obj", "*.blend",
    "*.png", "*.jpg", "*.jpeg", "*.tga", "*.bmp", "*.exr",
    "*.wav", "*.mp3", "*.ogg", "*.flac",
    "*.mp4", "*.mov", "*.avi", "*.webm",
    "*.psd", "*.kra", "*.zip", "*.7z", "*.rar",
]


def find_program(name):
    found = shutil.which(name)
    if found:
        return found

    candidates = []
    if name == "git":
        candidates += [
            os.path.expandvars(r"%ProgramFiles%\Git\cmd\git.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Git\cmd\git.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\Git\cmd\git.exe"),
        ]
        gh = Path(os.environ.get("LOCALAPPDATA", "")) / "GitHubDesktop"
        if gh.exists():
            for p in sorted(gh.glob("app-*"), reverse=True):
                candidates.append(str(p / "resources" / "app" / "git" / "cmd" / "git.exe"))
    elif name == "git-lfs":
        candidates += [
            os.path.expandvars(r"%ProgramFiles%\Git\mingw64\bin\git-lfs.exe"),
            os.path.expandvars(r"%ProgramFiles%\Git LFS\git-lfs.exe"),
            os.path.expandvars(r"%LocalAppData%\Programs\Git\mingw64\bin\git-lfs.exe"),
        ]

    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


GIT = find_program("git")
GIT_LFS = find_program("git-lfs")


def run(cmd, cwd=None, capture=False):
    print("\n>", " ".join(str(x) for x in cmd))
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=capture)


def git(args, cwd=None, capture=False):
    return run([GIT] + args, cwd, capture)


def lfs(args, cwd=None):
    return run([GIT_LFS] + args, cwd)


def ask_yes(question):
    return input(question + " [y/N]: ").strip().lower() in ("y", "yes", "д", "да")


def repo_name_from_url(url):
    name = urlparse(url).path.strip("/").split("/")[-1]
    return name[:-4] if name.endswith(".git") else name


def stop(msg):
    print("\n" + msg)
    input("\nEnter...")
    sys.exit(1)


def purge_with_filter_repo(repo):
    """Возвращает True, если git-filter-repo отработал."""
    args = ["--force", "--invert-paths"]
    for d in PURGE_DIRS:
        args += ["--path", d + "/", "--path-glob", "*/" + d + "/*"]

    # 1) python -m git_filter_repo
    probe = subprocess.run([sys.executable, "-m", "git_filter_repo", "--version"],
                           capture_output=True, text=True)
    if probe.returncode != 0:
        print("\nУстанавливаю git-filter-repo...")
        run([sys.executable, "-m", "pip", "install", "git-filter-repo"])
        probe = subprocess.run([sys.executable, "-m", "git_filter_repo", "--version"],
                               capture_output=True, text=True)

    if probe.returncode == 0:
        return run([sys.executable, "-m", "git_filter_repo"] + args, cwd=repo).returncode == 0

    # 2) git filter-repo (если установлен как git-команда)
    return git(["filter-repo"] + args, cwd=repo).returncode == 0


def purge_with_filter_branch(repo):
    """Запасной вариант (медленнее, но есть в любом Git)."""
    specs = []
    for d in PURGE_DIRS:
        specs += [d, "*/" + d]
    cmd = "git rm -r --cached --ignore-unmatch -q -- " + " ".join(f"'{s}'" for s in specs)
    env = dict(os.environ, FILTER_BRANCH_SQUELCH_WARNING="1")
    print("\n> git filter-branch (может занять время)...")
    r = subprocess.run(
        [GIT, "filter-branch", "-f", "--index-filter", cmd,
         "--prune-empty", "--tag-name-filter", "cat", "--", "--all"],
        cwd=repo, text=True, env=env)
    if r.returncode != 0:
        return False
    # Чистим старые ссылки и мусор
    git(["for-each-ref", "--format=%(refname)", "refs/original/"], cwd=repo)
    shutil.rmtree(repo / ".git" / "refs" / "original", ignore_errors=True)
    git(["reflog", "expire", "--expire=now", "--all"], cwd=repo)
    git(["gc", "--prune=now", "--aggressive"], cwd=repo)
    return True


def main():
    print("=" * 70)
    print(" Исправление Unreal Engine репозитория: очистка истории + Git LFS")
    print("=" * 70)

    if not GIT:
        stop("Git не найден. Установи: https://git-scm.com/download/win")
    if not GIT_LFS:
        stop("Git LFS не найден. Установи: https://git-lfs.com/")

    url = input("\nСсылка на GitHub-репозиторий:\n> ").strip()
    if not url:
        return
    name = repo_name_from_url(url)
    if not name:
        stop("Некорректная ссылка.")

    # Если скрипт лежит внутри репозитория, берём именно эту папку
    if (Path.cwd() / ".git").exists():
        repo = Path.cwd()
    else:
        repo = Path.cwd() / name

    # --- 1. Клонирование / поиск репозитория ---
    if not (repo / ".git").exists():
        print("\nКлонирование...")
        if git(["clone", url, str(repo)]).returncode != 0:
            stop("Не удалось клонировать репозиторий.")
    else:
        print("\nИспользуется существующая папка:", repo)

    # Подтягиваем все ветки
    git(["fetch", "--all", "--tags"], cwd=repo)

    # --- 2. Резервная копия ---
    backup = Path.cwd() / (name + "_backup.git")
    if not backup.exists():
        print("\nСоздаю резервную копию:", backup)
        git(["clone", "--mirror", str(repo), str(backup)])

    print("\n⚠ Сейчас будет ПЕРЕПИСАНА история Git (коммиты получат новые хэши).")
    print("  Если репозиторием пользуется кто-то ещё, им придётся клонировать заново.")
    if not ask_yes("Продолжить?"):
        print("Отменено.")
        return

    # --- 3. Чистка истории ---
    print("\nУдаляю из истории:", ", ".join(PURGE_DIRS))
    ok = purge_with_filter_repo(repo)
    if not ok:
        print("\ngit-filter-repo не сработал, пробую git filter-branch...")
        ok = purge_with_filter_branch(repo)
    if not ok:
        stop("Не удалось переписать историю. Резервная копия: " + str(backup))

    # filter-repo удаляет origin — возвращаем
    remotes = git(["remote"], cwd=repo, capture=True).stdout.split()
    if "origin" not in remotes:
        git(["remote", "add", "origin", url], cwd=repo)

    # --- 4. .gitignore ---
    gi = repo / ".gitignore"
    existing = gi.read_text(encoding="utf-8", errors="ignore") if gi.exists() else ""
    if "# === Unreal Engine generated files ===" not in existing:
        gi.write_text(existing.rstrip() + "\n\n" + UNREAL_IGNORE, encoding="utf-8")
        print("\nДобавлен Unreal .gitignore.")
    git(["add", ".gitignore"], cwd=repo)
    git(["commit", "-m", "Add Unreal Engine .gitignore"], cwd=repo)

    # --- 5. Git LFS ---
    lfs(["install"], cwd=repo)

    if ask_yes("\nПеренести ассеты (.uasset, .umap, .fbx, .png ...) в LFS по всей истории?"):
        include = ",".join(LFS_PATTERNS)
        r = lfs(["migrate", "import", "--everything", f"--include={include}", "--yes"], cwd=repo)
        if r.returncode != 0:
            print("\nlfs migrate не удался — настраиваю LFS только для будущих коммитов.")
            for pat in LFS_PATTERNS:
                lfs(["track", pat], cwd=repo)
            git(["add", ".gitattributes"], cwd=repo)
            git(["commit", "-m", "Configure Git LFS"], cwd=repo)
    else:
        for pat in LFS_PATTERNS:
            lfs(["track", pat], cwd=repo)
        git(["add", ".gitattributes"], cwd=repo)
        git(["commit", "-m", "Configure Git LFS"], cwd=repo)

    # --- 6. Проверка больших файлов ---
    print("\nФайлы в LFS:")
    lfs(["ls-files"], cwd=repo)

    # --- 7. Push ---
    print("\nОтправка на GitHub (force-with-lease)...")
    git(["fetch", "origin"], cwd=repo)
    push = git(["push", "--force-with-lease", "--all", "origin"], cwd=repo)
    git(["push", "--force-with-lease", "--tags", "origin"], cwd=repo)

    print("\n" + "=" * 70)
    if push.returncode == 0:
        print(" ГОТОВО! История очищена, LFS настроен, push выполнен.")
        print(" Папки Intermediate/Saved/Binaries/DerivedDataCache удалены из истории")
        print(" (локально они пересоздадутся при следующем запуске Unreal).")
    else:
        print(" PUSH НЕ УДАЛСЯ")
        print(" Проверь вывод выше. Если файл >100 MB всё ещё в истории, он лежит")
        print(" вне перечисленных папок и не подошёл под LFS-шаблоны.")
        print(" Резервная копия:", backup)
    print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nОтменено.")
    except SystemExit:
        raise
    except Exception as e:
        print("\nОШИБКА:", e)
    input("\nEnter...")
