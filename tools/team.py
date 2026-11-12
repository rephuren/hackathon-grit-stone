#!/usr/bin/env python3
"""Координация команды и агентов (Claude, Codex, люди) через файлы в репозитории. Без зависимостей.

Зачем: двое правят один код одновременно. Перед задачей читаем заявки других, свою записываем, после задачи снимаем.
Каждая заявка в ОТДЕЛЬНОМ файле .team/claims/<кто>__<номер>.json, поэтому слияние не конфликтует.

  python3 tools/team.py status                              что сейчас занято и какие ветки живы
  python3 tools/team.py start <кто> "<задача>" <файл/папка>...   занять файлы (или отказ, если занято)
  python3 tools/team.py finish <кто> [номер]                снять заявку (после слияния/пуша работы)
  python3 tools/team.py say <от> <кому> "<текст>"           написать сообщение коллеге
  python3 tools/team.py inbox <кто>                         прочитать свои сообщения (помечает прочитанными)

Заявка живёт 4 часа без обновления (после этого считается устаревшей и не блокирует, но показывается в status).
"""
import json
import os
import subprocess
import sys
import time

ROOT = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip()
TEAM = os.path.join(ROOT, ".team")
CLAIMS = os.path.join(TEAM, "claims")
INBOX = os.path.join(TEAM, "inbox")
TTL = 4 * 3600
SHARED_ONLY = (".team/",)  # эти файлы можно пушить прямо в main, это сама координация


def git(*a, check=True):
    return subprocess.run(["git", "-C", ROOT, *a], text=True, capture_output=True, check=check)


def sync():
    git("fetch", "-q", "origin", check=False)
    git("pull", "-q", "--rebase", "--autostash", "origin", "main", check=False)


def push_team(msg: str):
    git("add", ".team")
    if not git("status", "--porcelain", ".team").stdout.strip():
        return
    git("commit", "-q", "-m", msg)
    for _ in range(4):
        if git("push", "-q", "origin", "HEAD:main", check=False).returncode == 0:
            return
        git("pull", "-q", "--rebase", "--autostash", "origin", "main", check=False)
    print("Не удалось отправить координационный файл. Проверьте сеть и повторите push.")


def load_claims():
    out = []
    if os.path.isdir(CLAIMS):
        for fn in sorted(os.listdir(CLAIMS)):
            if fn.endswith(".json"):
                try:
                    with open(os.path.join(CLAIMS, fn), encoding="utf-8") as fh:
                        c = json.load(fh)
                    c["_file"] = fn
                    c["stale"] = time.time() - c.get("updated", c["created"]) > TTL
                    out.append(c)
                except (OSError, ValueError):
                    pass
    return out


def norm(p: str) -> str:
    p = os.path.normpath(p.replace("\\", "/")).replace("\\", "/")
    return p.rstrip("/")


def overlap(a: str, b: str) -> bool:
    a, b = norm(a), norm(b)
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def conflicts(paths, who, claims):
    res = []
    for c in claims:
        if c["who"] == who or c["stale"]:
            continue
        hit = [(p, q) for p in paths for q in c["paths"] if overlap(p, q)]
        if hit:
            res.append((c, hit))
    return res


def cmd_status():
    sync()
    claims = load_claims()
    print("== Заявки ==")
    if not claims:
        print("Никто ничего не занял.")
    for c in claims:
        age = int((time.time() - c.get("updated", c["created"])) / 60)
        print(f"[{'УСТАРЕЛА' if c['stale'] else 'активна'}] {c['who']}: {c['task']}  ({age} мин)\n    файлы: {', '.join(c['paths'])}")
    print("== Ветки на GitHub ==")
    r = git("for-each-ref", "--sort=-committerdate", "--format=%(refname:short)|%(committerdate:relative)|%(authorname)", "refs/remotes/origin", check=False).stdout
    for line in r.splitlines():
        if "HEAD" not in line:
            print("  " + line.replace("|", "  ·  "))
    print("== Свежие коммиты main ==")
    print(git("log", "--oneline", "-5", "origin/main", check=False).stdout.rstrip())


