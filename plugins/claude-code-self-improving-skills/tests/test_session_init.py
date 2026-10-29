"""Unit tests for session_init.py counting rules and background reporting."""

import importlib
import json

from conftest import _run_script

PROV = ("---\nname: {0}\ndescription: d\nmetadata:\n"
        "  provenance: self-improving-skills\n---\nbody\n")


def test_count_excludes_support_dir_skillmd(sandbox):
    import session_init
    importlib.reload(session_init)  # rebind SKILLS_DIR to the sandbox HOME
    sandbox.make_skill("learned-one", PROV.format("learned-one"))
    # a SKILL.md copy inside a support dir must NOT inflate the count
    ref = sandbox.skills / "learned-one" / "references"
    ref.mkdir()
    (ref / "SKILL.md").write_text(PROV.format("copy"), encoding="utf-8")
    # nor a non-provenance (user-authored) skill
    sandbox.make_skill("hand-made")
    assert session_init._count_learned_skills() == 1


def _init(sandbox, env=None):
    run_env = {"SIS_REVIEW_MODE": "background", "SIS_TEST_NO_LAUNCH": "1"}
    run_env.update(env or {})
    out = _run_script(sandbox.home, "session_init.py", {"source": "startup"}, run_env)
    return json.loads(out)["hookSpecificOutput"] if out.strip() else {}


def _init_raw(sandbox, env=None):
    """The whole hook output, systemMessage included."""
    run_env = {"SIS_REVIEW_MODE": "background", "SIS_TEST_NO_LAUNCH": "1"}
    run_env.update(env or {})
    out = _run_script(sandbox.home, "session_init.py", {"source": "startup"}, run_env)
    return json.loads(out) if out.strip() else {}


def _learned(sandbox, count, stem="learned"):
    for i in range(count):
        name = "{0}-{1}".format(stem, i)
        sandbox.make_skill(name, PROV.format(name))


# --- library cap notice -----------------------------------------------------

def test_a_library_over_its_cap_is_announced_to_the_user(sandbox):
    # 0.18.3 printed "247 learned skills" and nothing else while the guard
    # refused every new skill against a cap of 100.
    _learned(sandbox, 3)
    out = _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "3"})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "현재 학습된 스킬 3개가 ~/.claude/skills 에 누적되어 있습니다 (상한 3개)." in context
    assert "상한 3개(SIS_MAX_LEARNED_SKILLS)를 넘어 새 스킬 생성이 막혀 있습니다" in context
    assert "/prune-skills" in context
    assert "상한 3개를 넘어 새 스킬이 만들어지지 않고" in out["systemMessage"]


def test_the_user_hears_about_the_cap_once_a_day_while_the_model_hears_every_time(sandbox):
    _learned(sandbox, 3)
    assert "systemMessage" in _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "3"})
    again = _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "3"})
    assert "systemMessage" not in again
    assert "새 스킬 생성이 막혀 있습니다" in again["hookSpecificOutput"]["additionalContext"]


def test_a_library_near_its_cap_gets_a_warning_before_creation_stops(sandbox):
    _learned(sandbox, 9)
    out = _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "10"})
    assert "상한에 가까워졌습니다(9/10" in out["hookSpecificOutput"]["additionalContext"]
    assert "상한 10개에 가까워졌습니다" in out["systemMessage"]


def test_a_library_well_under_its_cap_says_nothing_about_it(sandbox):
    _learned(sandbox, 2)
    out = _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "10"})
    assert "(상한 10개)" in out["hookSpecificOutput"]["additionalContext"]
    assert "가까워졌습니다" not in out["hookSpecificOutput"]["additionalContext"]
    assert "systemMessage" not in out


def test_the_notice_counts_what_the_guard_counts(sandbox):
    # A marker-stamped skill the user owns is not counted by the guard, so the
    # notice must not count it either.
    _learned(sandbox, 2)
    sandbox.make_skill("hand-stamped", PROV.format("hand-stamped"))
    sandbox.usage_store.seed_if_missing("hand-stamped", created_by="user")
    out = _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "3"})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "현재 학습된 스킬 2개" in context
    assert "막혀 있습니다" not in context


def test_without_a_configured_cap_there_is_no_cap_notice(sandbox, monkeypatch):
    monkeypatch.delenv("SIS_MAX_LEARNED_SKILLS", raising=False)
    _learned(sandbox, 3)
    out = _init_raw(sandbox)
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "현재 학습된 스킬 3개가 ~/.claude/skills 에 누적되어 있습니다." in context
    assert "상한" not in context and "systemMessage" not in out


