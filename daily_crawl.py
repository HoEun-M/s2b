# coding: utf-8
"""서버 PC 매일 자동 수집 (작업 스케줄러가 새벽에 daily_crawl.bat 으로 실행).

1) GitHub에서 최신 누적 파일·원장을 받는다 (다른 PC에서 올린 내용과 충돌하지 않도록 먼저).
2) 나라장터: 최근 7일 중 원장에 없는 날만 받는다 (보통 어제 하루).
3) 학교장터: 최근 7일(어제까지)을 한 구간으로 받는다. 검색어당 요청 수는 하루치와 같고,
   PC가 꺼져 하루 이틀 빠져도 다음 실행 때 같이 채워진다.
4) 둘 다 끝나면 GitHub에 한 번 올린다.

로그: logs/daily_YYYYMMDD_HHMM.log
"""
import os
import subprocess
import sys
from datetime import datetime, timedelta

APP_DIR = os.path.dirname(os.path.abspath(__file__))
WINDOW_DAYS = 7


def main():
    os.chdir(APP_DIR)
    os.makedirs("logs", exist_ok=True)
    now = datetime.now()
    date_to = (now - timedelta(days=1)).strftime("%Y%m%d")
    date_from = (now - timedelta(days=WINDOW_DAYS)).strftime("%Y%m%d")
    log_path = os.path.join("logs", "daily_" + now.strftime("%Y%m%d_%H%M") + ".log")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    with open(log_path, "a", encoding="utf-8") as log:
        def say(text):
            log.write("=== " + datetime.now().strftime("%m-%d %H:%M:%S") + " " + text + "\n")
            log.flush()

        def run(cmd, timeout):
            say("run: " + " ".join(cmd))
            try:
                code = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, env=env, timeout=timeout).returncode
            except subprocess.TimeoutExpired:
                code = -1
            say("exit " + str(code))
            return code

        say("daily crawl " + date_from + "~" + date_to)
        if run(["git", "pull", "--rebase", "--autostash", "origin", "main"], 600) != 0:
            say("git pull 실패: 충돌을 정리한 뒤 다시 실행하세요. 수집하지 않고 끝냅니다.")
            return 1

        g2b = run([sys.executable, "-u", "g2b_api_crawler.py", "--from", date_from, "--to", date_to, "--no-github-upload"], 3 * 3600)
        s2b = run([sys.executable, "-u", "s2b_local_crawler.py", "--from", date_from, "--to", date_to, "--no-github-upload"], 6 * 3600)

        # 한쪽이 실패해도 받은 만큼은 올린다 (원장에 완료로 남은 날만 다음에 건너뛴다)
        sys.path.insert(0, APP_DIR)
        import s2b_local_crawler as local
        say("upload")
        stdout = sys.stdout
        sys.stdout = log
        try:
            uploaded = local.publish_to_github(date_from, date_to, True)
        finally:
            sys.stdout = stdout
        say("done g2b=" + str(g2b) + " s2b=" + str(s2b) + " uploaded=" + str(uploaded))
        return 0 if (g2b == 0 and s2b == 0 and uploaded) else 1


if __name__ == "__main__":
    sys.exit(main())
