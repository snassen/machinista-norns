#!/usr/bin/env python3
"""Writes dust/data/script-library.lua from the community catalog survey.
Usage: make_library.py catalog-survey.json output.lua"""
import json
import re
import sys
import unicodedata

FOLDERS = ['loopers', 'samplers', 'effects', 'sequencers', 'generative', 'instruments', 'utilities', 'other']
# First match wins, so a generative synth counts as generative and a sequencer that also
# generates notes counts as a sequencer.
RULES = [
    ('loopers', {'looper'}),
    ('samplers', {'sampler', 'granulator', 'granular'}),
    ('effects', {'audio_fx', 'effect', 'delay', 'fx', 'reverb'}),
    ('sequencers', {'sequencer', 'tracker'}),
    ('generative', {'generative', 'art'}),
    ('instruments', {'synth', 'drum', 'engine', 'drone'}),
    ('utilities', {'midi', 'utility', 'crow', 'grid', 'arc', 'controller', 'keyboard', 'hid', 'lib'}),
]


def folder(tags):
    tags = set(tags)
    for name, match in RULES:
        if tags & match:
            return name
    return 'other'


def plain(text):
    # The screen font is ASCII, so accents and symbols are dropped from descriptions.
    return ' '.join(unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode().split())


def quoted(text):
    return json.dumps(text, ensure_ascii=False)


def main(source, target):
    lines = ['-- Which folder each script appears in. Generated, but safe to edit by hand.', 'return {',
             '  folders = {' + ', '.join(quoted(f) for f in FOLDERS) + '},', '  scripts = {']
    for entry in sorted(json.load(open(source)), key=lambda e: e['name'].lower()):
        directory = entry['url'].rstrip('/').rsplit('/', 1)[-1]
        info = '{ folder = %s, description = %s }' % (quoted(folder(entry['tags'])), quoted(plain(entry['description'])))
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", entry['name']).strip('-')
        for key in sorted({entry['name'], directory, safe}):
            lines.append('    [%s] = %s,' % (quoted(key), info))
    lines += ['  },', '}', '']
    open(target, 'w').write('\n'.join(lines))


main(sys.argv[1], sys.argv[2])
