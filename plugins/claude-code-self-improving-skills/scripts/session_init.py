#!/usr/bin/env python3
"""SessionStart-hook logic for the claude-code-self-improving-skills plugin.

Injects a small amount of additionalContext at session start so the agent:
  1. knows the self-improvement loop is active and how to feed it (the advisory
     nudge — Hermes' SKILLS_GUIDANCE analogue),
  2. is aware of how many learned skills already exist under ~/.claude/skills,
     and — when SIS_MAX_LEARNED_SKILLS is set, from 90% of it on — that new skills are about
     to be, or already are, refused (the user hears this too, once a day,
     through systemMessage), and
  3. is reminded to run /curate-skills when the learned-skill library has grown
     and hasn't been consolidated in a while (the Hermes 7-day curator analogue,
     here event-gated rather than wall-clock).

Output contract: a SessionStart hook adds context by printing
  {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": "..."}}
to stdout. Fails safe to silent (no context) on any error.

Config:
  SIS_TRANSITION_INTERVAL_DAYS  days between the deterministic stale/archive passes (default 1)
  SIS_CURATE_MIN_SKILLS  learned-skill count above which consolidation runs (default 8)
  SIS_CURATE_INTERVAL_DAYS  days between automatic consolidation passes (default 7)
  SIS_CURATE_MAX_JOBS    consolidation cluster jobs queued per pass (default 5)
  SIS_COMPRESS_MAX_JOBS  description-compression batch jobs queued per pass (default 6)
  SIS_AUTO_CURATE        "0" turns the background library passes off
  SIS_MAX_LEARNED_SKILLS the library cap the notice compares against (default 0 = no cap, no notice)

Two clocks since 0.18.0. The time-based transitions are free and deterministic,
so they run daily; the LLM passes (consolidation clusters, compression batches)
are weekly. Through 0.17.0 one seven-day clock gated both, which is why the
transitions ran fifteen times while the consolidation ran four.
"""

import json
import os
import sys
import time
from typing import NoReturn

import runtime_env
import sis_io
import skill_paths

# Pin UTF-8 before the (Korean) SessionStart note is written; see sis_io.
sis_io.pin_utf8_stdio()

SKILLS_DIR = skill_paths.personal_skills_root()
# Resolved through skill_paths so SIS_STATE_DIR moves ALL plugin state; a
# hard-coded default here would leave this file behind in the real home.
STATE_DIR = skill_paths.state_dir()
CURATOR_STATE = os.path.join(STATE_DIR, "curator_state.json")
# Kept out of curator_state.json: the curator seeds that file with a fresh
# dict on first run, which would wipe whatever we stored alongside it.
DISTILL_SEEN = os.path.join(STATE_DIR, "distill_seen.json")
# Same reason: the day the library-cap notice last reached the user.
NOTICES = os.path.join(STATE_DIR, "notices.json")
# Share of the cap at which the notice starts, before creation is refused.
NEAR_CAP = 0.9
PROVENANCE_KEY = "self-improving-skills"  # marker we write into learned SKILL.md frontmatter


def emit_context(text, reload_skills=False, system_message=None) -> NoReturn:
    if text:
        payload = {
            "hookEventName": "SessionStart",
            "additionalContext": text,
        }
        if reload_skills:
            # Skill discovery runs before SessionStart hooks finish, so a skill
            # the background worker installed since the last session would
            # otherwise only become available one session later.
            payload["reloadSkills"] = True
        out = {"hookSpecificOutput": payload}
        if system_message:
            # additionalContext reaches the model only; a state the user has to
            # act on goes out on the channel the user actually sees too.
            out["systemMessage"] = system_message
        sys.stdout.write(json.dumps(out, ensure_ascii=False))
    sys.exit(0)


def _review_mode():
    mode = (os.environ.get("SIS_REVIEW_MODE") or "").strip().lower()
    return mode if mode in ("background", "foreground", "off") else "background"


