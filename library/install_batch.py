#!/usr/bin/env python3
"""Installs norns scripts one at a time and keeps only those that pass every check.

Each script is downloaded to a holding area, scanned, compiled together with everything
already installed, then loaded in the real norns while its log and CPU use are watched.
A script that fails stays out of dust/code, with the reason in results.json.
The bridge to Machinista is off during the test, so test sounds don't reach the MK3.

Usage: install_batch.py names.txt
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
STACK = HOME / 'norns-stack'
LIB = STACK / 'library'
RUN = STACK / 'run'
CODE = HOME / 'dust/code'
INCOMING = LIB / 'incoming'
REJECTED = LIB / 'rejected'
RESULTS = LIB / 'results.json'
SURVEY = LIB / 'catalog-survey.json'

BLOCKED = re.compile(r'\b(sudo|apt-get|apt install|systemctl|shutdown|reboot|poweroff|nmcli|passwd|chpasswd|mkfs|dd if=)\b|rm\s+-rf\s+[/~]')
LUA_ERROR = re.compile(r'### SCRIPT ERROR|stack traceback|attempt to |load fail|script load failed|lua:\d+:')
SC_ERROR = re.compile(r'ERROR:|FAILURE IN SERVER|not installed|exception in GraphDef')
COMPILE_ERROR = re.compile(r'ERROR:|Class not defined|Duplicate|duplicate|COMPILE ERROR|Compile failed')
ENV = dict(os.environ, QT_QPA_PLATFORM='offscreen', LD_LIBRARY_PATH=str(STACK / 'prefix/lib'),
           PATH=str(STACK / 'prefix/bin') + ':' + os.environ['PATH'])


def log(*parts):
    print(time.strftime('%H:%M:%S'), *parts, flush=True)


def sh(*command, timeout=120, **options):
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, env=ENV, errors='replace', **options)


# ---- download and scan: nothing from the script runs here

def folder_name(name):
    # norns writes the script's name into Lua source unescaped, so keep it plain.
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip('-')


def download(entry):
    target = INCOMING / folder_name(entry['name'])
    shutil.rmtree(target, ignore_errors=True)
    result = sh('git', 'clone', '-q', '--depth', '1', '--recurse-submodules', '--shallow-submodules',
                entry['url'], str(target), timeout=300)
    if result.returncode:
        return None, 'download failed: ' + result.stderr.strip()[:120]
    return target, None


def scan(target):
    binaries, blocked, hardcoded = [], [], []
    for path in target.rglob('*'):
        if '.git' in path.parts or not path.is_file():
            continue
        relative = str(path.relative_to(target))
        if path.suffix in ('.so', '.scx'):
            binaries.append(relative)
        if path.suffix in ('.lua', '.sc', '.sh', '.py', '.scd'):
            text = path.read_text(errors='replace')
            match = BLOCKED.search(text)
            if match:
                blocked.append(relative + ': ' + match.group(0))
            if '/home/we' in text:
                hardcoded.append(relative)
    if binaries:
        return 'prebuilt binary, 32-bit parts need rebuilding: ' + binaries[0]
    if blocked:
        return 'runs system commands: ' + blocked[0]
    if hardcoded and not Path('/home/we').resolve() == HOME:
        return 'hardcodes /home/we: ' + hardcoded[0]
    return None


# ---- compile: the candidate together with everything already accepted

def compile_setup():
    """A copy of norns' SuperCollider core that doesn't boot a server when it compiles."""
    work = LIB / 'compile-check'
    shutil.rmtree(work, ignore_errors=True)
    shutil.copytree(HOME / 'norns/sc/core', work / 'core')
    crone = work / 'core/Crone.sc'
    text = crone.read_text()
    if 'bootOnInit = true' not in text:
        raise SystemExit('Crone.sc has changed: cannot switch off booting')
    crone.write_text(text.replace('bootOnInit = true', 'bootOnInit = false'))
    return work


def compile_check(work, extra=None):
    paths = [work / 'core', HOME / 'norns/sc/engines', HOME / 'dust']
    if extra:
        paths.append(extra)
    config = work / 'config.yaml'
    config.write_text('includePaths:\n' + ''.join(f'  - "{p}"\n' for p in paths) +
                      'excludePaths: []\npostInlineWarnings: false\nexcludeDefaultPaths: false\n')
    done = work / 'done.scd'
    done.write_text('"COMPILE_CHECK_DONE".postln; 0.exit;\n')
    try:
        result = sh('nice', '-n', '10', 'sclang', '-l', str(config), '-u', '57199', str(done),
                    timeout=120, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return 'SuperCollider compile hung'
    output = result.stdout + result.stderr
    errors = [line for line in output.splitlines() if COMPILE_ERROR.search(line)]
    if errors:
        return 'engine does not compile: ' + errors[0].strip()[:140]
    if 'COMPILE_CHECK_DONE' not in output:
        return 'SuperCollider did not finish compiling'
    return None


# ---- load: in the real norns

class Norns:
    def start(self):
        sh('bash', str(STACK / 'norns-stop.sh'))
        time.sleep(2)
        result = sh('bash', str(STACK / 'norns-start.sh'), 'code/awake/awake.lua', timeout=150)
        sh('pkill', '-x', 'norns_bridged')
        if 'NORNS_READY' not in result.stdout:
            raise SystemExit('norns did not start: ' + result.stdout[-300:])
        time.sleep(3)
        self.send('norns.script.clear()')
        time.sleep(2)

    @staticmethod
    def send(line):
        with open(RUN / 'in', 'w') as fifo:
            fifo.write(line + '\n')

    @staticmethod
    def size(name):
        return (RUN / name).stat().st_size

    @staticmethod
    def since(name, offset):
        with open(RUN / name, errors='replace') as file:
            file.seek(offset)
            return file.read()

    def alive(self):
        return all(sh('pgrep', '-x', name).returncode == 0 for name in ('norns', 'scsynth', 'jackd', 'sclang'))

    def responds(self):
        token = 'PING%d' % time.time_ns()
        offset = self.size('norns.log')
        try:
            self.send('print("%s")' % token)
        except OSError:
            return False
        for _ in range(25):
            time.sleep(0.2)
            if token in self.since('norns.log', offset):
                return True
        return False

    @staticmethod
    def cpu_jiffies():
        total = 0
        for name in ('norns', 'scsynth'):
            for pid in sh('pgrep', '-x', name).stdout.split():
                try:
                    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                    total += int(fields[11]) + int(fields[12])
                except OSError:
                    pass
        return total

    def peak(self):
        output = sh(str(RUN / 'peak'), timeout=20).stdout.strip().splitlines()
        values = re.findall(r'[\d.]+', output[-1]) if output else []
        return max(map(float, values)) if values else 0.0

    def test(self, script):
        """Returns (problem or None, details)."""
        lua, sc = self.size('norns.log'), self.size('sclang.log')
        self.send('norns.script.load("%s")' % script)
        started = time.time()
        init = False
        while time.time() - started < 30:
            time.sleep(0.5)
            if not self.alive():
                return 'norns crashed while loading', {}
            if '# script init' in self.since('norns.log', lua):
                init = True
                break
        details = {'load_seconds': round(time.time() - started, 1)}
        if init:
            time.sleep(3)
            before, at = self.cpu_jiffies(), time.time()
            time.sleep(6)
            details['cpu_of_one_core'] = round((self.cpu_jiffies() - before) / os.sysconf('SC_CLK_TCK') / (time.time() - at) * 100, 1)
            details['peak'] = self.peak()
        output, engine = self.since('norns.log', lua), self.since('sclang.log', sc)
        errors = [line for line in output.splitlines() if LUA_ERROR.search(line)]
        engine_errors = [line for line in engine.splitlines() if SC_ERROR.search(line)]
        if not self.alive():
            return 'norns crashed while running', details
        if not self.responds():
            return 'norns stopped responding', details
        if errors:
            return 'script error: ' + errors[0].strip()[:140], details
        if engine_errors:
            return 'engine error: ' + engine_errors[0].strip()[:140], details
        if not init:
            return 'never finished starting', details
        self.send('norns.script.clear()')
        time.sleep(2)
        return None, details


def scripts_in(target):
    tops = sorted(p.name for p in target.glob('*.lua'))
    main = target.name + '.lua'
    return sorted(tops, key=lambda n: n != main)


def reject(name, target, results, reason, **details):
    results[name] = dict(status='rejected', reason=reason, **details)
    REJECTED.mkdir(exist_ok=True)
    destination = REJECTED / name
    shutil.rmtree(destination, ignore_errors=True)
    if target.exists():
        shutil.move(str(target), destination)
    shutil.rmtree(HOME / 'dust/data' / name, ignore_errors=True)
    log('REJECT', name, reason)


def save(results):
    RESULTS.write_text(json.dumps(results, indent=1, ensure_ascii=False))


def main(names_file):
    catalog = {e['name']: e for e in json.loads(SURVEY.read_text())}
    names = [line.strip() for line in Path(names_file).read_text().splitlines() if line.strip() and not line.startswith('#')]
    results = json.loads(RESULTS.read_text()) if RESULTS.exists() else {}
    INCOMING.mkdir(exist_ok=True)
    work = compile_setup()
    baseline = compile_check(work)
    if baseline:
        raise SystemExit('installed scripts fail the compile check by themselves: ' + baseline)
    log('baseline compile is clean')

    accepted = []
    for name in names:
        if (CODE / folder_name(name)).exists():
            log('skip, already installed:', name)
            continue
        entry = catalog.get(name)
        if entry is None or 'error' in entry:
            results[name] = dict(status='rejected', reason='not in the catalog or gone from GitHub')
            log('REJECT', name, results[name]['reason'])
            continue
        target, problem = download(entry)
        if target is None:
            results[name] = dict(status='rejected', reason=problem)
            log('REJECT', name, problem)
            continue
        problem = scan(target)
        if not problem and not scripts_in(target):
            problem = 'no script at the top level'
        if not problem and any(target.rglob('*.sc')):
            problem = compile_check(work, target)
        if problem:
            reject(name, target, results, problem)
        else:
            shutil.move(str(target), CODE / target.name)
            accepted.append(target.name)
            log('compile ok', name)
        save(results)

    # norns compiles engines when it starts, so one restart covers everything accepted above.
    norns = Norns()
    final = compile_check(work)
    if final:
        raise SystemExit('combined library fails to compile, not restarting norns: ' + final)
    norns.start()
    for name in accepted:
        target = CODE / name
        outcome, details = None, {}
        for script in scripts_in(target):
            outcome, details = norns.test(f'code/{name}/{script}')
            if outcome:
                break
        if outcome:
            restart = bool(re.search('crash|responding', outcome)) or not norns.alive()
            had_engine = any(target.rglob('*.sc'))
            reject(name, target, results, outcome, **details)
            if restart or had_engine:
                norns.start()
            else:
                norns.send('norns.script.clear()')
                time.sleep(2)
        else:
            results[name] = dict(status='installed', **details)
            log('PASS', name, details)
        save(results)

    passed = sum(1 for r in results.values() if r['status'] == 'installed')
    log('finished:', passed, 'installed,', len(results) - passed, 'rejected')


if __name__ == '__main__':
    main(sys.argv[1])
