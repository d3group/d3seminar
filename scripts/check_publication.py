"""Check publishable files and optionally scan Git history and the current checkout for secrets."""

import argparse, json, shutil, subprocess, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GITLEAKS = 'ghcr.io/gitleaks/gitleaks:v8.30.1@sha256:c00b6bd0aeb3071cbcb79009cb16a60dd9e0a7c60e2be9ab65d25e6bc8abbb7f'
SITE_FILES = set('''index.html event.html agenda.html about.html admin.html
app.js home.js calendar.js agenda.js about.js admin.js api.js shared.js styles.css config.json
assets/agenda-paper.svg assets/d3.svg assets/uni-wuerzburg.svg'''.split())
CONFIG_FIELDS = set('''campaign_id title subtitle organizer timezone location site_url api_url backend_url
test_mode required_participants deadline slots events semester duration_days daily_hours'''.split())
EVENT_FIELDS = CONFIG_FIELDS - {'events'} | {'id', 'name', 'kind', 'description'}


def fields(value, allowed):
    if not isinstance(value, dict) or value.keys() - allowed:
        raise ValueError('Unexpected configuration fields.')


def validate_public_config(config):
    fields(config, CONFIG_FIELDS)
    events = config.get('events')
    if not isinstance(events, list) or not events:
        raise ValueError('Expected public events.')
    for event in [config, *events]:
        fields(event, CONFIG_FIELDS if event is config else EVENT_FIELDS)
        if 'slots' in event:
            if not isinstance(event['slots'], list):
                raise ValueError('Expected candidate dates.')
            for slot in event['slots']:
                fields(slot, {'id', 'start', 'end'})
        if 'daily_hours' in event:
            fields(event['daily_hours'], {'start', 'end', 'last_day_end'})


def check_site(site):
    errors, found = [], set()
    if not site.is_dir() or site.is_symlink():
        return ['The site directory is missing or is a symlink.']
    for path in site.rglob('*'):
        name = path.relative_to(site).as_posix()
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            errors.append(f'Unsafe artifact path: {name}')
        elif path.is_dir():
            if name != 'assets':
                errors.append(f'Unexpected artifact directory: {name}')
        elif name not in SITE_FILES:
            errors.append(f'Unexpected artifact file: {name}')
        else:
            found.add(name)
            try:
                content = path.read_text(encoding='utf-8')
                if '\x00' in content:
                    raise ValueError('Binary content.')
                if name == 'config.json':
                    validate_public_config(json.loads(content))
            except (ValueError, OSError):
                errors.append(f'Invalid public content: {name}')
    errors.extend(f'Missing artifact file: {name}' for name in sorted(SITE_FILES - found))
    return errors


def repository_files(root):
    result = subprocess.run(['git', '-C', str(root), 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            check=True, capture_output=True)
    return sorted(set(result.stdout.decode().split('\x00')) - {''})


def check_repository(root):
    names = repository_files(root)
    ignored = subprocess.run(['git', '-C', str(root), 'check-ignore', '--no-index', '-z', '--stdin'],
                             input='\x00'.join(names).encode(), capture_output=True)
    if ignored.returncode not in (0, 1):
        raise RuntimeError('Could not check Git ignore rules.')
    errors = [f'Ignored file is tracked: {name}' for name in ignored.stdout.decode().split('\x00') if name]
    errors.extend(f'Unsafe repository path: {name}' for name in names if (root / name).is_symlink())
    return errors


def scan_secrets(root):
    with tempfile.TemporaryDirectory(prefix='d3-publication-') as directory:
        snapshot = Path(directory)
        names = set(repository_files(root)) | {'site/' + name for name in SITE_FILES}
        for name in names:
            source = root / name
            if not source.is_file():
                continue
            target = snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        command = ['docker', 'run', '--rm', '--network', 'none', '--read-only',
                   '-v', f'{root}:/repo:ro', '-v', f'{snapshot}:/scan:ro',
                   '-e', 'GIT_CONFIG_COUNT=1', '-e', 'GIT_CONFIG_KEY_0=safe.directory', '-e', 'GIT_CONFIG_VALUE_0=/repo', GITLEAKS]
        flags = ['--config=/repo/.gitleaks.toml', '--redact', '--no-banner', '--ignore-gitleaks-allow', '--verbose']
        subprocess.run([*command, 'git', '/repo', '--log-opts=--all', *flags], check=True)
        subprocess.run([*command, 'dir', '/scan', *flags], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--secrets', action='store_true', help='Also run pinned Gitleaks in Docker, without network access')
    args = parser.parse_args()
    errors = check_repository(ROOT) + check_site(ROOT / 'site')
    if errors:
        parser.exit(1, '\n'.join(errors) + '\n')
    if args.secrets:
        scan_secrets(ROOT)
    print('Publication checks passed.' + (' Git history and current publishable files scanned for secrets.' if args.secrets else ''))


if __name__ == '__main__':
    main()