def _background_note():
    """(note, reload_skills) for the background queue.

    Silence is the default: a job that distilled something, or decided there
    was nothing worth keeping, is not news. Only states a human has to act on
    are surfaced — plus a relaunch when work is waiting and no worker is alive,
    which is how a job survives a machine restart.
    """
    try:
        import distill_queue
        import distill_worker
    except Exception:
        return None, False

    try:
        queue = distill_queue.DistillQueue()
        counts = queue.status()["counts"]
    except Exception:
        return None, False

    seen = _read_seen()
    alerts = []
    reload_skills = False

    try:
        done = queue.list_jobs(status="done", limit=50)
    except Exception:
        done = []
    fresh = [job for job in done if str(job["id"]) not in seen]
    if any((job.get("result") or {}).get("skills") for job in fresh):
        reload_skills = True

    blocked_by_code = {}
    try:
        for job in queue.list_jobs(status="blocked", limit=100):
            blocked_by_code.setdefault(job.get("error_code") or "unknown", 0)
            blocked_by_code[job.get("error_code") or "unknown"] += 1
    except Exception:
        blocked_by_code = {}

    if blocked_by_code.get("authentication_required"):
        alerts.append(
            "증류 작업 {0}건이 CLI 인증을 기다립니다 — `claude setup-token` 후 "
            "{1} 에 CLAUDE_CODE_OAUTH_TOKEN 을 넣고 /distill-status retry 로 "
            "재시도하세요".format(
                blocked_by_code["authentication_required"],
                distill_worker.worker_env_file()))
    if blocked_by_code.get("unprotected_write"):
        alerts.append(
            "증류 작업 {0}건에서 되돌릴 수 없는 쓰기가 감지돼 보류됐습니다 — "
            "/distill-status 로 경로를 확인하세요".format(
                blocked_by_code["unprotected_write"]))
    if blocked_by_code.get("symlinked_skills"):
        # No longer produced: the symlink refusal was removed. Jobs blocked by
        # the old rule are still sitting in queues out there, and they retry
        # cleanly now, so point at that instead of restating a rule that is gone.
        alerts.append(
            "심볼릭 링크 때문에 보류됐던 증류 작업 {0}건이 있습니다 — 이 제한은 "
            "없어졌으니 /distill-status retry 로 재시도하세요".format(
                blocked_by_code["symlinked_skills"]))
    if blocked_by_code.get("out_of_scope_write"):
        # Same story as symlinked_skills: the home-file watchlist is gone.
        alerts.append(
            "홈 파일 감시 때문에 보류됐던 증류 작업 {0}건이 있습니다 — 이 검사는 "
            "없어졌으니 /distill-status retry 로 재시도하세요".format(
                blocked_by_code["out_of_scope_write"]))
    other_blocked = sum(
        count for code, count in blocked_by_code.items()
        if code not in ("authentication_required", "unprotected_write",
                        "symlinked_skills", "out_of_scope_write"))
    if other_blocked:
        alerts.append("보류된 증류 작업 {0}건 (/distill-status)".format(other_blocked))
    if counts.get("failed"):
        alerts.append("실패한 증류 작업 {0}건 (/distill-status)".format(counts["failed"]))

    waiting = int(counts.get("pending") or 0) + int(counts.get("running") or 0)
    if waiting:
        try:
            if not queue.worker_alive():
                launched = distill_worker.launch_detached()
                if not launched.get("launched") and launched.get("reason") == "claude_not_found":
                    alerts.append(
                        "대기 중인 증류 작업 {0}건이 있으나 claude CLI 를 찾지 못했습니다 "
                        "(SIS_CLAUDE_BIN 으로 경로 지정 가능)".format(waiting))
        except Exception:
            pass

    _remember_seen(seen, [str(job["id"]) for job in fresh])
    return ("[claude-code-self-improving-skills] " + ", ".join(alerts) if alerts else None), reload_skills