def test_a_cap_of_zero_means_no_cap_notice(sandbox):
    _learned(sandbox, 3)
    out = _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "0"})
    context = out["hookSpecificOutput"]["additionalContext"]
    assert "현재 학습된 스킬 3개가 ~/.claude/skills 에 누적되어 있습니다." in context
    assert "상한" not in context and "systemMessage" not in out


def test_the_cap_notice_counts_what_is_waiting_in_the_tray(sandbox):
    import skill_guard
    importlib.reload(skill_guard)
    _learned(sandbox, 3)
    skill_guard.park_candidate_note("reference-matched-music-mix", "cap", "p")
    out = _init_raw(sandbox, {"SIS_MAX_LEARNED_SKILLS": "3"})
    assert "(현재 1건, /distill-status)" in out["hookSpecificOutput"]["additionalContext"]
    assert "후보함에만 쌓입니다(현재 1건)" in out["systemMessage"]


def test_an_over_cap_library_is_not_reported_as_having_nothing_to_clean(sandbox, monkeypatch):
    import session_init
    monkeypatch.setenv("SIS_STATE_DIR", str(sandbox.home / ".claude" / "self-improve"))
    importlib.reload(session_init)
    _learned(sandbox, 2)
    lines = []
    session_init._run_curator({}, lines, over_cap=True)
    assert not any("정리할 미사용 스킬이 없습니다" in line for line in lines)
    assert any("상한을 넘은 상태입니다" in line for line in lines)
    lines = []
    session_init._run_curator({}, lines, over_cap=False)
    assert any("정리할 미사용 스킬이 없습니다" in line for line in lines)


def test_the_curator_reports_skills_it_starts_tracking(sandbox, monkeypatch):
    import session_init
    monkeypatch.setenv("SIS_STATE_DIR", str(sandbox.home / ".claude" / "self-improve"))
    importlib.reload(session_init)
    _learned(sandbox, 2)  # no usage record: arrived without any hook seeing them
    lines = []
    session_init._run_curator({}, lines)
    assert any("사용 기록이 없던 학습 스킬 2개" in line and "당장 옮기는 스킬은 없습니다" in line
               for line in lines)
    lines = []
    session_init._run_curator({}, lines)  # already tracked: no repeat
    assert not any("사용 기록이 없던" in line for line in lines)


def _queue(sandbox):
    import distill_queue
    importlib.reload(distill_queue)
    return distill_queue.DistillQueue(
        sandbox.home / ".claude" / "self-improve" / "distill-jobs.sqlite3")


def _job(queue, session="s1", prompt="p1"):
    return queue.enqueue(
        session_id=session, prompt_id=prompt, transcript_path="/tmp/t.jsonl",
        transcript_rows=5, signal=True, signal_source="stop_hook", trigger="signal")


def test_a_quiet_queue_produces_no_alert(sandbox):
    _queue(sandbox)  # create an empty queue
    context = _init(sandbox)["additionalContext"]
    assert "증류 작업" not in context


def test_a_finished_job_is_not_announced(sandbox):
    queue = _queue(sandbox)
    job_id = _job(queue)["job_id"]
    queue.claim_next("w1")
    queue.complete(job_id, "w1", {"status": "nothing_to_save", "skills": [],
                                  "candidates": [], "summary": "-"})
    # Success is not news; only states a human must act on are surfaced.
    assert "증류 작업" not in _init(sandbox)["additionalContext"]


def test_an_auth_blocked_job_tells_the_user_what_to_do(sandbox):
    queue = _queue(sandbox)
    job_id = _job(queue)["job_id"]
    queue.claim_next("w1")
    queue.block(job_id, "w1", code="authentication_required", message="sign in")
    context = _init(sandbox)["additionalContext"]
    assert "setup-token" in context and "worker.env" in context


def test_an_unprotected_write_is_surfaced(sandbox):
    queue = _queue(sandbox)
    job_id = _job(queue)["job_id"]
    queue.claim_next("w1")
    queue.block(job_id, "w1", code="unprotected_write", message="cannot roll back")
    assert "되돌릴 수 없는 쓰기" in _init(sandbox)["additionalContext"]


