"""Check the staged source export before publishing. Does not print matched secrets."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
ALLOWED = {'.gitattributes', '.gitignore', 'LICENSE', 'NOTICE', 'README.md', 'cli.py', 'engine.py',
           'config.example.json', 'check_public.py', 'tests/test_cli.py', 'tests/test_engine.py'}
RULES = {
    'personal Windows path': re.compile(r'[A-Za-z]:[/\\](?:Users|Documents and Settings)[/\\][^/\\\s]+', re.I),
    'personal POSIX home': re.compile(r'(?<![~\w])/(?:home|Users)/[A-Za-z0-9_.-]+/'),
    'API token shape': re.compile(r'\b(?:sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})'),
    'private key': re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
}


def main():
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').strip('\0').split('\0')
    if not names or names == ['']:
        raise RuntimeError('Stage source files before running this check')
    issues = []
    for name in names:
        if name not in ALLOWED:
            issues.append((name, 'outside the explicit public source manifest'))
            continue
        payload = subprocess.check_output(['git', 'show', ':' + name], cwd=ROOT)
        try:
            text = payload.decode('utf-8-sig')
        except UnicodeError:
            issues.append((name, 'unexpected binary content'))
            continue
        for label, pattern in RULES.items():
            if pattern.search(text):
                issues.append((name, label))
        for match in re.finditer(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])', text):
            if match[0] not in ('127.0.0.1', '0.0.0.0'):
                issues.append((name, 'non-loopback IPv4 address'))
    if issues:
        for name, label in issues:
            print(name + ': ' + label, file=sys.stderr)
        return 1
    print(f'PASS: {len(names)} staged source files; no runtime files or common credential/personal-path patterns')
    print('Pattern checks are supplementary; review the complete staged diff and commit metadata too.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
