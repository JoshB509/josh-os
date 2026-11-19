"""Offline release review packets. No credentials, network, uploads or approvals.

This is preparation infrastructure, not a connected publishing adapter.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ReleaseError(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def file_identity(value):
    path = Path(value).expanduser().resolve(strict=True)
    if not path.is_file():
        raise ReleaseError('Choose a regular file.')
    before = path.stat()
    sha = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            sha.update(chunk)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ReleaseError('File changed while preparing the packet.')
    if not after.st_size:
        raise ReleaseError('An upload asset is empty.')
    return dict(path=str(path), size=after.st_size, sha256=sha.hexdigest())


def schedule(local_time, zone_name, utc_offset, now=None):
    """Require an explicit offset to resolve DST folds; reject nonexistent times."""
    try:
        wall = datetime.fromisoformat(local_time)
        zone = ZoneInfo(zone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise ReleaseError('Use an ISO local datetime and an installed IANA timezone.') from None
    if wall.tzinfo is not None or 'T' not in local_time or wall.second or wall.microsecond:
        raise ReleaseError('Use local datetime YYYY-MM-DDTHH:MM without an offset; provide offset separately.')
    valid = {}
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=fold)
        instant = candidate.astimezone(timezone.utc)
        if instant.astimezone(zone).replace(tzinfo=None) == wall:
            valid[candidate.strftime('%z')] = instant
    offset = utc_offset.replace(':', '') if isinstance(utc_offset, str) else ''
    if offset not in valid:
        raise ReleaseError('Time does not exist or the UTC offset does not match this timezone/date.')
    instant = valid[offset]
    if instant <= (now or datetime.now(timezone.utc)):
        raise ReleaseError('Scheduled release must be in the future.')
    return dict(local_time=wall.isoformat(timespec='minutes'), timezone=zone_name,
                utc_offset=offset[:3] + ':' + offset[3:],
                publish_at=instant.isoformat().replace('+00:00', 'Z'))


def prepare(source, now=None):
    for key in ('channel_id', 'title', 'description', 'category_id', 'language'):
        if not isinstance(source.get(key), str) or not source[key].strip():
            raise ReleaseError('Missing ' + key)
    if not source['channel_id'].startswith('UC') or len(source['channel_id']) != 24:
        raise ReleaseError('Use the exact YouTube channel ID, not its name.')
    if len(source['title']) > 100 or any(c in source['title'] for c in '<>'):
        raise ReleaseError('Title must be at most 100 characters and contain no angle brackets.')
    if len(source['description'].encode('utf-8')) > 5000 or any(c in source['description'] for c in '<>'):
        raise ReleaseError('Description must be at most 5000 UTF-8 bytes and contain no angle brackets.')
    if not source['category_id'].isdigit():
        raise ReleaseError('Use a numeric YouTube category ID.')
    for key in ('made_for_kids', 'contains_paid_promotion', 'contains_synthetic_media'):
        if type(source.get(key)) is not bool:
            raise ReleaseError('Josh must explicitly select ' + key)
    variants = source.get('thumbnail_variants')
    if not isinstance(variants, list) or not 1 <= len(variants) <= 3:
        raise ReleaseError('Provide one to three selected thumbnails.')
    assets = [file_identity(p) for p in variants]
    primary = file_identity(source['primary_thumbnail'])
    if primary['sha256'] not in {a['sha256'] for a in assets}:
        raise ReleaseError('Primary thumbnail must be one of the reviewed variants.')
    if len({a['sha256'] for a in assets}) != len(assets):
        raise ReleaseError('Thumbnail variants must be distinct files.')
    video = file_identity(source['video'])
    mode = source.get('mode', 'schedule')
    if mode not in ('unlisted', 'schedule'): raise ReleaseError('Choose unlisted or schedule.')
    plan = schedule(source['local_time'], source['timezone'], source['utc_offset'], now) if mode == 'schedule' else None
    packet = dict(schema=1, state='needs_review', channel_id=source['channel_id'],
                  video=video, title=source['title'], description=source['description'],
                  category_id=source['category_id'], language=source['language'],
                  made_for_kids=source['made_for_kids'],
                  contains_paid_promotion=source['contains_paid_promotion'],
                  contains_synthetic_media=source['contains_synthetic_media'],
                  primary_thumbnail=primary, thumbnail_variants=assets,
                  schedule=plan, initial_privacy='unlisted' if mode == 'unlisted' else 'private', test_type='native_thumbnail_test',
                  capability='preparation_only_not_connected')
    for key in ('project_id', 'project_revision', 'pinned_comment'):
        if key in source: packet[key] = source[key]
    packet['packet_digest'] = digest(packet)
    return packet


def verify(packet, now=None):
    body = {k: v for k, v in packet.items() if k != 'packet_digest'}
    if packet.get('packet_digest') != digest(body):
        raise ReleaseError('Packet changed; prepare and review it again.')
    for asset in [packet['video'], packet['primary_thumbnail'], *packet['thumbnail_variants']]:
        if file_identity(asset['path']) != asset:
            raise ReleaseError('Asset changed; prepare and review it again.')
    value = packet['schedule']
    if packet.get('initial_privacy') == 'unlisted':
        if value is not None: raise ReleaseError('Unlisted upload cannot have a public schedule.')
        return True
    if schedule(value['local_time'], value['timezone'], value['utc_offset'], now) != value:
        raise ReleaseError('Schedule changed; prepare and review it again.')
    return True


def main():
    parser = argparse.ArgumentParser(description='Prepare an offline YouTube release review packet; never publishes.')
    parser.add_argument('input', type=Path, help='Local JSON containing release details')
    parser.add_argument('--output', required=True, type=Path, help='New review JSON file; existing files are not overwritten')
    args = parser.parse_args()
    try:
        packet = prepare(json.loads(args.input.read_text(encoding='utf-8-sig')))
        with args.output.open('x', encoding='utf-8') as target:
            json.dump(packet, target, ensure_ascii=False, indent=2)
        print('Prepared for review only. Nothing was uploaded, scheduled, or approved.')
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