def test_an_installed_skill_asks_for_a_skill_reload(sandbox):
    queue = _queue(sandbox)
    job_id = _job(queue)["job_id"]
    queue.claim_next("w1")
    queue.complete(job_id, "w1", {
        "status": "changed",
        "skills": [{"name": "fresh", "action": "installed", "path": "/x/SKILL.md"}],
        "candidates": [], "summary": "kept"})
    # Skill discovery runs before SessionStart finishes, so without this the
    # new skill would only be usable one session later.
    assert _init(sandbox).get("reloadSkills") is True


def test_the_same_finished_job_does_not_ask_twice(sandbox):
    queue = _queue(sandbox)
    job_id = _job(queue)["job_id"]
    queue.claim_next("w1")
    queue.complete(job_id, "w1", {
        "status": "changed",
        "skills": [{"name": "fresh", "action": "installed", "path": "/x/SKILL.md"}],
        "candidates": [], "summary": "kept"})
    assert _init(sandbox).get("reloadSkills") is True
    # SessionStart fires again on resume and fork.
    assert _init(sandbox).get("reloadSkills") is not True


def test_foreground_mode_keeps_the_delegation_guidance(sandbox):
    context = _init(sandbox, {"SIS_REVIEW_MODE": "foreground"})["additionalContext"]
    assert "skill-distiller" in context
    assert "run_in_background=true" in context


def test_off_mode_injects_nothing(sandbox):
    # Claiming the loop is active would be false, and the background child runs
    # in this mode too — any guidance there is noise it must ignore.
    assert _init(sandbox, {"SIS_REVIEW_MODE": "off"}) == {}


def test_background_mode_tells_the_agent_it_has_nothing_to_do(sandbox):
    context = _init(sandbox)["additionalContext"]
    assert "백그라운드 모드" in context


# --- background curation ----------------------------------------------------

def _curate(sandbox, monkeypatch, env=None, claude="/fake/claude"):
    """Drive session_init._enqueue_curation against the sandbox; return its lines."""
    import distill_queue
    import distill_worker
    import session_init
    importlib.reload(distill_queue)
    importlib.reload(distill_worker)
    importlib.reload(session_init)
    monkeypatch.setenv("SIS_STATE_DIR", str(sandbox.home / ".claude" / "self-improve"))
    monkeypatch.setenv("SIS_TEST_NO_LAUNCH", "1")
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    # Pin CLI discovery so the test doesn't depend on whether the machine
    # running it happens to have `claude` installed.
    monkeypatch.setattr(distill_worker, "discover_claude", lambda *a, **k: claude)
    lines = []
    session_init._enqueue_curation(lines)
    return lines


def _plant_cluster(sandbox, stem="live-ui"):
    """Two learned skills the similarity module groups into one cluster."""
    sandbox.make_skill(stem + "-probe", PROV.format(stem + "-probe"))
    sandbox.make_skill(stem + "-probing", PROV.format(stem + "-probing"))


def test_the_consolidation_pass_is_queued_as_a_background_job(sandbox, monkeypatch):
    _plant_cluster(sandbox)
    lines = _curate(sandbox, monkeypatch)
    jobs = _queue(sandbox).list_jobs()
    assert len(jobs) == 1
    assert jobs[0]["trigger"] == "curate"
    # It reads the skill library itself, so it carries no transcript.
    assert jobs[0]["transcript_path"] == ""
    assert jobs[0]["payload"]["members"] == ["live-ui-probe", "live-ui-probing"]
    assert jobs[0]["session_id"] == "curator-" + jobs[0]["payload"]["id"]
    assert any("통합 잡 1건" in line for line in lines)


def test_each_cluster_becomes_its_own_consolidation_job(sandbox, monkeypatch):
    _plant_cluster(sandbox, "live-ui")
    _plant_cluster(sandbox, "remote-windows")
    _curate(sandbox, monkeypatch)
    jobs = _queue(sandbox).list_jobs()
    assert sorted(j["payload"]["members"][0] for j in jobs) == ["live-ui-probe", "remote-windows-probe"]
    assert len({j["session_id"] for j in jobs}) == 2


def test_no_cluster_means_no_consolidation_job(sandbox, monkeypatch):
    sandbox.make_skill("alpha-only", PROV.format("alpha-only"))
    sandbox.make_skill("zulu-other", PROV.format("zulu-other"))
    lines = _curate(sandbox, monkeypatch)
    assert _queue(sandbox).list_jobs() == []
    assert any("없습니다" in line for line in lines)


