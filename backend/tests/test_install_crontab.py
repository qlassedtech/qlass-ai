"""
scripts/install_crontab.sh merge semantics: an existing crontab line for
one of OUR scripts is replaced (not duplicated) when its schedule/flags
change, other apps' lines are untouched, env lines replace by KEY, and
running the merge twice is a no-op. Drives the script with --dry-run
--from FILE so no real crontab is read or written.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "install_crontab.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

FRAGMENT = """\
# comment in the fragment
MAILTO=""
APP=/srv/app

# Nightly backup
30 2 * * *   cd $APP && flock -n /tmp/skoolgpt-backup.lock bash scripts/backup_db.sh >> logs/cron-backup.log 2>&1
0 4 1 * *    cd $APP && flock -n /tmp/skoolgpt-restore-check.lock bash scripts/backup_db.sh --restore-check >> logs/cron-backup.log 2>&1
0 19 * * *   cd $APP && flock -n /tmp/skoolgpt-habit.lock $PY scripts/send_habit_nudges.py >> logs/cron-habit.log 2>&1
"""

EXISTING = """\
MAILTO=root
*/5 * * * * flock -n /tmp/tq-worker.lock /opt/tq/run.sh >> /var/log/tq.log 2>&1
APP=/old/path
0 9 * * * cd $APP && $PY scripts/send_habit_nudges.py >> logs/habit_nudges.log 2>&1
30 2 * * *   cd $APP && bash scripts/backup_db.sh >> logs/cron-backup.log 2>&1
"""


def _merge(tmp_path, existing: str, fragment: str = FRAGMENT) -> str:
    frag = tmp_path / "crontab"
    frag.write_text(fragment)
    cur = tmp_path / "existing"
    cur.write_text(existing)
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run", "--from", str(cur), str(frag)],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _job_lines(text: str, needle: str) -> list[str]:
    return [line for line in text.splitlines() if needle in line and not line.startswith("#")]


def test_changed_schedule_replaces_the_old_line_instead_of_appending(tmp_path):
    merged = _merge(tmp_path, EXISTING)
    habit = _job_lines(merged, "send_habit_nudges.py")
    assert len(habit) == 1
    assert habit[0].startswith("0 19 * * *")
    assert "flock -n /tmp/skoolgpt-habit.lock" in habit[0]


def test_same_script_with_different_flags_is_a_different_job(tmp_path):
    merged = _merge(tmp_path, EXISTING)
    backup = _job_lines(merged, "backup_db.sh")
    assert len(backup) == 2  # plain nightly + --restore-check, old plain line replaced
    assert sum("--restore-check" in line for line in backup) == 1
    assert all("flock" in line for line in backup)


def test_other_apps_lines_and_env_keys_are_preserved_or_replaced_by_key(tmp_path):
    merged = _merge(tmp_path, EXISTING)
    assert "*/5 * * * * flock -n /tmp/tq-worker.lock /opt/tq/run.sh >> /var/log/tq.log 2>&1" in merged
    assert _job_lines(merged, "MAILTO=") == ['MAILTO=""']
    assert _job_lines(merged, "APP=") == ["APP=/srv/app"]


def test_merge_is_idempotent(tmp_path):
    once = _merge(tmp_path, EXISTING)
    twice = _merge(tmp_path, once)
    assert once == twice


def test_empty_existing_crontab_just_installs_the_fragment(tmp_path):
    merged = _merge(tmp_path, "")
    assert len(_job_lines(merged, "scripts/")) == 3
    assert "comment in the fragment" not in merged  # fragment comments are not copied, the marker is
    assert "managed by scripts/install_crontab.sh" in merged