def _remember_seen(seen, new_ids):
    """Persist which finished jobs have been reported.

    SessionStart fires again on resume and fork, so without this the same
    reload/alert would repeat on every one of them.
    """
    if not new_ids:
        return
    for job_id in new_ids:
        seen[job_id] = True
    if len(seen) > 200:
        seen = {k: True for k in sorted(seen, key=_as_int)[-200:]}
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(DISTILL_SEEN, "w", encoding="utf-8") as fh:
            json.dump(seen, fh)
    except Exception:
        pass


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _read_seen():
    try:
        with open(DISTILL_SEEN, encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _int_env(name, default):
    return skill_paths.int_env(name, default)


def _count_learned_skills():
    """Count learned skills this plugin distilled: DIRECT children of
    ~/.claude/skills with a SKILL.md carrying our provenance marker — the same
    counting rule as curator_transitions._learned_names. (A recursive walk
    would also count SKILL.md copies under support dirs like references/ or
    templates/, inflating the curation trigger — Hermes 9137b86a excludes
    support-dir SKILL.md from discovery for the same reason.)"""
    learned = 0
    if not os.path.isdir(SKILLS_DIR):
        return learned
    try:
        entries = os.listdir(SKILLS_DIR)
    except Exception:
        return learned
    for entry in entries:
        if entry.startswith("."):
            continue
        path = os.path.join(SKILLS_DIR, entry, "SKILL.md")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                head = fh.read(2048)
            if PROVENANCE_KEY in head:
                learned += 1
        except Exception:
            pass
    return learned


def _library_status():
    """(learned, cap) by the rule the guard refuses new skills with, or None.

    `_count_learned_skills` only looks for the marker; the guard also asks the
    usage record who owns the skill. Through 0.18.3 this note printed the
    first number and never compared it with anything, so a library 2.5x over
    the cap read the same as one at half of it."""
    try:
        import skill_similarity
        try:
            import usage_store
            records = usage_store.all_records()
        except Exception:
            records = {}
        inventory = skill_similarity.read_inventory(SKILLS_DIR, records=records)
        return skill_similarity.learned_count(inventory), skill_similarity.library_cap()
    except Exception:
        return None


def _parked_count():
    try:
        import skill_guard
        return len(skill_guard.parked_candidates())
    except Exception:
        return 0


def _cap_notice(learned, cap):
    """(context line, user message) for a library at or near its cap, or
    (None, None). The user message is the same news in fewer words."""
    if cap <= 0 or learned < cap * NEAR_CAP:
        return None, None
    if learned < cap:
        return (
            "학습 스킬이 상한에 가까워졌습니다({0}/{1}, SIS_MAX_LEARNED_SKILLS). 상한에 닿으면 "
            "백그라운드 증류는 새 스킬을 만들지 않고 기존 스킬 patch 와 후보함 보관만 합니다.".format(learned, cap),
            "[self-improving-skills] 학습 스킬 {0}개 — 상한 {1}개에 가까워졌습니다. "
            "/prune-skills 로 오래된 스킬을 미리보고 정리할 수 있습니다.".format(learned, cap),
        )
    parked = _parked_count()
    return (
        "[claude-code-self-improving-skills] 학습 스킬 {0}개가 상한 {1}개(SIS_MAX_LEARNED_SKILLS)를 "
        "넘어 새 스킬 생성이 막혀 있습니다 — 백그라운드 증류는 기존 스킬 patch 만 하고, 새 기법은 "
        "후보함({2})에 보관됩니다(현재 {3}건, /distill-status). 줄이려면 /prune-skills 로 미리보기 후 "
        "정리하거나 /curate-skills 로 통합하고, 상한을 바꾸려면 SIS_MAX_LEARNED_SKILLS 를 조정하세요."
        .format(learned, cap, os.path.join(STATE_DIR, "candidates"), parked),
        "[self-improving-skills] 학습 스킬 {0}개로 상한 {1}개를 넘어 새 스킬이 만들어지지 않고 "
        "후보함에만 쌓입니다(현재 {2}건). /prune-skills 로 정리하거나 SIS_MAX_LEARNED_SKILLS 를 "
        "조정하세요.".format(learned, cap, parked),
    )


def _notify_user_today(key):
    """True at most once per UTC day per key — the user-visible message
    repeats daily while the state lasts, not on every session start."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        with open(NOTICES, encoding="utf-8") as fh:
            notices = json.load(fh)
        if not isinstance(notices, dict):
            notices = {}
    except Exception:
        notices = {}
    if notices.get(key) == today:
        return False
    notices[key] = today
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(NOTICES, "w", encoding="utf-8") as fh:
            json.dump(notices, fh)
    except Exception:
        pass
    return True


def _read_curator_state():
    try:
        with open(CURATOR_STATE, encoding="utf-8") as fh:
            d = json.load(fh)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_curator_state(state):
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        tmp = CURATOR_STATE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CURATOR_STATE)
    except Exception:
        pass


def _curation_status(learned_count):
    """'seed' (first ever — defer), 'due' (run the transitions now), or 'idle'."""
    state = _read_curator_state()
    if "last_run" not in state:
        return "seed"
    if learned_count < _int_env("SIS_CURATE_MIN_SKILLS", 8):
        return "idle"
    interval = _int_env("SIS_TRANSITION_INTERVAL_DAYS", 1) * 86400
    try:
        last = float(state.get("last_run", 0))
    except (TypeError, ValueError):
        last = 0.0
    return "due" if (time.time() - last) >= interval else "idle"


def _consolidation_status(learned_count):
    """Whether the weekly LLM passes (clusters, compression) are due.

    A state file without `last_consolidation` — every install upgraded from
    0.17.0 — is due at once: that is the first pass over a library the old
    clock never got to."""
    state = _read_curator_state()
    if "last_run" not in state:
        return "seed"
    if learned_count < _int_env("SIS_CURATE_MIN_SKILLS", 8):
        return "idle"
    if "last_consolidation" not in state:
        return "due"
    interval = _int_env("SIS_CURATE_INTERVAL_DAYS", 7) * 86400
    try:
        last = float(state.get("last_consolidation", 0))
    except (TypeError, ValueError):
        last = 0.0
    return "due" if (time.time() - last) >= interval else "idle"


def _run_curator(state, lines, over_cap=False):
    """Run the deterministic time-based transition pass inline and report it."""
    summary = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import curator_transitions
        summary = curator_transitions.run(dry_run=False)
    except Exception:
        summary = None
    state["last_run"] = time.time()
    state["run_count"] = int(state.get("run_count", 0)) + 1
    if summary:
        na, ns, nr = len(summary["archived"]), len(summary["stale"]), len(summary["reactivated"])
        state["last_summary"] = {"archived": na, "stale": ns, "reactivated": nr}
        first_seen = summary.get("first_seen") or []
        if first_seen:
            agent = sum(1 for item in first_seen if item.get("created_by") == "agent")
            lines.append(
                "[큐레이터] 사용 기록이 없던 학습 스킬 {0}개(증류 스킬 {1}개)를 오늘부터 추적합니다 — "
                "자동 정리는 오늘을 기준으로 나이를 세므로 당장 옮기는 스킬은 없습니다. 파일 나이로 미리보려면 "
                "/prune-skills 를 쓰세요.".format(len(first_seen), agent)
            )
        if na or ns or nr:
            lines.append(
                "[큐레이터] 미사용 스킬 자동 정리 실행: stale {0}개, 아카이브 {1}개, 재활성화 {2}개. "
                "아카이브된 스킬은 ~/.claude/skills/.archive/ 로 이동(삭제 아님, 하나씩은 /restore-skill, "
                "이번 정리 전체를 되돌리려면 /curator-rollback). "
                "세부 리포트는 ~/.claude/self-improve/logs/curator/.".format(ns, na, nr)
            )
        elif over_cap:
            # "Nothing to clean" next to a full library reads as "all is well"
            # — the 0.18.3 message did exactly that for three weeks.
            lines.append(
                "[큐레이터] 정기 점검 완료 — {0}일 미사용 기준에 걸린 스킬은 없지만, 라이브러리는 상한을 "
                "넘은 상태입니다. 자동 정리만으로는 줄지 않으니 /prune-skills 로 직접 정리하세요."
                .format(summary.get("archive_days", 45))
            )
        else:
            lines.append("[큐레이터] 정기 점검 완료 — 정리할 미사용 스킬이 없습니다.")
    _write_curator_state(state)


def _library_passes():
    """The cluster and batch jobs the library needs right now, computed
    deterministically so the same tree yields the same jobs (and the queue's
    (session_id, prompt_id) key folds repeats into one)."""
    import skill_similarity
    try:
        import usage_store
        records = usage_store.all_records()
    except Exception:
        records = {}
    inventory = skill_similarity.read_inventory(records=records)
    clusters = skill_similarity.clusters(inventory)[: _int_env("SIS_CURATE_MAX_JOBS", 5)]
    clustered = {m for c in clusters for m in c["members"]}
    # Compression waits for consolidation: a description rewritten today would
    # be merged away tomorrow. Members of a pending cluster are left out.
    over = skill_similarity.over_cap(inventory, exclude=clustered)
    batches = skill_similarity.batches(over)[: _int_env("SIS_COMPRESS_MAX_JOBS", 6)]
    return clusters, batches


def _enqueue_curation(lines):
    """Hand the weekly LLM passes to the background worker: one job per
    cluster of similar skills (consolidation) and one per batch of over-cap
    descriptions (compression).

    The time-based transitions are deterministic, so they run inline.
    Deciding that five narrow skills are really one — reading them and writing
    the umbrella — is judgement work that needs a model, and doing it inline
    would block the session start it was triggered from. So it goes on the same
    queue the Stop hook uses. One job per cluster keeps each child's work small
    enough to finish inside its 600-second clock; the 0.17.0 single pass over
    the whole library never did.

    Best-effort by construction: every failure path falls through to the manual
    pointer rather than raising, because a SessionStart hook must never be the
    reason a session doesn't start.
    """
    if (os.environ.get("SIS_AUTO_CURATE") or "").strip() == "0":
        lines.append("[큐레이터] 자동 통합은 꺼져 있습니다(SIS_AUTO_CURATE=0). "
                     "직접 정리하려면 /curate-skills 를 실행하세요.")
        return
    queued_clusters = 0
    queued_batches = 0
    planned = None
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import distill_queue
        import distill_worker

        if distill_worker.discover_claude():
            clusters, batches = _library_passes()
            planned = (len(clusters), len(batches))
            queue = distill_queue.DistillQueue()
            day = time.strftime("%Y%m%d", time.gmtime())
            for cluster in clusters:
                # One job per cluster per UTC day: the queue keys
                # (session_id, prompt_id) uniquely, so parallel session starts
                # on the same day collapse to a single job per cluster.
                result = queue.enqueue(
                    session_id="curator-{0}".format(cluster["id"]),
                    prompt_id="curate-{0}".format(day),
                    transcript_path="", transcript_rows=0, signal=False,
                    signal_source="session_start", trigger=distill_worker.CURATE_TRIGGER,
                    payload={"kind": "cluster", "id": cluster["id"], "members": cluster["members"]},
                )
                queued_clusters += 1 if result.get("enqueued") and not result.get("coalesced") else 0
            for batch in batches:
                result = queue.enqueue(
                    session_id="compress-{0}".format(batch["id"]),
                    prompt_id="compress-{0}".format(day),
                    transcript_path="", transcript_rows=0, signal=False,
                    signal_source="session_start", trigger=distill_worker.COMPRESS_TRIGGER,
                    payload={"kind": "batch", "id": batch["id"], "members": batch["members"]},
                )
                queued_batches += 1 if result.get("enqueued") and not result.get("coalesced") else 0
            if queued_clusters or queued_batches:
                try:
                    distill_worker.launch_detached()
                except Exception:
                    # The jobs are durable; the next SessionStart relaunches them.
                    pass
    except Exception:
        planned = None

    state = _read_curator_state()
    state["last_consolidation"] = time.time()
    _write_curator_state(state)

    if queued_clusters or queued_batches:
        lines.append(
            "[큐레이터] 중복 스킬 통합 잡 {0}건, 설명 압축 잡 {1}건을 백그라운드에 맡겼습니다 — 이 대화는 "
            "그대로 진행하시면 됩니다. 결과는 다음 세션 시작 시 보고되고, 되돌리려면 "
            "/curator-rollback 을 쓰세요.".format(queued_clusters, queued_batches)
        )
    elif planned == (0, 0):
        lines.append("[큐레이터] 통합할 클러스터도 압축할 설명도 없습니다.")
    else:
        lines.append("[큐레이터] 중복 스킬의 의미 기반 통합이 필요하면 /curate-skills 를 실행하세요.")


def main():
    # Inside Cowork the cowork variant announces its own loop; describing the
    # background queue there would promise persistence this session cannot give.
    if runtime_env.is_cowork_runtime():
        emit_context(None)

    try:
        sys.stdin.read()  # drain payload; we don't need its fields
    except Exception:
        pass

    try:
        learned = _count_learned_skills()
    except Exception:
        emit_context(None)

    mode = _review_mode()
    if mode == "off":
        # Nothing is watching this session, so saying the loop is active would
        # be false — and the background child runs in this mode too, where any
        # guidance we inject is pure noise it must ignore.
        emit_context(None)
    background = mode == "background"
    if background:
        lines = [
            "[claude-code-self-improving-skills] 자기개선 루프 활성 (백그라운드 모드). "
            "복잡한 작업이 끝나면 Stop 훅이 별도 프로세스에 증류를 맡깁니다 — "
            "이 대화에는 아무 것도 출력되지 않고 비용도 붙지 않으니, 증류를 위해 "
            "따로 할 일은 없습니다. 지금 당장 남기고 싶으면 /distill-skill 을 쓰세요.",
            "학습 스킬에서 낡거나 틀린 내용을 발견하면 그 자리에서 해당 SKILL.md 를 patch 하세요.",
        ]
    else:
        lines = [
            "[claude-code-self-improving-skills] 자기개선 루프 활성. 복잡한 작업·까다로운 디버깅·새 기법 발견을 "
            "끝냈고 재사용 가능하다면, /distill-skill 또는 Task(또는 Agent) 도구로 "
            "subagent_type=\"claude-code-self-improving-skills:skill-distiller\"(네임스페이스 접두사 생략 시 "
            "호출 실패)를 run_in_background=true 로 호출해 ~/.claude/skills 에 남기세요 — "
            "그냥 종료하면 Stop 훅이 한 번 상기시킵니다.",
            "학습 스킬에서 낡거나 틀린 내용을 발견하면 그 자리에서 해당 SKILL.md 를 patch 하세요. "
            "백그라운드 모드(기본)로 돌리면 이 위임 자체가 필요 없어집니다 — "
            "`claude setup-token` 후 ~/.claude/self-improve/worker.env 에 토큰을 넣으면 됩니다.",
        ]
    library = _library_status()
    over_cap = False
    system_message = None
    if library is not None:
        count, cap = library
        if count:
            lines.append("현재 학습된 스킬 {0}개가 ~/.claude/skills 에 누적되어 있습니다{1}.".format(
                count, " (상한 {0}개)".format(cap) if cap > 0 else ""))
        notice, message = _cap_notice(count, cap)
        if notice:
            lines.append(notice)
            if _notify_user_today("library_cap"):
                system_message = message
        over_cap = count >= cap > 0
    elif learned:
        lines.append("현재 학습된 스킬 {0}개가 ~/.claude/skills 에 누적되어 있습니다.".format(learned))

    reload_skills = False
    if background:
        try:
            note, reload_skills = _background_note()
            if note:
                lines.append(note)
        except Exception:
            pass

    try:
        status = _curation_status(learned)
        if status == "seed":
            # First ever tick: seed both clocks and DEFER (never curate on install).
            now = time.time()
            _write_curator_state({"last_run": now, "run_count": 0, "last_consolidation": now})
        else:
            if status == "due":
                _run_curator(_read_curator_state(), lines, over_cap=over_cap)
            if background and _consolidation_status(learned) == "due":
                # Only in background mode: foreground/off means the user opted
                # out of this plugin spawning child sessions, and a consolidation
                # pass is exactly that.
                _enqueue_curation(lines)
    except Exception:
        pass

    emit_context("\n".join(lines), reload_skills=reload_skills, system_message=system_message)


# NOTE(v0.10.0): the always-on ~470-char permission-recovery hint that used to
# be injected here every session was replaced by one-clause pointers — in the
# intro lines above and in the Stop-hook nudge (where blocks actually happen).
# The full 5-rule recovery recipe lives in README "auto mode" 섹션. The design
# rationale stands: never predict a block from settings (skip flags, runtime
# modes, enterprise settings make that unreliable) — react to a REAL block.


if __name__ == "__main__":
    main()