def test_over_cap_skills_are_batched_into_compress_jobs(sandbox, monkeypatch):
    # Three unrelated skills (distinct words, so they do not cluster) whose
    # descriptions are all far over the cap.
    for name in ("alpha-first", "bravo-second", "charlie-third"):
        long_desc = (name.split("-")[0] + " ") * 80
        sandbox.make_skill(name, PROV.format(name).replace("description: d", "description: " + long_desc))
    monkeypatch.setenv("SIS_COMPRESS_MAX_JOBS", "1")
    lines = _curate(sandbox, monkeypatch)
    jobs = _queue(sandbox).list_jobs()
    assert [j["trigger"] for j in jobs] == ["compress"]
    assert jobs[0]["payload"] == {"kind": "batch", "id": jobs[0]["payload"]["id"],
                                  "members": ["alpha-first", "bravo-second", "charlie-third"]}
    assert any("압축 잡 1건" in line for line in lines)


def test_user_authored_and_symlinked_skills_are_not_batched_for_compression(sandbox, monkeypatch, tmp_path):
    # The 0.18.2 compress pass rewrote the descriptions of two user-authored
    # skills that carried the provenance marker, and of a skill symlinked in
    # from ~/.agents — through the link, outside every snapshot.
    import usage_store
    for name in ("alpha-agent", "bravo-user"):
        long_desc = (name.split("-")[0] + " ") * 80
        sandbox.make_skill(name, PROV.format(name).replace("description: d", "description: " + long_desc))
    usage_store.seed_if_missing("bravo-user", created_by="user")
    outside = tmp_path / "elsewhere" / "charlie-linked"
    outside.mkdir(parents=True)
    (outside / "SKILL.md").write_text(
        PROV.format("charlie-linked").replace("description: d", "description: " + "charlie " * 80),
        encoding="utf-8")
    (sandbox.skills / "charlie-linked").symlink_to(outside, target_is_directory=True)
    _curate(sandbox, monkeypatch)
    jobs = _queue(sandbox).list_jobs()
    assert [j["trigger"] for j in jobs] == ["compress"]
    assert jobs[0]["payload"]["members"] == ["alpha-agent"]


def test_cluster_members_are_not_compressed_before_consolidation(sandbox, monkeypatch):
    long_desc = "x" * 400
    for name in ("live-ui-probe", "live-ui-probing"):
        sandbox.make_skill(name, PROV.format(name).replace("description: d", "description: " + long_desc))
    _curate(sandbox, monkeypatch)
    triggers = sorted(j["trigger"] for j in _queue(sandbox).list_jobs())
    assert triggers == ["curate"]  # the cluster job only; compression waits


def test_the_daily_transition_pass_does_not_wait_for_the_weekly_consolidation(sandbox, monkeypatch):
    import session_init
    importlib.reload(session_init)
    monkeypatch.setenv("SIS_STATE_DIR", str(sandbox.home / ".claude" / "self-improve"))
    importlib.reload(session_init)
    import time
    two_days_ago = time.time() - 2 * 86400
    session_init._write_curator_state({"last_run": two_days_ago, "run_count": 3,
                                       "last_consolidation": time.time()})
    assert session_init._curation_status(20) == "due"          # daily clock
    assert session_init._consolidation_status(20) == "idle"    # weekly clock just reset
    # An install upgraded from 0.17.0 has no consolidation stamp yet: due at once.
    session_init._write_curator_state({"last_run": time.time(), "run_count": 3})
    assert session_init._curation_status(20) == "idle"
    assert session_init._consolidation_status(20) == "due"
    assert session_init._consolidation_status(3) == "idle"     # below SIS_CURATE_MIN_SKILLS


def test_curation_can_be_turned_off(sandbox, monkeypatch):
    lines = _curate(sandbox, monkeypatch, {"SIS_AUTO_CURATE": "0"})
    assert _queue(sandbox).list_jobs() == []
    # Turning the automation off must still leave the manual route visible.
    assert any("/curate-skills" in line for line in lines)


def test_two_session_starts_on_one_day_queue_a_single_pass(sandbox, monkeypatch):
    """Parallel session starts must not race several children over the same
    skill tree — the (session_id, prompt_id) key collapses them into one job."""
    _plant_cluster(sandbox)
    _curate(sandbox, monkeypatch)
    _curate(sandbox, monkeypatch)
    assert len(_queue(sandbox).list_jobs()) == 1


def test_without_a_claude_binary_it_falls_back_to_the_manual_pointer(sandbox, monkeypatch):
    lines = _curate(sandbox, monkeypatch, claude=None)
    assert _queue(sandbox).list_jobs() == []
    assert any("/curate-skills" in line for line in lines)