def cmd_start(who, task, paths):
    if not paths:
        sys.exit("Укажите хотя бы один файл или папку, которые будете править.")
    sync()
    cl = conflicts(paths, who, load_claims())
    if cl:
        print("ЗАНЯТО. Не начинайте правки в этих файлах, пока коллега не снимет заявку:")
        for c, hit in cl:
            print(f"  {c['who']}: «{c['task']}»  пересечение: {', '.join(p for p, _ in hit)}")
        print("Выберите другие файлы/задачу или напишите коллеге: python3 tools/team.py say ...")
        sys.exit(2)
    os.makedirs(CLAIMS, exist_ok=True)
    n = int(time.time())
    claim = {"who": who, "task": task, "paths": [norm(p) for p in paths], "created": n, "updated": n}
    fn = f"{who}__{n}.json"
    with open(os.path.join(CLAIMS, fn), "w", encoding="utf-8") as fh:
        json.dump(claim, fh, ensure_ascii=False, indent=1)
    push_team(f"team: {who} занял {', '.join(paths)}")
    # гонка: если параллельно записал и другой, выигрывает более ранняя заявка
    sync()
    mine = next(c for c in load_claims() if c["_file"] == fn)
    for c, hit in conflicts(claim["paths"], who, load_claims()):
        if c["created"] < mine["created"]:
            os.remove(os.path.join(CLAIMS, fn))
            push_team(f"team: {who} уступил {c['who']}")
            print(f"Одновременно занял {c['who']} («{c['task']}») и успел раньше. Ваша заявка снята.")
            sys.exit(2)
    print(f"Заявка записана: {fn}. Работайте в своей ветке (feature/...), по окончании: python3 tools/team.py finish {who}")


def cmd_finish(who, num=None):
    sync()
    done = 0
    for c in load_claims():
        if c["who"] == who and (num is None or str(num) in c["_file"]):
            os.remove(os.path.join(CLAIMS, c["_file"]))
            done += 1
    push_team(f"team: {who} освободил файлы")
    print(f"Снято заявок: {done}")


def cmd_say(frm, to, text):
    sync()
    d = os.path.join(INBOX, to)
    os.makedirs(d, exist_ok=True)
    fn = f"{int(time.time())}-{frm}.md"
    with open(os.path.join(d, fn), "w", encoding="utf-8") as fh:
        fh.write(f"От: {frm}\nКогда: {time.strftime('%Y-%m-%d %H:%M')}\n\n{text}\n")
    push_team(f"team: сообщение {frm} → {to}")
    print("Отправлено.")


def cmd_inbox(who):
    sync()
    d = os.path.join(INBOX, who)
    msgs = sorted(f for f in os.listdir(d) if f.endswith(".md")) if os.path.isdir(d) else []
    if not msgs:
        print("Новых сообщений нет.")
        return
    os.makedirs(os.path.join(d, "read"), exist_ok=True)
    for fn in msgs:
        with open(os.path.join(d, fn), encoding="utf-8") as fh:
            print("-----\n" + fh.read())
        os.replace(os.path.join(d, fn), os.path.join(d, "read", fn))
    push_team(f"team: {who} прочитал сообщения")


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
    elif a[0] == "status":
        cmd_status()
    elif a[0] == "start" and len(a) >= 3:
        cmd_start(a[1], a[2], a[3:])
    elif a[0] == "finish" and len(a) >= 2:
        cmd_finish(a[1], a[2] if len(a) > 2 else None)
    elif a[0] == "say" and len(a) >= 4:
        cmd_say(a[1], a[2], " ".join(a[3:]))
    elif a[0] == "inbox" and len(a) >= 2:
        cmd_inbox(a[1])
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
